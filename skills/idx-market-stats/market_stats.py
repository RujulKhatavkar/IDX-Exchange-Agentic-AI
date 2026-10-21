#!/usr/bin/env python3
"""Market statistics from california_sold (closed residential sales).

Usage:
    python3 market_stats.py overview --city "Irvine" [--zip 92618] [--type Condominium] [--months N]
    python3 market_stats.py summary  --city "Irvine" [...same options]
    python3 market_stats.py trend    --city "Irvine" [...same options]

Prints one line of JSON:
    {"city": "...", "zip": ..., "type": ..., "months": N, "from": "YYYY-MM-DD", "to": "YYYY-MM-DD",
     "summary": {...},                (summary, overview)
     "change_over_period": {...},     (summary, overview: first full month vs. last full month)
     "reading": {...},                (summary, overview: labels + a one-sentence headline)
     "trend": [{...}, ...]}           (trend, overview: one object per month, oldest first)

overview exists so an agent answering "how's the market?" gets the trend in
the same call as the summary - with only the summary in hand, a model will
happily describe a "stable" or "rising" market it never looked at.

The query builders are pure functions - no DB access - so they're unit
tested directly without a live database, same as search_properties.py.
Every query here is an aggregate: one row (summary) or one row per month
(trend) no matter how many sales match, never the sales themselves.
"""

import argparse
import json
import statistics
import sys
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path

# db.py lives in the sibling property-search skill; resolve() follows the
# ~/.openclaw/skills symlink back into this repo so the path holds there too.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "idx-property-search"))
from db import run_query  # noqa: E402

DEFAULT_MONTHS = 12

# Optional narrowing filters -> california_sold column. Column names come
# only from this fixed dict, never from user input - values are always bound
# through %s - so building SQL with an f-string here is safe.
FILTER_COLUMNS = {
    "zip": "PostalCode",
    "type": "PropertySubType",
}


def date_window(months: int, today: date | None = None) -> tuple[str, str]:
    """Return (from, to) as YYYY-MM-DD strings for the last `months` months.

    CloseDate is stored as YYYY-MM-DD text, so both bounds are compared as
    strings. The upper bound matters: a handful of rows carry typo'd future
    close dates (2028, 2030, 2072) that would otherwise count as sales.
    """
    today = today or date.today()
    return (today - timedelta(days=months * 30)).isoformat(), today.isoformat()


def build_where(city: str, months: int, filters: dict | None = None, today: date | None = None) -> tuple[str, list]:
    """Shared WHERE clause: one city, closed in the window, valid close price."""
    start, end = date_window(months, today)
    where = "City = %s AND PropertyType = 'Residential' AND ClosePrice > 0 AND CloseDate BETWEEN %s AND %s"
    params: list = [city, start, end]
    for key, column in FILTER_COLUMNS.items():
        value = (filters or {}).get(key)
        if value is None:
            continue
        where += f" AND {column} = %s"
        params.append(value)
    return where, params


def build_market_summary_query(
    city: str, months: int = DEFAULT_MONTHS, filters: dict | None = None, today: date | None = None
) -> tuple[str, tuple]:
    """Build one aggregate SELECT for a city's market summary.

    MySQL has no MEDIAN(), so the sales are ranked by price with window
    functions and the median is the average of the middle one (odd count)
    or two (even count) rows. NULLIF guards the ratios: a missing LivingArea
    or ListPrice drops that sale from that one average instead of erroring
    or dropping the sale from every other metric too.
    """
    where, params = build_where(city, months, filters, today)
    sql = f"""
        WITH sales AS (
            SELECT
                ClosePrice, ListPrice, LivingArea, DaysOnMarket,
                ROW_NUMBER() OVER (ORDER BY ClosePrice) AS price_rank,
                COUNT(*) OVER () AS total
            FROM california_sold
            WHERE {where}
        )
        SELECT
            COUNT(*)                                                AS sold_count,
            ROUND(AVG(ClosePrice), 0)                               AS avg_close_price,
            ROUND(AVG(CASE WHEN price_rank IN (FLOOR((total + 1) / 2), CEIL((total + 1) / 2))
                           THEN ClosePrice END), 0)                 AS median_close_price,
            ROUND(AVG(ClosePrice / NULLIF(LivingArea, 0)), 0)       AS avg_price_per_sqft,
            ROUND(AVG(DaysOnMarket), 1)                             AS avg_days_on_market,
            ROUND(AVG(ClosePrice / NULLIF(ListPrice, 0)) * 100, 1)  AS list_to_close_pct
        FROM sales
    """
    return sql, tuple(params)


