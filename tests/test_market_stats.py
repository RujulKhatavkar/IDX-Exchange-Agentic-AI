import sys
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

# Test the copy in this repo: skills/idx-market-stats
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "skills" / "idx-market-stats"))
from market_stats import (  # noqa: E402
    add_month_over_month,
    build_market_summary_query,
    build_price_trend_query,
    change_over_period,
    date_window,
    mark_partial_months,
    market_reading,
    pct_change,
)

TODAY = date(2026, 10, 21)
BUILDERS = [build_market_summary_query, build_price_trend_query]


def test_date_window_is_months_times_30_days_ending_today():
    assert date_window(12, TODAY) == ("2025-10-26", "2026-10-21")
    assert date_window(1, TODAY) == ("2026-09-21", "2026-10-21")


@pytest.mark.parametrize("build", BUILDERS)
def test_city_only_binds_city_and_window(build):
    sql, params = build("Irvine", 12, today=TODAY)
    assert "City = %s" in sql
    assert params == ("Irvine", "2025-10-26", "2026-10-21")


@pytest.mark.parametrize("build", BUILDERS)
def test_window_has_an_upper_bound(build):
    """A few rows have typo'd future close dates (2028, 2030, 2072). Without
    an upper bound they'd count as sales in every window."""
    sql, _ = build("Irvine", 12, today=TODAY)
    assert "CloseDate BETWEEN %s AND %s" in sql


@pytest.mark.parametrize("build", BUILDERS)
def test_excludes_non_residential_and_zero_prices(build):
    sql, _ = build("Irvine", 12, today=TODAY)
    assert "PropertyType = 'Residential'" in sql
    assert "ClosePrice > 0" in sql


@pytest.mark.parametrize("build", BUILDERS)
def test_zip_and_type_filters_add_clauses_in_order(build):
    sql, params = build("Irvine", 12, {"zip": "92618", "type": "Condominium"}, today=TODAY)
    assert "AND PostalCode = %s" in sql
    assert "AND PropertySubType = %s" in sql
    assert params == ("Irvine", "2025-10-26", "2026-10-21", "92618", "Condominium")


@pytest.mark.parametrize("build", BUILDERS)
def test_none_filters_are_skipped(build):
    sql, params = build("Irvine", 12, {"zip": None, "type": None}, today=TODAY)
    assert "PostalCode" not in sql
    assert "PropertySubType" not in sql
    assert len(params) == 3


@pytest.mark.parametrize("build", BUILDERS)
def test_no_placeholder_count_mismatch(build):
    sql, params = build("Irvine", 6, {"zip": "92618", "type": "Townhouse"}, today=TODAY)
    assert sql.count("%s") == len(params)


@pytest.mark.parametrize("build", BUILDERS)
def test_user_values_never_inlined_into_sql(build):
    city = "Irvine'; DROP TABLE california_sold; --"
    sql, params = build(city, 12, {"zip": "92618' OR '1'='1"}, today=TODAY)
    assert "DROP TABLE" not in sql
    assert "'1'='1" not in sql
    assert params[0] == city


@pytest.mark.parametrize("build", BUILDERS)
def test_median_uses_middle_rank(build):
    """MySQL has no MEDIAN(); averaging the rows ranked FLOOR/CEIL((n+1)/2)
    picks the middle row for odd n and the middle two for even n."""
    sql, _ = build("Irvine", 12, today=TODAY)
    assert "ROW_NUMBER() OVER" in sql
    assert "FLOOR((total + 1) / 2), CEIL((total + 1) / 2)" in sql


def test_summary_is_a_single_aggregate_row():
    sql, _ = build_market_summary_query("Irvine", 12, today=TODAY)
    assert "GROUP BY" not in sql
    for metric in ("sold_count", "avg_close_price", "median_close_price",
                   "avg_price_per_sqft", "avg_days_on_market", "list_to_close_pct"):
        assert f"AS {metric}" in sql


def test_trend_ranks_and_groups_per_month():
    sql, _ = build_price_trend_query("Irvine", 12, today=TODAY)
    assert "PARTITION BY SUBSTR(CloseDate, 1, 7)" in sql
    assert "GROUP BY month" in sql
    assert "ORDER BY month" in sql
    assert "AS days_covered" in sql


@pytest.mark.parametrize(
    "previous,current,expected",
    [
        (100, 110, 10.0),
        (200, 150, -25.0),
        (Decimal("1500000"), Decimal("1627500"), 8.5),
        (None, 100, None),  # first month: nothing to compare against
        (0, 100, None),     # avoid dividing by zero
        (100, None, None),  # month with no $/sqft data
    ],
)
def test_pct_change(previous, current, expected):
    assert pct_change(previous, current) == expected


def test_month_over_month_compares_each_row_to_the_one_before():
    rows = [
        {"month": "2026-04", "median_close_price": Decimal("1500000"), "avg_price_per_sqft": Decimal("800")},
        {"month": "2026-05", "median_close_price": Decimal("1650000"), "avg_price_per_sqft": Decimal("780")},
        {"month": "2026-06", "median_close_price": Decimal("1650000"), "avg_price_per_sqft": Decimal("819")},
    ]
    add_month_over_month(rows)
    assert [r["median_price_change_pct"] for r in rows] == [None, 10.0, 0.0]
    assert [r["price_per_sqft_change_pct"] for r in rows] == [None, -2.5, 5.0]


