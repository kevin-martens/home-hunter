"""Spotto.be scraper for rental and buying apartments/houses."""

from __future__ import annotations

import logging
import re

from bs4 import BeautifulSoup

from scrapers.base import BaseScraper, Listing, detect_property_type, fallback_title, normalize_property_type
from config import TARGET_LOCATIONS, PROPERTY_TYPES, TRANSACTION_TYPES, MIN_PRICE, MAX_PRICE, MIN_BUY_PRICE, MAX_BUY_PRICE, MIN_BEDROOMS

logger = logging.getLogger(__name__)

class SpottoScraper(BaseScraper):
    """Scraper for Spotto.be listings."""

    PLATFORM_NAME = "spotto"
    REQUEST_DELAY = 2.0
    MAX_PAGES = 3

    def scrape(self) -> list[Listing]:
        listings = []

        for city, postal_code in TARGET_LOCATIONS:
            for prop_type in PROPERTY_TYPES:
                for trans_type in TRANSACTION_TYPES:
                    
                    self.current_min_price = MIN_PRICE if trans_type == "rent" else MIN_BUY_PRICE
                    self.current_max_price = MAX_PRICE if trans_type == "rent" else MAX_BUY_PRICE
                    self.current_postal_code = postal_code
                    self.current_city = city
                    self.current_property_type = normalize_property_type(prop_type)
                    
                    spotto_prop_type = "appartement" if prop_type == "apartment" else "huis"
                    spotto_trans_type = "te-huur" if trans_type == "rent" else "te-koop"
                    
                    # Base search URL
                    # e.g. https://www.spotto.be/nl/te-huur/9000-gent/appartement/overzicht?minPrice=800&maxPrice=1100
                    search_url = f"https://www.spotto.be/nl/{spotto_trans_type}/{postal_code}-{city.lower()}/{spotto_prop_type}/overzicht?minPrice={self.current_min_price}&maxPrice={self.current_max_price}"
                    
                    for page in range(1, self.MAX_PAGES + 1):
                        paged_url = f"{search_url}&page={page}" if page > 1 else search_url
                        page_listings = self._scrape_search_page(paged_url)
                        
                        if not page_listings:
                            logger.info(f"[{self.PLATFORM_NAME}] No more results on page {page} for {city} {prop_type} {trans_type}")
                            break

                        listings.extend(page_listings)
                        logger.info(f"[{self.PLATFORM_NAME}] Page {page} ({city} {prop_type} {trans_type}): {len(page_listings)} listings")

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
            
        for card in cards:
            listing = self._parse_html_card(card)
            if listing:
                listings.append(listing)

        return listings

    def _parse_html_card(self, card: BeautifulSoup) -> Listing | None:
        try:
            href = card.get("href", "")
            if not href:
                return None
                
            match = re.search(r'/([A-Za-z0-9_-]{20,25})$', href)
            listing_id = match.group(1) if match else href.split("/")[-1]
            if not listing_id:
                return None

            full_url = f"https://www.spotto.be{href}" if not href.startswith("http") else href
            
            wrapper = card.find_parent("div", class_=re.compile("card-result-wrapper"))
            if not wrapper:
                wrapper = card
                
            text = wrapper.get_text(separator=' | ', strip=True)
            
            # Extract price (e.g. € 695)
            price = 0
            price_match = re.search(r'€\s*([\d\.]+)', text)
            if price_match:
                price = int(price_match.group(1).replace('.', ''))
                
            if not (self.current_min_price <= price <= self.current_max_price):
                return None

            address = f"{self.current_postal_code} {self.current_city.capitalize()}"
            # Extract address from something like "Posteernestraat 20/B - 9000 Gent"
            address_match = re.search(rf'([\w\s/-]+ - {self.current_postal_code}\s+\w+)', text, re.IGNORECASE)
            if address_match:
                address = address_match.group(1).strip()

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

            title = card.get("title", "")
            if not title:
                img_alt = wrapper.find("img", alt=True)
                title = img_alt["alt"] if img_alt else ""

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
            )
        except Exception as e:
            logger.debug(f"[{self.PLATFORM_NAME}] Failed to parse card: {e}")
            return None
