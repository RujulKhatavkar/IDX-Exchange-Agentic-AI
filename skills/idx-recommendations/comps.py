"""Check a listing's asking price against recent sold comps in california_sold.

Comps are residential sales in the same city and of the same property type,
with living area within 20% of the listing's, over the last COMP_MONTHS
months of sales. comp_price = median sold $/sq ft x the listing's sq ft;
delta_pct says how far the asking price is above (+) or below (-) that.

Differences from the handbook's validate_with_comps:

- Median, not average, $/sq ft: a few very expensive sales pull the average
  up (by 3% in Pasadena and 9% in Lake Arrowhead), which is enough to flip a
  verdict from "in line" to "above comps".
- Same property type: condos and houses in one city sell at different $/sq ft,
  so mixing them skews the comp price (in Pasadena, half the size-matched
  comps for a house are condos and townhomes).
- The window ends at the latest sale in the data, not today: the sold data
  stops at a fixed date, so "6 months before today" shrinks every day and
  eventually finds nothing. It still never goes past today, so the few typo'd
  future close dates (2028, 2072) are excluded, as in Week 5.
- The verdict is a fixed label computed here, so the agent quotes it rather
  than reading the numbers itself (the Week 5 lesson).
"""

import statistics
from datetime import date, timedelta

COMP_MONTHS = 6
SQFT_BAND = 0.2          # comps within +/-20% of the listing's living area
MIN_COMPS = 3            # fewer sales than this can't support a verdict
IN_LINE_PCT = 5.0        # |delta| up to this is "in line with recent comps"


def latest_sale_query(today: date) -> tuple[str, tuple]:
    sql = """
        SELECT MAX(CloseDate) AS latest
        FROM california_sold
        WHERE PropertyType = 'Residential' AND CloseDate <= %s
    """
    return sql, (today.isoformat(),)


def comp_window(latest_sale: str, months: int = COMP_MONTHS) -> tuple[str, str]:
    """(from, to) as YYYY-MM-DD strings, ending at the latest recorded sale."""
    end = date.fromisoformat(latest_sale[:10])
    return (end - timedelta(days=months * 30)).isoformat(), end.isoformat()


def build_comps_query(city: str, sqft: int, property_type: str | None,
                      window: tuple[str, str]) -> tuple[str, tuple]:
    sql = """
        SELECT ClosePrice / LivingArea AS ppsf
        FROM california_sold
        WHERE City = %s AND PropertyType = 'Residential' AND ClosePrice > 0
          AND LivingArea BETWEEN %s AND %s
          AND CloseDate BETWEEN %s AND %s
    """
    params: list = [city, round(sqft * (1 - SQFT_BAND)), round(sqft * (1 + SQFT_BAND)), *window]
    if property_type:
        sql += " AND PropertySubType = %s"
        params.append(property_type)
    return sql, tuple(params)


def median_ppsf(rows: list[dict]) -> float | None:
    values = [float(row["ppsf"]) for row in rows if row.get("ppsf")]
    return statistics.median(values) if values else None


def price_assessment(price, sqft, comp_ppsf, comp_count: int) -> dict:
    """comp_price, delta_pct and a fixed verdict label for one listing."""
    result = {"list_price": price, "comp_count": int(comp_count or 0),
              "median_comp_ppsf": None, "comp_price": None, "delta_pct": None}
    if not sqft:
        return {**result, "verdict": "no sq ft listed, so it can't be compared to comps"}
    if result["comp_count"] < MIN_COMPS or not comp_ppsf:
        return {**result, "verdict": "not enough recent comps to judge the price"}
    comp_price = float(comp_ppsf) * float(sqft)
    delta = (float(price) - comp_price) / comp_price * 100
    if delta > IN_LINE_PCT:
        verdict = f"priced {delta:.1f}% above recent comps"
    elif delta < -IN_LINE_PCT:
        verdict = f"priced {-delta:.1f}% below recent comps"
    else:
        verdict = "in line with recent comps"
    return {**result, "median_comp_ppsf": round(float(comp_ppsf)), "comp_price": round(comp_price),
            "delta_pct": round(delta, 1), "verdict": verdict}
