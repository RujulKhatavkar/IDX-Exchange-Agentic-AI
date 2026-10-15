#!/usr/bin/env python3
"""Query rets_property (active listings) and california_sold (sold comps).

Usage:
    python3 search_properties.py search "<free text query>" [--page N] [--limit N] [--known '<json>']
    python3 search_properties.py comps --city "Irvine" [--months N]

Prints one line of JSON:
    search -> {"filters": {...}, "page": N, "limit": N, "count": N, "results": [...]}
    comps  -> {"city": "...", "months": N, "count": N, "results": [...]}

--known carries a multi-turn conversation's filters forward: pass the
previous call's "filters" output back in on the next turn, and whatever
the new <query> mentions overrides it (merge_filters in parse_query.py).
There is no server-side session store - the calling agent already has the
whole conversation in context, so it is the one responsible for holding
onto and re-sending the filters JSON turn to turn.

The query builders (build_active_listing_query, build_sold_comps_query) are
pure functions - no DB access - so they're unit tested directly without a
live database. Row counts are capped at MAX_LIMIT: nothing in this skill
ever returns an unbounded result set.
"""

import argparse
import json
import sys
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from db import run_query  # noqa: E402
from parse_query import merge_filters, parse_property_query  # noqa: E402

MAX_LIMIT = 50
DEFAULT_LIMIT = 10

ACTIVE_LISTING_COLUMNS = """
    L_ListingID, L_DisplayId, L_Address, L_City, L_Zip,
    L_SystemPrice AS price, L_Keyword2 AS beds, LM_Dec_3 AS baths,
    LM_Int2_3 AS sqft, L_Type_ AS type, L_Status AS status,
    LMD_MP_Latitude AS lat, LMD_MP_Longitude AS lng,
    YearBuilt, AssociationFee, DaysOnMarket,
    PoolPrivateYN AS pool, ViewYN AS hasView, FireplaceYN AS fireplace, PhotoCount,
    LA1_UserFirstName AS agentFirstName, LA1_UserLastName AS agentLastName,
    LO1_OrganizationName AS brokerage
"""

# Maps parse_query.py output keys to (column, operator). Column/operator
# come only from this fixed dict, never from user input - values are always
# bound through %s - so building SQL with an f-string here is safe.
FILTER_COLUMNS = {
    "city": ("L_City", "="),
    "minPrice": ("L_SystemPrice", ">="),
    "maxPrice": ("L_SystemPrice", "<="),
    "beds": ("L_Keyword2", ">="),
    "baths": ("LM_Dec_3", ">="),
    "sqft": ("LM_Int2_3", ">="),
    "type": ("L_Type_", "="),
    "pool": ("PoolPrivateYN", "="),
    "hasView": ("ViewYN", "="),
    "maxHoa": ("AssociationFee", "<="),
}

# PoolPrivateYN and ViewYN store "1" for true and "" (or NULL, left
# unqueryable here) for false - never the literal "True"/"False" the
# handbook's schema doc describes. parse_query.py's output stays "True"/
# "False" since that's the right contract for any consumer; this table's
# storage quirk is translated only here, right before binding.
YN_COLUMN_VALUES = {"True": "1", "False": ""}

SOLD_COMP_COLUMNS = """
    ListingKey, UnparsedAddress, City, CloseDate, ClosePrice,
    OriginalListPrice, ListPrice, DaysOnMarket,
    BedroomsTotal AS beds, BathroomsTotalInteger AS baths, LivingArea AS sqft,
    PropertyType, PropertySubType, YearBuilt,
    ListAgentFullName, ListOfficeName, BuyerOfficeName
"""


def clamp_limit(limit: int) -> int:
    return max(1, min(limit, MAX_LIMIT))


def build_active_listing_query(
    filters: dict, page: int = 1, limit: int = DEFAULT_LIMIT
) -> tuple[str, tuple]:
    """Build a parameterized SELECT against rets_property from parser filters."""
    limit = clamp_limit(limit)
    offset = (max(page, 1) - 1) * limit

    sql = f"SELECT {ACTIVE_LISTING_COLUMNS} FROM rets_property WHERE L_Status = 'Active'"
    params: list = []
    for key, (column, op) in FILTER_COLUMNS.items():
        value = filters.get(key)
        if value is None:
            continue
        if key in ("pool", "hasView"):
            value = YN_COLUMN_VALUES.get(value, value)
        sql += f" AND {column} {op} %s"
        params.append(value)
    sql += " ORDER BY L_SystemPrice ASC LIMIT %s OFFSET %s"
    params.extend([limit, offset])
    return sql, tuple(params)


def search_active_listings(filters: dict, page: int = 1, limit: int = DEFAULT_LIMIT) -> list[dict]:
    sql, params = build_active_listing_query(filters, page, limit)
    return run_query(sql, params)


def build_sold_comps_query(city: str, months: int = 12, limit: int = MAX_LIMIT) -> tuple[str, tuple]:
    """Build a parameterized SELECT against california_sold for recent comps.

    CloseDate is stored as YYYY-MM-DD text, not a DATE column, so the cutoff
    is computed here and compared as a string rather than relying on MySQL
    date arithmetic over a varchar.
    """
    limit = clamp_limit(limit)
    cutoff = (date.today() - timedelta(days=months * 30)).isoformat()
    sql = f"""
        SELECT {SOLD_COMP_COLUMNS}
        FROM california_sold
        WHERE City = %s AND PropertyType = 'Residential' AND CloseDate >= %s
        ORDER BY CloseDate DESC
        LIMIT %s
    """
    return sql, (city, cutoff, limit)


def get_sold_comps(city: str, months: int = 12, limit: int = MAX_LIMIT) -> list[dict]:
    sql, params = build_sold_comps_query(city, months, limit)
    return run_query(sql, params)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    search = sub.add_parser("search", help="Search active listings from a free text query")
    search.add_argument("query")
    search.add_argument("--page", type=int, default=1)
    search.add_argument("--limit", type=int, default=DEFAULT_LIMIT)
    search.add_argument(
        "--known",
        help="JSON object of filters already established earlier in this conversation "
        "(the previous call's 'filters' output). New values in <query> override these.",
    )

    comps = sub.add_parser("comps", help="Sold comps for a city")
    comps.add_argument("--city", required=True)
    comps.add_argument("--months", type=int, default=12)

    args = parser.parse_args()

    if args.command == "search":
        new_filters = parse_property_query(args.query)
        filters = merge_filters(json.loads(args.known), new_filters) if args.known else new_filters
        results = search_active_listings(filters, args.page, args.limit)
        output = {
            "filters": {k: v for k, v in filters.items() if v is not None},
            "page": args.page,
            "limit": clamp_limit(args.limit),
            "count": len(results),
            "results": results,
        }
    else:
        results = get_sold_comps(args.city, args.months)
        output = {"city": args.city, "months": args.months, "count": len(results), "results": results}

    print(json.dumps(output, default=str))


if __name__ == "__main__":
    main()
