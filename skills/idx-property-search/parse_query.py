#!/usr/bin/env python3
"""Parse a free text real estate query into a structured filter object.

Usage:
    python3 parse_query.py "3 bed condos in Irvine under $1.5M with a pool"

Prints JSON whose keys map to rets_property columns:
    city      -> L_City            maxPrice -> L_SystemPrice (<=)
    minPrice  -> L_SystemPrice (>=) beds     -> L_Keyword2 (>=)
    baths     -> LM_Dec_3 (>=)     sqft     -> LM_Int2_3 (>=)
    type      -> L_Type_           pool     -> PoolPrivateYN
    hasView   -> ViewYN            maxHoa   -> AssociationFee (<=)
"""

import json
import re
import sys
from pathlib import Path

# Optional: one city per line, exported with
#   SELECT DISTINCT L_City FROM rets_property WHERE L_City <> '' ORDER BY L_City;
# When present, cities are matched against this list instead of a regex guess.
CITIES_FILE = Path(__file__).with_name("cities.txt")

# Values match L_Type_ in rets_property, checked with
#   SELECT L_Type_, COUNT(*) FROM rets_property GROUP BY L_Type_;
# rets_property holds residential listings only; there is no land type.
TYPE_PATTERNS = [
    (r"\bsingle[\s-]?family\b|\bsfr\b|\bdetached\b", "SingleFamilyResidence"),
    (r"\btown\s?(?:home|house)s?\b", "Townhouse"),
    (r"\bcondo(?:minium)?s?\b", "Condominium"),
    (r"\bduplex(?:es)?\b", "Duplex"),
    (r"\btriplex(?:es)?\b", "Triplex"),
    (r"\b(?:quadruplex|fourplex|4[\s-]?plex)(?:es)?\b", "Quadruplex"),
    (r"\bmobile\s+homes?\b", "MobileHome"),
    (r"\bmanufactured\b", "ManufacturedOnLand"),
    (r"\bcabins?\b", "Cabin"),
    (r"\bco[\s-]?ops?\b|\bstock\s+cooperative\b", "StockCooperative"),
]

MONEY = r"\$?\s*(\d[\d,]*(?:\.\d+)?)\s*(k|m|mm|mil|million|thousand)?\b"
MAX_WORDS = r"(?:under|below|less than|up to|no more than|max(?:imum)?|at most|<=?)"
MIN_WORDS = r"(?:over|above|more than|at least|min(?:imum)?|starting at|>=?)"


def to_amount(num: str, suffix: str | None) -> int:
    value = float(num.replace(",", ""))
    suffix = (suffix or "").lower()
    if suffix in ("k", "thousand"):
        value *= 1_000
    elif suffix in ("m", "mm", "mil", "million"):
        value *= 1_000_000
    return int(round(value))


def load_cities() -> list[str]:
    if not CITIES_FILE.exists():
        return []
    cities = [c.strip() for c in CITIES_FILE.read_text().splitlines() if c.strip()]
    return sorted(cities, key=len, reverse=True)  # longest first: "Newport Beach" before "Newport"


def blank(text: str, match: re.Match) -> str:
    """Replace a consumed span with spaces so later patterns cannot reuse it."""
    return text[: match.start()] + " " * (match.end() - match.start()) + text[match.end():]


def parse_city(query: str, cities: list[str]) -> str | None:
    lowered = query.lower()
    for city in cities:
        if re.search(rf"\b{re.escape(city.lower())}\b", lowered):
            return city
    stop = (
        r"under|below|over|above|with|for|at|between|that|and|less|more|max|min|"
        r"priced|around|near|starting|no|having|built|hoa"
    )
    m = re.search(
        rf"\b(?:in|near|around)\s+([a-z][a-z.' ]*?)(?=\s+(?:{stop})\b|[,.?!]|$)",
        query,
        re.IGNORECASE,
    )
    if not m:
        return None
    city = m.group(1).strip()
    if city.lower() in {"the", "a", "an", "my", "this", "that"}:
        return None
    return " ".join(w.capitalize() for w in city.split())


def parse_property_query(query: str, cities: list[str] | None = None) -> dict:
    cities = load_cities() if cities is None else cities
    text = query.lower()
    out = {
        "city": parse_city(query, cities),
        "minPrice": None,
        "maxPrice": None,
        "beds": None,
        "baths": None,
        "sqft": None,
        "type": None,
        "pool": None,
        "hasView": None,
        "maxHoa": None,
    }

    # HOA first so "HOA under $500" is never read as a $500 max price.
    if re.search(r"\bno hoa\b", text):
        out["maxHoa"] = 0
        text = re.sub(r"\bno hoa\b", " ", text)
    m = re.search(rf"\bhoa(?:\s+fees?)?\s*(?:{MAX_WORDS}|of)?\s*\$?\s*(\d[\d,]*)", text)
    if m:
        out["maxHoa"] = int(m.group(1).replace(",", ""))
        text = blank(text, m)

    # Size and room counts before prices so "at least 3 beds" is not a price.
    m = re.search(r"(\d[\d,]*)\s*\+?\s*(?:sq\.?\s*ft\.?|sqft|square\s+feet|sf)\b", text)
    if m:
        out["sqft"] = int(m.group(1).replace(",", ""))
        text = blank(text, m)

    m = re.search(r"(\d+(?:\.5)?)\s*\+?\s*(?:-\s*)?(?:bath(?:room)?s?|ba)\b", text)
    if m:
        out["baths"] = float(m.group(1)) if "." in m.group(1) else int(m.group(1))
        text = blank(text, m)

    m = re.search(r"(\d+)\s*\+?\s*(?:-\s*)?(?:bed(?:room)?s?|bd|br)\b", text)
    if m:
        out["beds"] = int(m.group(1))
        text = blank(text, m)

    # Prices.
    m = re.search(rf"\bbetween\s*{MONEY}\s*(?:and|to|-)\s*{MONEY}", text)
    if m:
        lo_suffix = m.group(2) or m.group(4)  # "between 800 and 1.2m" shares the suffix
        out["minPrice"] = to_amount(m.group(1), lo_suffix)
        out["maxPrice"] = to_amount(m.group(3), m.group(4))
        text = blank(text, m)
    m = re.search(rf"(?:{MAX_WORDS}|budget(?:\s+(?:of|is))?)\s*{MONEY}", text)
    if m:
        out["maxPrice"] = to_amount(m.group(1), m.group(2))
        text = blank(text, m)
    m = re.search(rf"{MIN_WORDS}\s*{MONEY}", text)
    if m:
        out["minPrice"] = to_amount(m.group(1), m.group(2))
        text = blank(text, m)

    for pattern, value in TYPE_PATTERNS:
        if re.search(pattern, text):
            out["type"] = value
            break

    if re.search(r"\b(?:no|without)\s+(?:a\s+)?pool\b", text):
        out["pool"] = "False"
    elif re.search(r"\bpool\b", text):
        out["pool"] = "True"

    if re.search(r"\b(?:no|without)\s+(?:a\s+)?views?\b", text):
        out["hasView"] = "False"
    elif re.search(r"\bviews?\b", text):
        out["hasView"] = "True"

    return out


def main() -> None:
    if len(sys.argv) < 2:
        print('usage: parse_query.py "<query>"', file=sys.stderr)
        sys.exit(2)
    filters = parse_property_query(" ".join(sys.argv[1:]))
    print(json.dumps({k: v for k, v in filters.items() if v is not None}))


if __name__ == "__main__":
    main()
