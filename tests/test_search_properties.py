import sys
from datetime import date, timedelta
from pathlib import Path

import pytest

# Test the copy in this repo: skills/idx-property-search
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "skills" / "idx-property-search"))
from search_properties import (  # noqa: E402
    MAX_LIMIT,
    build_active_listing_query,
    build_sold_comps_query,
    clamp_limit,
)


def test_no_filters_still_scopes_to_active():
    sql, params = build_active_listing_query({})
    assert "WHERE L_Status = 'Active'" in sql
    assert "AND" not in sql.split("ORDER BY")[0].split("WHERE")[1]
    assert params == (10, 0)  # default limit, offset 0


def test_filters_add_parameterized_clauses_in_order():
    filters = {"city": "Irvine", "maxPrice": 1_500_000, "beds": 3, "pool": "True"}
    sql, params = build_active_listing_query(filters, page=1, limit=10)
    assert "AND L_City = %s" in sql
    assert "AND L_SystemPrice <= %s" in sql
    assert "AND L_Keyword2 >= %s" in sql
    assert "AND PoolPrivateYN = %s" in sql
    # params land in the same order the clauses are appended, then limit/offset.
    # pool "True" is translated to "1" - see test_pool_and_view_translated_to_yn_storage.
    assert params == ("Irvine", 1_500_000, 3, "1", 10, 0)


@pytest.mark.parametrize(
    "key,column,parsed_value,stored_value",
    [
        ("pool", "PoolPrivateYN", "True", "1"),
        ("pool", "PoolPrivateYN", "False", ""),
        ("hasView", "ViewYN", "True", "1"),
        ("hasView", "ViewYN", "False", ""),
    ],
)
def test_pool_and_view_translated_to_yn_storage(key, column, parsed_value, stored_value):
    """PoolPrivateYN/ViewYN store "1"/"" in the live DB, never "True"/"False"
    as the handbook's schema doc claims. Without this translation, pool/view
    filters silently match zero rows."""
    sql, params = build_active_listing_query({key: parsed_value})
    assert f"AND {column} = %s" in sql
    assert stored_value in params


def test_no_placeholder_count_mismatch():
    filters = {"city": "Irvine", "minPrice": 500_000, "maxPrice": 1_500_000, "baths": 2, "sqft": 1200, "type": "Condominium", "hasView": "True", "maxHoa": 500}
    sql, params = build_active_listing_query(filters)
    assert sql.count("%s") == len(params)


def test_pagination_offset():
    _, params_page1 = build_active_listing_query({}, page=1, limit=10)
    _, params_page3 = build_active_listing_query({}, page=3, limit=10)
    assert params_page1[-1] == 0  # offset
    assert params_page3[-1] == 20  # (3 - 1) * 10


@pytest.mark.parametrize("page", [0, -5])
def test_non_positive_page_treated_as_first_page(page):
    _, params = build_active_listing_query({}, page=page, limit=10)
    assert params[-1] == 0


@pytest.mark.parametrize("requested,expected", [(5, 5), (50, 50), (500, MAX_LIMIT), (0, 1), (-10, 1)])
def test_limit_is_clamped(requested, expected):
    assert clamp_limit(requested) == expected


def test_search_never_exceeds_max_limit():
    _, params = build_active_listing_query({}, limit=10_000)
    assert params[-2] == MAX_LIMIT  # the LIMIT value, second to last param


def test_sold_comps_query_shape_and_params():
    sql, params = build_sold_comps_query("Pasadena", months=6, limit=25)
    assert sql.count("%s") == len(params) == 3
    city, cutoff, limit = params
    assert city == "Pasadena"
    assert limit == 25
    # cutoff is an ISO date roughly `months` back, and not in the future
    cutoff_date = date.fromisoformat(cutoff)
    assert cutoff_date <= date.today()
    assert cutoff_date >= date.today() - timedelta(days=6 * 31)


def test_sold_comps_limit_is_clamped():
    _, params = build_sold_comps_query("Irvine", months=12, limit=9_999)
    assert params[-1] == MAX_LIMIT
