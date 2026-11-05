import sys
from decimal import Decimal
from pathlib import Path

import numpy as np
import pytest

# Test the copy in this repo: skills/idx-recommendations
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "skills" / "idx-recommendations"))
from comps import (  # noqa: E402
    build_comps_query,
    comp_window,
    latest_sale_query,
    median_ppsf,
    price_assessment,
)
from recommend import (  # noqa: E402
    PRICE_POINTS,
    build_candidate_query,
    build_target_query,
    description_similarity,
    hybrid_scores,
    structured_points,
    tiered_points,
    top_matches,
    unique_listings,
)

# --- finding the liked listing ------------------------------------------------


def test_target_by_listing_id_checks_both_id_columns():
    sql, params = build_target_query(" 1176236993 ")
    assert "(L_ListingID = %s OR L_DisplayId = %s)" in sql
    assert params == ("1176236993", "1176236993")


def test_target_by_address_is_a_partial_match():
    sql, params = build_target_query("380 Dearborn")
    assert "L_Address LIKE %s" in sql
    assert params == ("%380 Dearborn%",)


def test_target_city_narrows_an_address():
    sql, params = build_target_query("Main Street", city="Hesperia")
    assert "AND L_City = %s" in sql
    assert params == ("%Main Street%", "Hesperia")


def test_target_query_fetches_one_more_than_it_shows():
    """11 rows tells us there are more than 10 matches, without loading thousands."""
    sql, _ = build_target_query("Main Street")
    assert "L_Status = 'Active'" in sql
    assert sql.endswith("LIMIT 11")


def test_target_query_never_inlines_user_values():
    sql, params = build_target_query("1 Main'; DROP TABLE rets_property; --")
    assert "DROP TABLE" not in sql
    assert params == ("%1 Main'; DROP TABLE rets_property; --%",)


def test_unique_listings_drops_repeated_rows():
    rows = [{"L_ListingID": "a", "n": 1}, {"L_ListingID": "a", "n": 2}, {"L_ListingID": "b", "n": 3}]
    assert unique_listings(rows) == [{"L_ListingID": "a", "n": 1}, {"L_ListingID": "b", "n": 3}]


def test_candidates_are_other_listings_of_the_same_type():
    sql, params = build_candidate_query({"L_ListingID": "42", "type": "Condominium"})
    assert "L_ListingID <> %s" in sql and "AND L_Type_ = %s" in sql
    assert params == ("42", "Condominium")


def test_candidates_without_a_type_are_not_filtered_by_type():
    sql, params = build_candidate_query({"L_ListingID": "42", "type": None})
    assert "L_Type_ = %s" not in sql
    assert params == ("42",)


# --- structured points (handbook: 60 of the 100) ------------------------------


@pytest.mark.parametrize("diff,points", [
    (0, 20), (49_999, 20), (50_000, 12), (149_999, 12), (150_000, 5), (299_999, 5), (300_000, 0),
    (np.nan, 0),  # missing price
])
def test_price_tiers_match_the_handbook(diff, points):
    assert tiered_points(np.array([diff], dtype=float), PRICE_POINTS)[0] == points


TARGET = {"L_ListingID": "t", "L_City": "Pasadena", "price": 1_015_000, "beds": 2, "sqft": 1244}


def cand(listing_id, city="Pasadena", price=1_000_000, beds=2, sqft=1250, address=None):
    return {"L_ListingID": listing_id, "L_City": city, "price": price, "beds": beds, "sqft": sqft,
            "L_Address": address or f"{listing_id} Main St"}


def test_structured_points_per_part():
    candidates = [
        cand("same"),                                                     # everything matches
        cand("far", city="pasadena", price=1_200_000, beds=3, sqft=2000),  # $185k, 756 sq ft apart; city matches (any case)
        cand("mid", city="Altadena", price=1_150_000, sqft=1700),          # $135k, 456 sq ft apart
    ]
    points = structured_points(TARGET, candidates)
    assert points["price"].tolist() == [20, 5, 12]
    assert points["beds"].tolist() == [15, 0, 15]
    assert points["city"].tolist() == [15, 15, 0]
    assert points["sqft"].tolist() == [10, 0, 5]


def test_missing_values_score_zero_instead_of_crashing():
    candidates = [cand("no-data", city=None, price=None, beds=None, sqft=None)]
    points = structured_points(TARGET, candidates)
    assert all(values.tolist() == [0] for values in points.values())

    target_without_sqft = {**TARGET, "sqft": None, "beds": None}
    points = structured_points(target_without_sqft, [cand("c")])
    assert points["sqft"].tolist() == [0] and points["beds"].tolist() == [0]


def test_bed_counts_compare_across_number_types():
    """MySQL returns beds as int; Decimal or float must still count as the same number."""
    points = structured_points({**TARGET, "beds": Decimal("2")}, [cand("c", beds=2)])
    assert points["beds"].tolist() == [15]


# --- description similarity (handbook: 40 of the 100) -------------------------


def unit(*values):
    v = np.array(values, dtype=np.float32)
    return v / np.linalg.norm(v)


