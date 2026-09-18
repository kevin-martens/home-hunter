"""Spotto.be scraper for rental and buying apartments/houses."""

from __future__ import annotations

import logging
import re

from bs4 import BeautifulSoup

from scrapers.base import (
    BaseScraper,
    Listing,
    detect_property_type,
    detect_transaction_type,
    fallback_title,
    normalize_property_type,
    normalize_transaction_type,
)
from config import TARGET_LOCATIONS, PROPERTY_TYPES, TRANSACTION_TYPES, MIN_PRICE, MAX_PRICE, MIN_BUY_PRICE, MAX_BUY_PRICE, MIN_BEDROOMS

logger = logging.getLogger(__name__)

class SpottoScraper(BaseScraper):
    """Scraper for Spotto.be listings."""

    PLATFORM_NAME = "spotto"
    REQUEST_DELAY = 2.0
    MAX_PAGES = 3

    NON_RESIDENTIAL_KEYWORDS = (
        "grond", "bouwgrond", "perceel", "projectgrond",
        "parkeerplaats", "staanplaats", "garage", "autostaanplaats",
        "garagebox", "box", "loods", "magazijn", "opslagruimte",
        "kantoor", "handelspand", "handelsgelijkvloers", "bedrijfsruimte",
    )

    def scrape(self) -> list[Listing]:
        listings = []
        seen_listing_ids = set()

        for city, postal_code in TARGET_LOCATIONS:
            for prop_type in PROPERTY_TYPES:
                for trans_type in TRANSACTION_TYPES:
                    
                    self.current_min_price = MIN_PRICE if trans_type == "rent" else MIN_BUY_PRICE
                    self.current_max_price = MAX_PRICE if trans_type == "rent" else MAX_BUY_PRICE
                    self.current_postal_code = postal_code
                    self.current_city = city
                    self.current_property_type = normalize_property_type(prop_type)
                    self.current_transaction_type = normalize_transaction_type(trans_type)
                    
                    spotto_prop_type = "appartement" if prop_type == "apartment" else "huis"
                    spotto_trans_type = "te-huur" if trans_type == "rent" else "te-koop"
                    
                    search_url = f"https://www.spotto.be/nl/{spotto_trans_type}/{postal_code}-{city.lower()}/{spotto_prop_type}/overzicht?minPrice={self.current_min_price}&maxPrice={self.current_max_price}"
                    
                    for page in range(1, self.MAX_PAGES + 1):
                        paged_url = f"{search_url}&page={page}" if page > 1 else search_url
                        page_listings = self._scrape_search_page(paged_url)

                        new_page_listings = [
                            l for l in page_listings if l.id not in seen_listing_ids
                        ]
                        if not new_page_listings:
                            logger.info(f"[{self.PLATFORM_NAME}] No more results on page {page} for {city} {prop_type} {trans_type}")
                            break

                        for l in new_page_listings:
                            seen_listing_ids.add(l.id)

                        listings.extend(new_page_listings)
                        logger.info(f"[{self.PLATFORM_NAME}] Page {page} ({city} {prop_type} {trans_type}): {len(new_page_listings)} new listings")

        return listings

    def _scrape_search_page(self, url: str) -> list[Listing]:
        response = self._get_with_fallback(url)
        if not response:
            return []

        soup = BeautifulSoup(response.text, "lxml")
        listings = []

        # Find property cards
        cards = soup.select("a.property-card")
        if not cards:
            cards = soup.select("a[href^='/nl/p/']")

        seen_card_ids = set()
        for card in cards:
            listing = self._parse_html_card(card)
            if listing and listing.id not in seen_card_ids:
                seen_card_ids.add(listing.id)
                listings.append(listing)

        return listings

    def _is_non_residential(self, text: str, href: str, title: str) -> bool:
        """Filter out land, garages, commercial spaces, and parking spots."""
        combined = f"{text} {href} {title}".lower()
        return any(k in combined for k in self.NON_RESIDENTIAL_KEYWORDS)

    def _parse_html_card(self, card: BeautifulSoup) -> Listing | None:
        try:
            href = card.get("href", "")
            if not href:
                return None

            clean_href = href.split("?")[0].rstrip("/")
            match = re.search(r'/([A-Za-z0-9_-]{20,25})$', clean_href)
            listing_id = match.group(1) if match else clean_href.split("/")[-1]
            if not listing_id:
                return None

            full_url = f"https://www.spotto.be{clean_href}" if not clean_href.startswith("http") else clean_href

            wrapper = card.find_parent("div", class_=re.compile("card-result-wrapper"))
            if not wrapper:
                wrapper = card
                
            text = wrapper.get_text(separator=' | ', strip=True)

            title = card.get("title", "")
            if not title:
                img_alt = wrapper.find("img", alt=True)
                title = img_alt["alt"] if img_alt else ""

            # Check if this is land, garage, parking, or commercial
            if self._is_non_residential(text, clean_href, title):
                logger.debug(f"[{self.PLATFORM_NAME}] Skipping non-residential listing: {title or clean_href}")
                return None
            
            # Extract price (e.g. € 695 or € 345.000)
            price = 0
            price_match = re.search(r'€\s*([\d\.]+)', text)
            if price_match:
                price = int(price_match.group(1).replace('.', ''))
                
            if not (self.current_min_price <= price <= self.current_max_price):
                return None

            # Extract genuine address from card text if present
            address = f"{self.current_postal_code} {self.current_city.capitalize()}"
            # Match formats like "Posteernestraat 20/B - 9000 Gent" or "Klaproosstraat 8 - 9860 Oosterzele"
            address_match = re.search(r'([\w\s./-]+ - \d{4}\s+[A-Za-z\s-]+)', text)
            if address_match:
                address = address_match.group(1).strip()
            else:
                # Fallback to general postal code + municipality in card
                pc_match = re.search(r'\b(\d{4})\s+([A-Za-z\s-]+)', text)
                if pc_match:
                    address = f"{pc_match.group(1)} {pc_match.group(2).strip()}"

            bedrooms = 0
            if "kamer" in text.lower() or "slaapkamer" in text.lower():
                bed_match = re.search(r'(\d+)\s*(?:slaap)?kamer', text, re.IGNORECASE)
                if bed_match:
                    bedrooms = int(bed_match.group(1))
                    
            if bedrooms > 0 and bedrooms < MIN_BEDROOMS:
                return None

            images = []
            img_tag = wrapper.find("img")
            if img_tag:
                img_url = img_tag.get("src") or img_tag.get("data-src")
                if img_url:
                    images.append(img_url)

            surface = None
            surface_match = re.search(r'\|\s*(\d+)(?:\s*m²)?\s*$', text, re.IGNORECASE)
            if surface_match:
                surface = int(surface_match.group(1))

            property_type = detect_property_type(
                fallback=getattr(self, "current_property_type", "apartment"),
                url=full_url,
                title=title,
            )
            if not title:
                title = fallback_title(property_type, address).split(" — ")[0]

            return Listing(
                id=listing_id,
                platform=self.PLATFORM_NAME,
                title=title,
                price=price,
                bedrooms=bedrooms,
                address=address,
                url=full_url,
                description="",
                image_urls=images,
                surface_m2=surface,
                property_type=property_type,
                transaction_type=detect_transaction_type(
                    fallback=getattr(self, "current_transaction_type", "rent"),
                    url=full_url,
                    title=title,
                    price=price,
                ),
            )
        except Exception as e:
            logger.debug(f"[{self.PLATFORM_NAME}] Failed to parse card: {e}")
            return None

    def enrich_listing(self, listing: Listing) -> Listing:
        """Fetch detail page to enrich description, EPC label, and images."""
        if not listing.url:
            return listing

        response = self._get_with_fallback(listing.url)
        if not response:
            return listing

        try:
            soup = BeautifulSoup(response.text, "lxml")

            # 1. Description
            if not listing.description or len(listing.description) < 40:
                desc_el = soup.select_one(
                    "[class*='description'], #description, [data-testid='description'], article.description, .property__description"
                )
                if desc_el:
                    desc_text = desc_el.get_text(separator="\n", strip=True)
                    if len(desc_text) > 30:
                        listing.description = desc_text

            # 2. EPC label
            if not listing.epc_label:
                epc_match = re.search(r'\bEPC\s*(?:label|score)?\s*:?\s*([A-F]\+?)\b', response.text, re.IGNORECASE)
                if epc_match:
                    listing.epc_label = epc_match.group(1).upper()

            # 3. Surface area
            if not listing.surface_m2:
                surf_match = re.search(r'(\d+)\s*(?:m²|m2|vierkante meter)', response.text, re.IGNORECASE)
                if surf_match:
                    try:
                        surf_val = int(surf_match.group(1))
                        if 20 <= surf_val <= 1500:
                            listing.surface_m2 = surf_val
                    except ValueError:
                        pass

            # 4. Bedrooms
            if not listing.bedrooms or listing.bedrooms == 0:
                bed_match = re.search(r'(\d+)\s*(?:slaap)?kamer', response.text, re.IGNORECASE)
                if bed_match:
                    try:
                        listing.bedrooms = int(bed_match.group(1))
                    except ValueError:
                        pass

            # 5. Additional Images
            if len(listing.image_urls) <= 1:
                img_tags = soup.select("img[src*='spotto'], img[data-src*='spotto'], .gallery img, .carousel img")
                for tag in img_tags:
                    src = tag.get("src") or tag.get("data-src")
                    if src and src.startswith("http") and src not in listing.image_urls:
                        listing.image_urls.append(src)
                        if len(listing.image_urls) >= 5:
                            break

        except Exception as e:
            logger.debug(f"[{self.PLATFORM_NAME}] Enrichment failed for {listing.id}: {e}")

        return listing