def get_market_summary(
    city: str, months: int = DEFAULT_MONTHS, filters: dict | None = None
) -> dict:
    sql, params = build_market_summary_query(city, months, filters)
    return run_query(sql, params)[0]


def build_price_trend_query(
    city: str, months: int = DEFAULT_MONTHS, filters: dict | None = None, today: date | None = None
) -> tuple[str, tuple]:
    """Build one aggregate SELECT with a row per close month (YYYY-MM).

    Same median trick as the summary, but ranked within each month
    (PARTITION BY). The month is the first 7 characters of the text
    CloseDate. days_covered counts distinct close dates in the month, so a
    month the data only partly spans (the dump starts and ends mid-month)
    is visible as such instead of looking like a sales slump.
    """
    where, params = build_where(city, months, filters, today)
    sql = f"""
        WITH sales AS (
            SELECT
                SUBSTR(CloseDate, 1, 7) AS month, CloseDate,
                ClosePrice, LivingArea, DaysOnMarket,
                ROW_NUMBER() OVER (PARTITION BY SUBSTR(CloseDate, 1, 7) ORDER BY ClosePrice) AS price_rank,
                COUNT(*) OVER (PARTITION BY SUBSTR(CloseDate, 1, 7)) AS total
            FROM california_sold
            WHERE {where}
        )
        SELECT
            month,
            COUNT(*)                                                AS sales,
            COUNT(DISTINCT CloseDate)                               AS days_covered,
            ROUND(AVG(ClosePrice), 0)                               AS avg_close_price,
            ROUND(AVG(CASE WHEN price_rank IN (FLOOR((total + 1) / 2), CEIL((total + 1) / 2))
                           THEN ClosePrice END), 0)                 AS median_close_price,
            ROUND(AVG(ClosePrice / NULLIF(LivingArea, 0)), 0)       AS avg_price_per_sqft,
            ROUND(AVG(DaysOnMarket), 1)                             AS avg_days_on_market
        FROM sales
        GROUP BY month
        ORDER BY month
    """
    return sql, tuple(params)


def pct_change(previous, current) -> float | None:
    if previous in (None, 0) or current is None:
        return None
    return round((float(current) - float(previous)) / float(previous) * 100, 1)


def add_month_over_month(rows: list[dict]) -> list[dict]:
    """Add median price and $/sqft % change vs. the previous month's row.

    The first month has nothing to compare against, so its changes are None.
    """
    previous: dict = {}
    for row in rows:
        row["median_price_change_pct"] = pct_change(previous.get("median_close_price"), row["median_close_price"])
        row["price_per_sqft_change_pct"] = pct_change(previous.get("avg_price_per_sqft"), row["avg_price_per_sqft"])
        previous = row
    return rows


def mark_partial_months(rows: list[dict]) -> list[dict]:
    """Flag months the data only partly spans.

    A month is partial when it has sales on fewer than 75% as many days as
    the typical (median) month - e.g. the dump starts 18 Mar and ends
    17 Sep, so those months show ~10-14 days against ~22 elsewhere.
    """
    if not rows:
        return rows
    typical = statistics.median(row["days_covered"] for row in rows)
    for row in rows:
        row["partial"] = row["days_covered"] < 0.75 * typical
    return rows


def change_over_period(rows: list[dict]) -> dict | None:
    """Compare the first full month of the trend with the last full one.

    Partial months are skipped at both ends so a half-month doesn't pass
    for a price move. Returns None when fewer than two full months exist.
    """
    full = [row for row in rows if not row.get("partial")]
    if len(full) < 2:
        return None
    first, last = full[0], full[-1]
    dom_change = None
    if first["avg_days_on_market"] is not None and last["avg_days_on_market"] is not None:
        dom_change = round(float(last["avg_days_on_market"]) - float(first["avg_days_on_market"]), 1)
    return {
        "from_month": first["month"],
        "to_month": last["month"],
        "median_price_change_pct": pct_change(first["median_close_price"], last["median_close_price"]),
        "price_per_sqft_change_pct": pct_change(first["avg_price_per_sqft"], last["avg_price_per_sqft"]),
        "days_on_market_change": dom_change,
    }


def get_price_trend(city: str, months: int = DEFAULT_MONTHS, filters: dict | None = None) -> list[dict]:
    sql, params = build_price_trend_query(city, months, filters)
    return mark_partial_months(add_month_over_month(run_query(sql, params)))


