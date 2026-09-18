"""AI text scoring using Groq API for 'modern & clean' vibes."""

from __future__ import annotations

import json
import logging
import os
import re
import time

from scrapers.base import Listing

logger = logging.getLogger(__name__)

# Default score when AI scoring is unavailable
DEFAULT_SCORE = 5.0
DEFAULT_REASONING = "AI scoring unavailable — unranked"


class TextScorer:
    """Score listing descriptions for modern/clean vibes using Groq API."""

    MODEL = os.environ.get("GROQ_MODEL", "openai/gpt-oss-120b")
    MAX_RETRIES = 2
    RETRY_DELAY = 5  # seconds

    SYSTEM_PROMPT = """You are a real estate quality analyzer specializing in Belgian residential properties (apartments and houses, both rental and purchase). 
You evaluate listings for a young professional working in IT looking for contemporary, comfortable, and well-maintained living spaces.

Scoring philosophy:
- Focus on overall quality, modern comfort, and energy efficiency.
- An EPC label of A or B is a major positive indicator (modern insulation, energy efficiency). An EPC A or B home in turnkey, clean, or well-maintained condition with good space/kitchen/garden should easily score 8.0 - 9.5, even if a heat pump or the word "renovated" is not explicitly mentioned.
- Do NOT penalize a property just because the word "gerenoveerd" is missing; many homes are newer builds, modern, or inherently well-kept without needing renovation.
- Features like a garage/parking spot, garden, terrace, air conditioning, solar panels, and bicycle storage are strong positive bonuses.
- Edge of city / residential / suburban area is preferred over noisy city center.

Scoring scale (1-10):
- 9.0-10.0: Exceptional, new build or high-end modern finish, EPC A, premium kitchen/bath, great outdoor space/garage.
- 8.0-8.9: Very good, turnkey/contemporary, EPC A or B, well-maintained, clean finishes, great comfort.
- 7.0-7.9: Good solid home, mostly modern, pleasant living space, decent energy rating, minor updates optional.
- 5.0-6.9: Average, older style with some updates or dated finishes, habitable but not modern.
- 3.0-4.9: Outdated, needs modernization (op te frissen/te renoveren), basic finishes, poor EPC (E/F).
- 1.0-2.9: Heavy renovation needed, dilapidated, or non-residential (land, parking, storage).

Key positive indicators (Dutch/Flemish):
- "nieuwbouw", "gerenoveerd", "hedendaags", "modern afgewerkt", "instapklaar", "kwalitatief"
- "nieuwe keuken", "inbouwtoestellen", "zonnepanelen", "warmtepomp", "airco", "tuin", "garage", "carport"
- Excellent EPC (A+, A, B)

Key negative indicators:
- "op te frissen", "te renoveren", "volledig te renoveren", "af te breken", "verouderd", "basiscomfort"
- Poor EPC (E, F)

You MUST respond with valid raw JSON only. No markdown formatting, no explanations outside the JSON."""

    USER_PROMPT_TEMPLATE = """Score this property listing for modern comfort and living quality:

Property Type: {property_type}
Transaction: {transaction_type}
Title: {title}
Price: {price_formatted}
Address: {address}
Surface: {surface}m²
EPC: {epc}
Bedrooms: {bedrooms}

Description:
{description}

Respond with this exact JSON format:
{{"modern_score": <number 1-10>, "reasoning": "<brief 1-2 sentence explanation>"}}"""

    def __init__(self):
        self.api_key = os.environ.get("GROQ_API_KEY", "")
        self.client = None
        self.rate_limited = False

        if self.api_key:
            try:
                from groq import Groq
                self.client = Groq(api_key=self.api_key)
                logger.info(f"✅ Groq client initialized (model: {self.MODEL})")
            except ImportError:
                logger.warning("⚠️ groq package not installed, text scoring disabled")
            except Exception as e:
                logger.warning(f"⚠️ Failed to initialize Groq client: {e}")
        else:
            logger.warning("⚠️ GROQ_API_KEY not set, text scoring disabled")

    @staticmethod
    def _extract_json(text: str) -> dict | None:
        """Extract and parse JSON from a response that might contain markdown or extra text."""
        if not text:
            return None

        # 1. Try direct parse
        try:
            return json.loads(text.strip())
        except json.JSONDecodeError:
            pass

        # 2. Try stripping markdown code fences
        cleaned = text.strip()
        if "```" in cleaned:
            match = re.search(r'```(?:json)?\s*([\s\S]*?)\s*```', cleaned)
            if match:
                try:
                    return json.loads(match.group(1).strip())
                except json.JSONDecodeError:
                    pass

        # 3. Try regex search for modern_score JSON object
        match = re.search(r'\{[^{}]*"modern_score"[^{}]*\}', cleaned, re.DOTALL)
        if match:
            try:
                return json.loads(match.group(0))
            except json.JSONDecodeError:
                pass

        return None

    @property
    def is_available(self) -> bool:
        """Check if the scorer is ready to use."""
        return self.client is not None and not self.rate_limited

    def score_listing(self, listing: Listing) -> tuple[float, str]:
        """
        Score a single listing's description for modern/clean vibes.

        Returns:
            Tuple of (score: float 1-10, reasoning: str)
        """
        if not self.is_available:
            return DEFAULT_SCORE, DEFAULT_REASONING

        description = (listing.description or "").strip()
        if len(description) < 20:
            description = "Limited description available. Infer only from title, EPC, surface, bedrooms, and any location clues."

        is_buy = getattr(listing, "transaction_type", "rent") == "buy" or (listing.price and listing.price > 10000)
        price_formatted = f"€{listing.price:,}".replace(",", ".") if is_buy else f"€{listing.price}/month"

        prompt = self.USER_PROMPT_TEMPLATE.format(
            property_type=getattr(listing, "property_type", "apartment") or "property",
            transaction_type="Buy" if is_buy else "Rent",
            title=listing.title,
            price_formatted=price_formatted,
            address=listing.address,
            surface=listing.surface_m2 or "unknown",
            epc=listing.epc_label or "unknown",
            bedrooms=listing.bedrooms,
            description=description[:2000],
        )

        for attempt in range(self.MAX_RETRIES + 1):
            try:
                # Use response_format for native JSON output when supported
                create_kwargs = {
                    "model": self.MODEL,
                    "messages": [
                        {"role": "system", "content": self.SYSTEM_PROMPT},
                        {"role": "user", "content": prompt},
                    ],
                    "temperature": 0.2,
                    "max_tokens": 500,
                }
                try:
                    create_kwargs["response_format"] = {"type": "json_object"}
                    response = self.client.chat.completions.create(**create_kwargs)
                except Exception as rf_err:
                    # Fallback without response_format if model or endpoint rejects it
                    logger.debug(f"[text_scorer] JSON mode fallback without response_format: {rf_err}")
                    create_kwargs.pop("response_format", None)
                    response = self.client.chat.completions.create(**create_kwargs)

                content = response.choices[0].message.content or ""
                result = self._extract_json(content)

                if not result:
                    logger.warning(f"[text_scorer] Could not parse JSON from: {content[:120]}")
                    if attempt < self.MAX_RETRIES:
                        time.sleep(self.RETRY_DELAY)
                        continue
                    return DEFAULT_SCORE, "Failed to parse AI response"

                score = float(result.get("modern_score", DEFAULT_SCORE))
                score = max(1.0, min(10.0, score))  # Clamp to 1-10
                reasoning = result.get("reasoning", "No reasoning provided")

                logger.debug(
                    f"[text_scorer] {listing.platform}:{listing.id} → "
                    f"score={score}, reason={reasoning[:80]}"
                )
                return score, reasoning

            except Exception as e:
                error_str = str(e).lower()
                is_rate_limit = any(
                    k in error_str
                    for k in (
                        "rate_limit",
                        "rate limit",
                        "429",
                        "too many requests",
                        "quota",
                        "tpd",
                        "rpd",
                    )
                )
                if is_rate_limit:
                    logger.warning(
                        f"[text_scorer] Rate limited (attempt {attempt + 1}/"
                        f"{self.MAX_RETRIES + 1})"
                    )
                    # If daily quota / limit reached, stop immediately without further retries
                    if "daily" in error_str or "tpd" in error_str or "rpd" in error_str or "tokens per day" in error_str:
                        logger.warning("[text_scorer] Daily Groq quota exhausted.")
                        self.rate_limited = True
                        return DEFAULT_SCORE, "Rate limited (daily quota exhausted) — unranked"

                    if attempt < self.MAX_RETRIES:
                        time.sleep(self.RETRY_DELAY * (attempt + 1))
                        continue

                    # Exhausted retries due to rate limit — stop attempting for subsequent listings
                    self.rate_limited = True
                    return DEFAULT_SCORE, "Rate limited — unranked"
                else:
                    logger.error(f"[text_scorer] Scoring failed: {e}")
                    return DEFAULT_SCORE, f"Scoring error: {str(e)[:100]}"

        return DEFAULT_SCORE, DEFAULT_REASONING

    def score_listings(self, listings: list[Listing]) -> list[Listing]:
        """Score multiple listings, updating them in place."""
        if not self.is_available:
            logger.warning("[text_scorer] Scorer not available, skipping all text scoring")
            for listing in listings:
                listing.text_score = DEFAULT_SCORE
                listing.score_reasoning = DEFAULT_REASONING
            return listings

        logger.info(f"[text_scorer] Scoring {len(listings)} listings...")

        for i, listing in enumerate(listings):
            if self.rate_limited:
                remaining_count = len(listings) - i
                logger.warning(
                    f"[text_scorer] Rate limit reached. Stopping Groq API calls for remaining "
                    f"{remaining_count} listings and defaulting to {DEFAULT_SCORE:.1f}/10."
                )
                for rem_listing in listings[i:]:
                    rem_listing.text_score = DEFAULT_SCORE
                    rem_listing.score_reasoning = DEFAULT_REASONING
                break

            score, reasoning = self.score_listing(listing)
            listing.text_score = score
            listing.score_reasoning = reasoning

            logger.info(
                f"[text_scorer] ({i + 1}/{len(listings)}) "
                f"{listing.platform}:{listing.id} → {score:.1f}/10"
            )

            if self.rate_limited:
                remaining_count = len(listings) - (i + 1)
                if remaining_count > 0:
                    logger.warning(
                        f"[text_scorer] Rate limit reached. Stopping Groq API calls for remaining "
                        f"{remaining_count} listings and defaulting to {DEFAULT_SCORE:.1f}/10."
                    )
                    for rem_listing in listings[i + 1:]:
                        rem_listing.text_score = DEFAULT_SCORE
                        rem_listing.score_reasoning = DEFAULT_REASONING
                break

            # Small delay between API calls to avoid rate limiting
            if i < len(listings) - 1:
                time.sleep(1)

        return listings