def test_month_over_month_on_empty_trend():
    assert add_month_over_month([]) == []


def month(m, days, median=1_000_000, ppsf=800, dom=30.0):
    return {"month": m, "days_covered": days, "median_close_price": median,
            "avg_price_per_sqft": ppsf, "avg_days_on_market": dom}


def test_partial_months_are_the_short_ones_at_the_edges():
    """Mirrors the real dump: starts 18 Mar, ends 17 Sep."""
    rows = [month("2026-03", 10), month("2026-04", 22), month("2026-05", 23),
            month("2026-06", 21), month("2026-07", 23), month("2026-08", 22), month("2026-09", 14)]
    mark_partial_months(rows)
    assert [r["partial"] for r in rows] == [True, False, False, False, False, False, True]


def test_partial_months_on_empty_trend():
    assert mark_partial_months([]) == []


def test_change_over_period_skips_partial_months_at_both_ends():
    rows = mark_partial_months([
        month("2026-03", 10, median=500_000),  # partial: would fake a +200% "rise"
        month("2026-04", 22, median=1_500_000, ppsf=800, dom=40.0),
        month("2026-05", 22, median=1_550_000, ppsf=810, dom=35.0),
        month("2026-06", 22, median=1_650_000, ppsf=820, dom=33.7),
        month("2026-07", 9, median=3_000_000),  # partial
    ])
    assert change_over_period(rows) == {
        "from_month": "2026-04",
        "to_month": "2026-06",
        "median_price_change_pct": 10.0,
        "price_per_sqft_change_pct": 2.5,
        "days_on_market_change": -6.3,
    }


def test_change_over_period_needs_two_full_months():
    assert change_over_period([]) is None
    assert change_over_period(mark_partial_months([month("2026-04", 22)])) is None


def test_change_over_period_handles_decimals_and_missing_dom():
    rows = [
        {**month("2026-04", 22, median=Decimal("1500000"), ppsf=Decimal("800")), "avg_days_on_market": None, "partial": False},
        {**month("2026-05", 22, median=Decimal("1650000"), ppsf=Decimal("820")), "partial": False},
    ]
    change = change_over_period(rows)
    assert change["median_price_change_pct"] == 10.0
    assert change["days_on_market_change"] is None


def summary_row(dom, ratio, sold=100):
    return {"sold_count": sold, "avg_days_on_market": dom, "list_to_close_pct": ratio}


def change(median_pct, sqft_pct=0.0):
    return {"from_month": "2026-04", "to_month": "2026-08", "median_price_change_pct": median_pct,
            "price_per_sqft_change_pct": sqft_pct, "days_on_market_change": 0.0}


def test_reading_san_diego_is_seller_leaning():
    """The case the model got backwards: 27.6 days at 99.5% of asking."""
    reading = market_reading("San Diego", summary_row(Decimal("27.6"), 99.5), change(7.7, -0.8))
    assert reading["speed"] == "fast"
    assert reading["negotiation"] == "near asking"
    assert reading["market_lean"] == "seller-leaning"
    assert reading["median_price_direction"] == "rising"
    assert reading["price_per_sqft_direction"] == "flat"
    assert reading["headline"] == (
        "San Diego: from Apr 2026 to Aug 2026 the median price was rising (+7.7%) and price per sq ft "
        "was flat (-0.8%); homes sell at a fast pace (27.6 days on market) for near asking "
        "(99.5% of list price), a seller-leaning market."
    )


@pytest.mark.parametrize(
    "dom,ratio,speed,negotiation,lean",
    [
        (42.1, 100.0, "moderate", "at or above asking", "balanced"),
        (75, 95.0, "slow", "below asking", "buyer-leaning"),
        (75, 98.0, "slow", "near asking", "balanced"),
        (20, 95.0, "fast", "below asking", "balanced"),
        (29.9, 101.2, "fast", "at or above asking", "seller-leaning"),
        (30, 97.0, "moderate", "near asking", "balanced"),  # thresholds: 30 isn't fast, 97 is near asking
    ],
)
def test_reading_labels(dom, ratio, speed, negotiation, lean):
    reading = market_reading("X", summary_row(dom, ratio), change(0.0))
    assert (reading["speed"], reading["negotiation"], reading["market_lean"]) == (speed, negotiation, lean)


@pytest.mark.parametrize("pct,expected", [(3.1, "rising"), (3.0, "flat"), (-3.0, "flat"), (-3.3, "falling"), (None, "unknown")])
def test_reading_price_direction_band(pct, expected):
    assert market_reading("X", summary_row(40, 99), change(pct))["median_price_direction"] == expected


def test_reading_without_enough_months_says_so():
    reading = market_reading("X", summary_row(40, 99), None)
    assert reading["median_price_direction"] == "unknown"
    assert "isn't enough monthly data" in reading["headline"]


def test_reading_with_missing_metrics_does_not_crash():
    reading = market_reading("X", summary_row(None, None), change(None, None))
    assert reading["speed"] == "unknown"
    assert reading["negotiation"] == "unknown"
    assert "n/a" in reading["headline"]


def test_no_reading_without_sales():
    assert market_reading("Nowhereville", summary_row(None, None, sold=0), None) is None