# Fixed thresholds for turning numbers into words. They live here rather
# than in SKILL.md because a small model given prose rules improvises its
# own reading (it called 28 days + 99.5% of asking "buyer-friendly").
FLAT_BAND_PCT = 3.0      # price change within +/- this is "flat"
FAST_DAYS = 30           # under this many days on market is "fast"
SLOW_DAYS = 60           # over this is "slow"
NEAR_ASKING_PCT = 97.0   # list-to-close at or above this is "near asking"


def direction(change_pct) -> str:
    if change_pct is None:
        return "unknown"
    if change_pct > FLAT_BAND_PCT:
        return "rising"
    if change_pct < -FLAT_BAND_PCT:
        return "falling"
    return "flat"


def month_label(month: str) -> str:
    return date.fromisoformat(f"{month}-01").strftime("%b %Y")


def market_reading(city: str, summary: dict, change: dict | None) -> dict | None:
    """Label price direction, speed, negotiation room and market lean.

    Returns None when there are no sales. `headline` is one plain sentence
    built only from these numbers, for the agent to quote as-is.
    """
    if not summary.get("sold_count"):
        return None
    dom = summary["avg_days_on_market"]
    ratio = summary["list_to_close_pct"]

    if dom is None:
        speed = "unknown"
    elif dom < FAST_DAYS:
        speed = "fast"
    elif dom > SLOW_DAYS:
        speed = "slow"
    else:
        speed = "moderate"

    if ratio is None:
        negotiation = "unknown"
    elif ratio >= 100:
        negotiation = "at or above asking"
    elif ratio >= NEAR_ASKING_PCT:
        negotiation = "near asking"
    else:
        negotiation = "below asking"

    if speed == "fast" and negotiation != "below asking":
        lean = "seller-leaning"
    elif speed == "slow" and negotiation == "below asking":
        lean = "buyer-leaning"
    else:
        lean = "balanced"

    median_dir = direction(change and change["median_price_change_pct"])
    sqft_dir = direction(change and change["price_per_sqft_change_pct"])

    def signed(pct) -> str:
        return "n/a" if pct is None else f"{pct:+}%"

    if change:
        period = f"{month_label(change['from_month'])} to {month_label(change['to_month'])}"
        prices = (
            f"from {period} the median price was {median_dir} ({signed(change['median_price_change_pct'])})"
            f" and price per sq ft was {sqft_dir} ({signed(change['price_per_sqft_change_pct'])})"
        )
    else:
        prices = "there isn't enough monthly data to call a price trend"
    pace = f"{float(dom):g} days on market" if dom is not None else "days on market unknown"
    asking = f"{float(ratio):g}% of list price" if ratio is not None else "list-to-close unknown"
    headline = f"{city}: {prices}; homes sell at a {speed} pace ({pace}) for {negotiation} ({asking}), a {lean} market."
    return {
        "median_price_direction": median_dir,
        "price_per_sqft_direction": sqft_dir,
        "speed": speed,
        "negotiation": negotiation,
        "market_lean": lean,
        "headline": headline,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    for name, help_text in (
        ("overview", "Summary + monthly trend + first-to-last change, in one call"),
        ("summary", "Price, $/sqft, days on market and list-to-close for a city"),
        ("trend", "Month-by-month price, $/sqft and days on market for a city"),
    ):
        cmd = sub.add_parser(name, help=help_text)
        cmd.add_argument("--city", required=True)
        cmd.add_argument("--zip", help="Narrow to one ZIP code (PostalCode)")
        cmd.add_argument("--type", help="Narrow to one PropertySubType, e.g. Condominium")
        cmd.add_argument("--months", type=int, default=DEFAULT_MONTHS)

    args = parser.parse_args()

    filters = {"zip": args.zip, "type": args.type}
    start, end = date_window(args.months)
    output = {"city": args.city, **filters, "months": args.months, "from": start, "to": end}
    if args.command in ("overview", "summary"):
        # summary carries the reading too: an agent that reaches for summary
        # out of habit still gets the price direction instead of guessing it.
        trend = get_price_trend(args.city, args.months, filters)
        change = change_over_period(trend)
        output["summary"] = get_market_summary(args.city, args.months, filters)
        output["change_over_period"] = change
        output["reading"] = market_reading(args.city, output["summary"], change)
        if args.command == "overview":
            output["trend"] = trend
    else:
        output["trend"] = get_price_trend(args.city, args.months, filters)
    # AVG over an integer column comes back as Decimal; emit it as a JSON number.
    print(json.dumps(output, default=lambda v: float(v) if isinstance(v, Decimal) else str(v)))


if __name__ == "__main__":
    main()