VECTORS = np.array([unit(1, 0), unit(1, 1), unit(-1, 0), unit(0, 1), unit(0, 1)], dtype=np.float16)
INDEX_IDS = np.array(["t", "close", "opposite", "dup", "dup"])


def test_description_similarity_is_cosine_from_the_index():
    sims = description_similarity("t", ["close", "dup"], VECTORS, INDEX_IDS)
    assert sims == pytest.approx([0.7071, 0.0], abs=1e-3)


def test_opposite_descriptions_add_nothing_rather_than_subtract():
    assert description_similarity("t", ["opposite"], VECTORS, INDEX_IDS).tolist() == [0]


def test_listings_missing_from_the_index_get_zero():
    """No description, or listed after the index was built."""
    assert description_similarity("t", ["new-listing"], VECTORS, INDEX_IDS).tolist() == [0]
    assert description_similarity("not-indexed", ["close"], VECTORS, INDEX_IDS).tolist() == [0]


def test_perfect_match_scores_100():
    points = structured_points(TARGET, [cand("same")])
    totals, parts = hybrid_scores(points, np.array([1.0]))
    assert totals.tolist() == [100]
    assert parts["description"].tolist() == [40]


# --- ranking ------------------------------------------------------------------


def test_top_matches_rank_by_score_and_breakdown_adds_up():
    candidates = [cand("a"), cand("b", price=1_400_000), cand("c")]
    totals, parts = hybrid_scores(structured_points(TARGET, candidates), np.array([0.5, 0.9, 0.8]))
    best = top_matches(totals, parts, candidates, 3)
    assert [b["L_ListingID"] for b in best] == ["c", "a", "b"]
    for b in best:
        assert sum(b["breakdown"].values()) == pytest.approx(b["score"], abs=0.2)


def test_top_matches_skip_the_same_home_listed_twice():
    candidates = [cand("a", address="1 Elm St"), cand("a", address="1 Elm St"),
                  cand("a2", address="1 ELM ST"), cand("b", address="2 Oak Ave")]
    totals, parts = hybrid_scores(structured_points(TARGET, candidates), np.array([0.9, 0.9, 0.8, 0.1]))
    assert [b["L_ListingID"] for b in top_matches(totals, parts, candidates, 5)] == ["a", "b"]


# --- comp validation ----------------------------------------------------------


def test_comp_window_ends_at_the_latest_sale():
    assert comp_window("2026-09-17") == ("2026-03-21", "2026-09-17")


def test_latest_sale_never_goes_past_today():
    """A few close dates are typos in the future (2028, 2072)."""
    from datetime import date
    sql, params = latest_sale_query(date(2026, 11, 5))
    assert "CloseDate <= %s" in sql
    assert params == ("2026-11-05",)


def test_comps_query_matches_city_type_and_size_band():
    sql, params = build_comps_query("Pasadena", 1244, "SingleFamilyResidence", ("2026-03-21", "2026-09-17"))
    assert "PropertyType = 'Residential'" in sql and "ClosePrice > 0" in sql
    assert "LivingArea BETWEEN %s AND %s" in sql and "CloseDate BETWEEN %s AND %s" in sql
    assert "AND PropertySubType = %s" in sql
    assert params == ("Pasadena", 995, 1493, "2026-03-21", "2026-09-17", "SingleFamilyResidence")
    assert sql.count("%s") == len(params)


def test_comps_query_without_a_type():
    sql, params = build_comps_query("Pasadena", 1000, None, ("2026-03-21", "2026-09-17"))
    assert "PropertySubType" not in sql
    assert len(params) == 5


def test_median_ppsf_ignores_one_expensive_outlier():
    rows = [{"ppsf": Decimal("700")}, {"ppsf": Decimal("750")}, {"ppsf": Decimal("800")}, {"ppsf": Decimal("5000")}]
    assert median_ppsf(rows) == 775.0
    assert median_ppsf([]) is None


@pytest.mark.parametrize("price,verdict", [
    (1_100_000, "priced 10.0% above recent comps"),
    (900_000, "priced 10.0% below recent comps"),
    (1_050_000, "in line with recent comps"),    # exactly +5% is still in line
    (946_000, "priced 5.4% below recent comps"),  # just past the band: one decimal, not "5%"
])
def test_price_verdicts(price, verdict):
    result = price_assessment(price, 1000, Decimal("1000"), 40)   # comp price $1,000,000
    assert result["comp_price"] == 1_000_000
    assert result["verdict"] == verdict


def test_price_assessment_numbers():
    assert price_assessment(1_015_000, 1244, 948.65, 78) == {
        "list_price": 1_015_000, "comp_count": 78, "median_comp_ppsf": 949,
        "comp_price": 1_180_121, "delta_pct": -14.0, "verdict": "priced 14.0% below recent comps",
    }


def test_too_few_comps_gives_no_verdict():
    result = price_assessment(1_000_000, 1000, 1000, 2)
    assert result["verdict"] == "not enough recent comps to judge the price"
    assert result["comp_price"] is None and result["delta_pct"] is None


def test_no_sqft_gives_no_verdict():
    assert price_assessment(1_000_000, None, 1000, 40)["verdict"].startswith("no sq ft listed")
