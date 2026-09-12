"""Persistence helpers for deduplication and weekly summaries."""

from __future__ import annotations

import json
import logging
import re
import unicodedata
from datetime import date, datetime
from pathlib import Path

from scrapers.base import Listing, detect_transaction_type

logger = logging.getLogger(__name__)

HISTORY_VERSION = 1


def load_seen_ids(path: Path) -> set[str]:
    """Load the legacy seen-listings file."""
    if not path.exists():
        return set()

    try:
        with path.open("r", encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning("Failed to load legacy seen listings: %s", exc)
        return set()

    if isinstance(data, list):
        return {str(item) for item in data}

    return set()


def save_seen_ids(path: Path, seen_ids: set[str]) -> None:
    """Write the legacy seen-listings file for backward compatibility."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        json.dump(sorted(seen_ids), handle, indent=2)


def load_history(path: Path) -> dict:
    """Load listing history and weekly-report metadata."""
    default_history = {
        "version": HISTORY_VERSION,
        "records": {},
        "weekly_reports_sent": [],
    }

    if not path.exists():
        return default_history

    try:
        with path.open("r", encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning("Failed to load history file: %s", exc)
        return default_history

    if not isinstance(data, dict):
        return default_history

    records = data.get("records", {})
    weekly_reports_sent = data.get("weekly_reports_sent", [])

    return {
        "version": HISTORY_VERSION,
        "records": records if isinstance(records, dict) else {},
        "weekly_reports_sent": list(weekly_reports_sent) if isinstance(weekly_reports_sent, list) else [],
    }


def save_history(path: Path, history: dict) -> None:
    """Persist listing history."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        json.dump(history, handle, indent=2, ensure_ascii=False)


def normalize_text(value: str) -> str:
    """Normalize text for fuzzy duplicate detection."""
    normalized = unicodedata.normalize("NFKD", value or "")
    ascii_value = normalized.encode("ascii", "ignore").decode("ascii")
    collapsed = re.sub(r"[^a-z0-9]+", "-", ascii_value.lower()).strip("-")
    return collapsed


def listing_fingerprint(listing: Listing) -> str:
    """Stable cross-platform fingerprint for a property.

    Uses postal code + price + bedrooms + surface to match the same physical
    property across platforms (e.g., Immoweb vs Spotto).  When surface is
    unknown (0) we fall back to including the platform-specific unique key so
    that two different listings on the same platform with the same price never
    collapse into a single fingerprint.
    """
    postal_code = "0000"
    match = re.search(r'\b\d{4}\b', listing.address)
    if match:
        postal_code = match.group(0)

    surface = listing.surface_m2 or 0
    bedrooms = listing.bedrooms or 0

    # If we have no surface info, disambiguate within the same platform by
    # appending the unique key, which still allows cross-platform matching
    # when two listings share postal code + price + bedrooms.
    if surface == 0:
        return "|".join(
            [
                postal_code,
                str(listing.price or 0),
                str(bedrooms),
                listing.unique_key,
            ]
        )

    return "|".join(
        [
            postal_code,
            str(listing.price or 0),
            str(bedrooms),
            str(surface),
        ]
    )


def build_sent_index(history: dict, legacy_seen_ids: set[str]) -> tuple[set[str], set[str]]:
    """Return sent unique keys and cross-platform fingerprints."""
    sent_unique_keys = set(legacy_seen_ids)
    sent_fingerprints: set[str] = set()

    for fingerprint, record in history.get("records", {}).items():
        if not isinstance(record, dict):
            continue
        if not record.get("first_sent_at"):
            continue
        sent_fingerprints.add(fingerprint)
        sent_unique_keys.update(record.get("unique_keys", []))

    return sent_unique_keys, sent_fingerprints


def upsert_listing_record(
    history: dict,
    listing: Listing,
    discovered_at: datetime,
    was_emailed: bool,
) -> None:
    """Create or update a listing record in history."""
    fingerprint = listing_fingerprint(listing)
    record = history.setdefault("records", {}).get(fingerprint, {})

    unique_keys = set(record.get("unique_keys", []))
    unique_keys.add(listing.unique_key)

    discovered_day = discovered_at.date().isoformat()
    record.update(
        {
            "fingerprint": fingerprint,
            "first_seen_at": record.get("first_seen_at", discovered_at.isoformat(timespec="seconds")),
            "last_seen_at": discovered_at.isoformat(timespec="seconds"),
            "first_seen_date": record.get("first_seen_date", discovered_day),
            "last_seen_date": discovered_day,
            "platform": listing.platform,
            "id": listing.id,
            "unique_keys": sorted(unique_keys),
            "title": listing.title,
            "price": listing.price,
            "bedrooms": listing.bedrooms,
            "address": listing.address,
            "url": listing.url,
            "description": listing.description[:1000],
            "image_urls": listing.image_urls[:5],
            "epc_label": listing.epc_label,
            "surface_m2": listing.surface_m2,
            "posted_date": listing.posted_date,
            "property_type": getattr(listing, "property_type", "apartment") or "apartment",
            "transaction_type": getattr(listing, "transaction_type", "rent") or "rent",
            "text_score": listing.text_score,
            "photo_score": listing.photo_score,
            "final_score": listing.final_score,
            "score_reasoning": listing.score_reasoning,
        }
    )

    if was_emailed:
        sent_dates = set(record.get("sent_dates", []))
        sent_dates.add(discovered_day)
        record["first_sent_at"] = record.get("first_sent_at", discovered_at.isoformat(timespec="seconds"))
        record["last_sent_at"] = discovered_at.isoformat(timespec="seconds")
        record["sent_dates"] = sorted(sent_dates)

    history["records"][fingerprint] = record


def iso_week_key(value: date | datetime | None = None) -> str:
    """Return an ISO week key such as 2026-W14."""
    if value is None:
        value = datetime.now()
    if isinstance(value, datetime):
        value = value.date()
    year, week, _ = value.isocalendar()
    return f"{year}-W{week:02d}"


def week_label(week_key: str) -> str:
    """Return a user-facing label from an ISO week key."""
    year, week = week_key.split("-W", maxsplit=1)
    return f"Week {int(week)} of {year}"


def weekly_report_already_sent(history: dict, week_key: str) -> bool:
    """Check whether the weekly digest was already sent."""
    return week_key in set(history.get("weekly_reports_sent", []))


def mark_weekly_report_sent(history: dict, week_key: str) -> None:
    """Record that the weekly digest has been sent."""
    sent = set(history.get("weekly_reports_sent", []))
    sent.add(week_key)
    history["weekly_reports_sent"] = sorted(sent)


def weekly_top_listings(history: dict, week_key: str, limit: int = 10) -> list[Listing]:
    """Build a ranked list of listings first seen in the given week."""
    listings: list[Listing] = []

    for record in history.get("records", {}).values():
        if not isinstance(record, dict):
            continue
        first_seen_at = record.get("first_seen_at")
        if not first_seen_at:
            continue
        try:
            first_seen_dt = datetime.fromisoformat(first_seen_at)
        except ValueError:
            continue
        if iso_week_key(first_seen_dt) != week_key:
            continue
        listings.append(listing_from_record(record))

    listings.sort(key=lambda listing: listing.final_score or 0, reverse=True)
    return listings[:limit]


def listing_from_record(record: dict) -> Listing:
    """Convert a stored history record back into a Listing."""
    stored_type = str(record.get("property_type", "") or "").strip().lower()
    if stored_type not in {"house", "apartment"}:
        # Infer for records written before property_type was tracked.
        url = str(record.get("url", "") or "")
        title = str(record.get("title", "") or "")
        haystack = f"{url} {title}".lower()
        stored_type = "house" if any(
            marker in haystack
            for marker in (
                "/house/", "/huis/", "/huizen/",
                "house for", "house in", "huis te huur", "huis te koop",
                "huis in", "woning", "maison",
            )
        ) else "apartment"
    stored_trans = str(record.get("transaction_type", "") or "").strip().lower()
    if stored_trans not in {"buy", "rent"}:
        url = str(record.get("url", "") or "")
        title = str(record.get("title", "") or "")
        price_val = int(record.get("price", 0) or 0)
        stored_trans = detect_transaction_type(
            fallback="rent",
            url=url,
            title=title,
            price=price_val,
        )
    return Listing(
        id=str(record.get("id", "")),
        platform=record.get("platform", "unknown"),
        title=record.get("title", "Apartment listing"),
        price=int(record.get("price", 0) or 0),
        bedrooms=int(record.get("bedrooms", 0) or 0),
        address=record.get("address", ""),
        url=record.get("url", ""),
        description=record.get("description", ""),
        image_urls=list(record.get("image_urls", [])),
        epc_label=record.get("epc_label"),
        surface_m2=record.get("surface_m2"),
        posted_date=record.get("posted_date"),
        property_type=stored_type,
        transaction_type=stored_trans,
        text_score=record.get("text_score"),
        photo_score=record.get("photo_score"),
        final_score=record.get("final_score"),
        score_reasoning=record.get("score_reasoning"),
    )
