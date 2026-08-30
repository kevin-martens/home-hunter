"""Central configuration module for Apartment Hunter."""

import os
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

# Search settings
TARGET_CITY = os.environ.get("TARGET_CITY", "gent").strip()
TARGET_POSTAL_CODE = os.environ.get("TARGET_POSTAL_CODE", "9000").strip()

# Target Locations: list of tuples (city, postal_code)
_raw_locations = os.environ.get("TARGET_LOCATIONS", "").strip()
if _raw_locations:
    TARGET_LOCATIONS = []
    for loc in _raw_locations.split(","):
        parts = loc.strip().split(":")
        if len(parts) == 2:
            TARGET_LOCATIONS.append((parts[0].strip(), parts[1].strip()))
else:
    # Fallback to single city configuration
    TARGET_LOCATIONS = [(TARGET_CITY, TARGET_POSTAL_CODE)]

# Property and Transaction Types
_raw_prop_types = os.environ.get("PROPERTY_TYPES", "apartment").strip()
PROPERTY_TYPES = [pt.strip().lower() for pt in _raw_prop_types.split(",") if pt.strip()]

_raw_trans_types = os.environ.get("TRANSACTION_TYPES", "rent").strip()
TRANSACTION_TYPES = [tt.strip().lower() for tt in _raw_trans_types.split(",") if tt.strip()]

try:
    MIN_PRICE = int(os.environ.get("MIN_PRICE", "800"))
except ValueError:
    MIN_PRICE = 800

try:
    MAX_PRICE = int(os.environ.get("MAX_PRICE", "1000"))
except ValueError:
    MAX_PRICE = 1000

try:
    MIN_BUY_PRICE = int(os.environ.get("MIN_BUY_PRICE", "0"))
except ValueError:
    MIN_BUY_PRICE = 0

try:
    MAX_BUY_PRICE = int(os.environ.get("MAX_BUY_PRICE", "500000"))
except ValueError:
    MAX_BUY_PRICE = 500000

try:
    MIN_BEDROOMS = int(os.environ.get("MIN_BEDROOMS", "1"))
except ValueError:
    MIN_BEDROOMS = 1

# Proximity/Station Filter settings
ENABLE_STATION_FILTER = os.environ.get("ENABLE_STATION_FILTER", "false").lower() in {"1", "true", "yes", "on"}

# Parse station/proximity keywords
DEFAULT_NEAR = [
    "gent-sint-pieters", "sint-pieters", "stationsbuurt", "st pieters",
    "koningin elisabethlaan", "prinses clementinalaan", "clementinalaan",
    "kortrijksesteenweg", "smidsestraat", "aannemersstraat", "vasco da gamastraat",
    "patijntjesstraat", "zwijnaardsesteenweg", "voskenslaan", "sint-denijslaan"
]

DEFAULT_FAR = [
    "dampoort", "muide", "wondelgem", "oostakker", "mariakerke", "sint-amandsberg",
    "gentbrugge", "moscou", "rabat", "rabot", "bloemekenswijk", "dok noord",
    "blaisantvest", "oslostraat", "blankenbergestraat", "hofstraat", "komijnstraat",
    "begijnhoflaan", "steenakker", "kasteellaan", "francisco ferrerlaan"
]

near_raw = os.environ.get("STATION_NEAR_KEYWORDS", "")
STATION_NEAR_KEYWORDS = [
    k.strip().lower() for k in near_raw.split(",") if k.strip()
] if near_raw else DEFAULT_NEAR

far_raw = os.environ.get("STATION_FAR_KEYWORDS", "")
STATION_FAR_KEYWORDS = [
    k.strip().lower() for k in far_raw.split(",") if k.strip()
] if far_raw else DEFAULT_FAR
