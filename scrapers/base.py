"""Base scraper infrastructure and Listing dataclass."""

from __future__ import annotations

import logging
import random
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Optional

import requests
from curl_cffi import requests as curl_requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

logger = logging.getLogger(__name__)

# Browser versions to impersonate (curl_cffi TLS fingerprints)
_IMPERSONATE_BROWSERS = [
    "chrome124",
    "chrome123",
    "chrome120",
    "safari17_0",
]


def normalize_property_type(value: str | None) -> str:
    """Normalize a raw property-type value to 'house' or 'apartment'."""
    val = (value or "").strip().lower()
    if val in {"house", "huis", "huizen", "home", "woning", "maison", "villa"}:
        return "house"
    return "apartment"


def detect_property_type(*, fallback: str = "apartment", url: str = "", title: str = "") -> str:
    """Infer house vs apartment from URL/title, falling back to the searched type."""
    haystack = f"{url or ''} {title or ''}".lower()
    house_markers = (
        "/house/", "/huis/", "/huizen/",
        "house for", "house in", "huis te huur", "huis te koop",
        "huis in", "woning", "maison", " villa",
    )
    if any(marker in haystack for marker in house_markers):
        return "house"
    return normalize_property_type(fallback)


def normalize_transaction_type(value: str | None) -> str:
    """Normalize a raw transaction-type value to 'buy' or 'rent'."""
    val = (value or "").strip().lower()
    if val in {"buy", "koop", "te-koop", "te koop", "sale", "for-sale", "for sale", "a-vendre", "à vendre", "kopen"}:
        return "buy"
    return "rent"


def detect_transaction_type(
    *,
    fallback: str = "rent",
    url: str = "",
    title: str = "",
    price: int = 0,
) -> str:
    """Infer buy vs rent from URL/title/price, falling back to searched transaction type."""
    haystack = f"{url or ''} {title or ''}".lower()
    buy_markers = (
        "/for-sale", "/te-koop", "/kopen", "/a-vendre",
        "for sale", "te koop", "à vendre", "a vendre", "tekoop", "forsale",
        "koopappartement", "koopwoning",
    )
    rent_markers = (
        "/for-rent", "/te-huur", "/huren", "/a-louer",
        "for rent", "te huur", "à louer", "a louer", "tehuur", "forrent",
        "huurappartement", "huurwoning",
    )
    if any(marker in haystack for marker in buy_markers):
        return "buy"
    if any(marker in haystack for marker in rent_markers):
        return "rent"
    if price and price > 10000:
        return "buy"
    return normalize_transaction_type(fallback)


def fallback_title(
    property_type: str,
    location: str,
    price: int = 0,
    transaction_type: str = "rent",
) -> str:
    """Build a '<Type> in <location>' fallback title."""
    is_house = normalize_property_type(property_type) == "house"
    is_buy = normalize_transaction_type(transaction_type) == "buy" or (price > 10000 if price else False)
    label = "House" if is_house else "Apartment"
    location = (location or "").strip() or "Unknown location"
    if price:
        price_tag = f"€{price}" if (is_house or is_buy) else f"€{price}/mo"
        return f"{label} in {location} — {price_tag}"
    return f"{label} in {location}"


@dataclass
class Listing:
    """Standardized rental listing across all platforms."""

    id: str                           # platform-specific unique ID
    platform: str                     # "immoweb" | "zimmo" | "immoscoop"
    title: str
    price: int                        # price in EUR (rent or buy)
    bedrooms: int
    address: str
    url: str                          # direct link to listing
    description: str                  # full description text (usually Dutch)
    image_urls: list[str] = field(default_factory=list)
    epc_label: Optional[str] = None   # energy label if available
    surface_m2: Optional[int] = None
    posted_date: Optional[str] = None
    property_type: str = "apartment"  # "apartment" | "house"
    transaction_type: str = "rent"    # "rent" | "buy"

    # Scoring fields (populated later)
    text_score: Optional[float] = None
    photo_score: Optional[float] = None
    final_score: Optional[float] = None
    score_reasoning: Optional[str] = None

    @property
    def unique_key(self) -> str:
        """Unique identifier across platforms."""
        return f"{self.platform}_{self.id}"

    def to_dict(self) -> dict:
        """Serialize for JSON storage."""
        return {
            "id": self.id,
            "platform": self.platform,
            "title": self.title,
            "price": self.price,
            "bedrooms": self.bedrooms,
            "address": self.address,
            "url": self.url,
            "description": self.description[:500],  # truncate for storage
            "image_urls": self.image_urls[:5],
            "epc_label": self.epc_label,
            "surface_m2": self.surface_m2,
            "posted_date": self.posted_date,
            "property_type": self.property_type,
            "transaction_type": self.transaction_type,
            "text_score": self.text_score,
            "photo_score": self.photo_score,
            "final_score": self.final_score,
            "score_reasoning": self.score_reasoning,
        }


class BaseScraper(ABC):
    """Base class for all rental listing scrapers."""

    PLATFORM_NAME: str = "base"
    REQUEST_DELAY: float = 1.5  # seconds between requests
    MAX_RETRIES: int = 2

    def __init__(self):
        self._last_request_time: float = 0

    def _rate_limited_get(self, url: str, **kwargs) -> requests.Response:
        """Make a rate-limited GET request using curl_cffi with TLS impersonation.

        curl_cffi mimics a real browser's TLS fingerprint, which is the primary
        signal Cloudflare and CloudFront use to detect bots.
        """
        elapsed = time.time() - self._last_request_time
        delay = self.REQUEST_DELAY + random.uniform(0.5, 2.0)
        if elapsed < delay:
            sleep_time = delay - elapsed
            logger.debug(f"[{self.PLATFORM_NAME}] Rate limiting: sleeping {sleep_time:.1f}s")
            time.sleep(sleep_time)

        logger.info(f"[{self.PLATFORM_NAME}] GET {url}")
        self._last_request_time = time.time()

        browser = random.choice(_IMPERSONATE_BROWSERS)
        headers = {
            "Accept-Language": "nl-BE,nl;q=0.9,en-US;q=0.8,en;q=0.7",
        }
        if "headers" in kwargs:
            headers.update(kwargs.pop("headers"))

        response = curl_requests.get(
            url,
            impersonate=browser,
            timeout=30,
            headers=headers,
            **kwargs,
        )
        response.raise_for_status()
        return response

    def _get_with_fallback(self, url: str, **kwargs) -> requests.Response | None:
        """Fetch a URL with error handling. Returns None on failure."""
        try:
            return self._rate_limited_get(url, **kwargs)
        except Exception as e:
            status = ""
            if hasattr(e, "response") and e.response is not None:
                status = f" (HTTP {e.response.status_code})"
            logger.warning(f"[{self.PLATFORM_NAME}] Failed to fetch{status}: {e}")
            return None

    @abstractmethod
    def scrape(self) -> list[Listing]:
        """Scrape listings from the platform. Returns a list of Listing objects."""
        ...

    def safe_scrape(self) -> list[Listing]:
        """Scrape with error handling — never crashes the pipeline."""
        try:
            listings = self.scrape()
            logger.info(f"[{self.PLATFORM_NAME}] ✅ Found {len(listings)} listings")
            return listings
        except Exception as e:
            logger.error(f"[{self.PLATFORM_NAME}] ❌ Scraping failed: {e}", exc_info=True)
            return []
