import sys
from decimal import Decimal
from pathlib import Path

import numpy as np
import pytest

# Test the copy in this repo: skills/idx-semantic-search
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "skills" / "idx-semantic-search"))
import semantic_search  # noqa: E402
from embed_listings import batches, build_fetch_query, build_listing_text, readable_type  # noqa: E402
from nvidia_embed import MAX_BATCH, MODEL, build_request_body, parse_response  # noqa: E402
from semantic_search import (  # noqa: E402
    build_candidate_query,
    build_details_query,
    hard_filters,
    score,
    top_unique,
)

# --- nvidia_embed: request/response shape -------------------------------------


@pytest.mark.parametrize("input_type", ["passage", "query"])
def test_request_body(input_type):
    body = build_request_body(["a", "b"], input_type)
    assert body == {"model": MODEL, "input": ["a", "b"], "input_type": input_type,
                    "encoding_format": "float", "truncate": "END"}


def test_request_body_rejects_unknown_input_type():
    """passage vs query changes the vectors; a typo must fail loudly, not silently mis-embed."""
    with pytest.raises(ValueError):
        build_request_body(["a"], "document")


@pytest.mark.parametrize("count", [0, MAX_BATCH + 1])
def test_request_body_rejects_bad_batch_sizes(count):
    with pytest.raises(ValueError):
        build_request_body(["x"] * count, "passage")


def test_parse_response_restores_input_order():
    payload = {"data": [{"index": 1, "embedding": [0.0, 1.0]}, {"index": 0, "embedding": [1.0, 0.0]}]}
    assert parse_response(payload, 2) == [[1.0, 0.0], [0.0, 1.0]]


def test_parse_response_rejects_missing_embeddings():
    with pytest.raises(ValueError):
        parse_response({"data": [{"index": 0, "embedding": [1.0]}]}, 2)


# --- embed_listings: what gets embedded ---------------------------------------

FULL_ROW = {
    "L_Type_": "SingleFamilyResidence", "L_City": "Lathrop", "L_Keyword2": 4,
    "LM_Dec_3": Decimal("3.0"), "LM_Int2_3": 2709, "YearBuilt": 2016,
    "L_SystemPrice": 775000, "L_Remarks": "Step into comfort.\n\nOpen  layout.",
}


def test_listing_text_full_row():
    assert build_listing_text(FULL_ROW) == (
        "Single Family Residence in Lathrop, CA. 4 beds, 3 baths, 2,709 sq ft, built 2016, "
        "price $775,000. Step into comfort. Open layout."
    )


def test_listing_text_leaves_out_missing_fields():
    row = {"L_Type_": None, "L_City": None, "L_Remarks": "Cozy cabin."}
    assert build_listing_text(row) == "Home in California, CA. Cozy cabin."


def test_listing_text_half_baths_and_cap():
    row = {**FULL_ROW, "LM_Dec_3": Decimal("2.5"), "L_Remarks": "x" * 20000}
    text = build_listing_text(row)
    assert "2.5 baths" in text
    assert len(text) == 8000


@pytest.mark.parametrize("raw,readable", [
    ("SingleFamilyResidence", "Single Family Residence"),
    ("Condominium", "Condominium"),
    ("ManufacturedOnLand", "Manufactured On Land"),
    (None, "Home"),
])
def test_readable_type(raw, readable):
    assert readable_type(raw) == readable


def test_fetch_query_is_keyset_paginated_on_active_listings_with_remarks():
    sql, params = build_fetch_query(after_id=319905, limit=1024)
    assert "L_Status = 'Active'" in sql
    assert "L_Remarks <> ''" in sql
    assert "id > %s" in sql and "ORDER BY id" in sql
    assert params == (319905, 1024)


def test_batches_cover_everything_in_order():
    chunks = list(batches(list(range(600)), 256))
    assert [len(c) for c in chunks] == [256, 256, 88]
    assert sum(chunks, []) == list(range(600))


# --- semantic_search: filters ---------------------------------------------------

CITIES = ["Pasadena", "Newport Beach", "Malibu"]


def test_hard_filters_drop_a_city_that_is_not_a_city():
    """The Week 2 parser reads "near good schools" as city="Good Schools"."""
    parsed = {"city": "Good Schools", "maxPrice": 1_500_000, "beds": None}
    assert hard_filters(parsed, CITIES) == {"maxPrice": 1_500_000}


def test_hard_filters_keep_a_real_city_case_insensitively():
    assert hard_filters({"city": "pasadena", "pool": "True"}, CITIES) == {"city": "pasadena", "pool": "True"}


def test_hard_filters_keep_city_when_no_city_list():
    """Without cities.txt there's nothing to check against - trust the parser."""
    assert hard_filters({"city": "Pasadena"}, []) == {"city": "Pasadena"}


def test_candidate_query_matches_week3_filters():
    sql, params = build_candidate_query({"city": "Pasadena", "maxPrice": 1_500_000, "hasView": "True"})
    assert sql.startswith("SELECT DISTINCT L_ListingID FROM rets_property WHERE L_Status = 'Active'")
    assert "AND L_City = %s" in sql
    assert "AND L_SystemPrice <= %s" in sql
    assert "AND ViewYN = %s" in sql
    assert params == ("Pasadena", 1_500_000, "1")  # "True" stored as "1", as in Week 3
    assert sql.count("%s") == len(params)


def test_candidate_query_never_inlines_user_values():
    city = "Pasadena'; DROP TABLE rets_property; --"
    sql, params = build_candidate_query({"city": city})
    assert "DROP TABLE" not in sql
    assert params == (city,)


def test_details_query_has_one_placeholder_per_listing():
    sql, params = build_details_query(["1", "2", "3"])
    assert "IN (%s, %s, %s)" in sql
    assert params == ("1", "2", "3")
    assert "L_Status = 'Active'" in sql


# --- semantic_search: ranking ---------------------------------------------------


def unit(*values):
    v = np.array(values, dtype=np.float32)
    return v / np.linalg.norm(v)


VECTORS = np.array([unit(1, 0), unit(0, 1), unit(1, 1), unit(-1, 0)], dtype=np.float16)


def test_score_is_cosine_similarity():
    scores = score(VECTORS, unit(1, 0))
    assert scores == pytest.approx([1.0, 0.0, 0.7071, -1.0], abs=1e-3)


def test_score_only_the_requested_rows():
    scores = score(VECTORS, unit(1, 0), rows=np.array([3, 0]))
    assert scores == pytest.approx([-1.0, 1.0], abs=1e-3)


def test_score_blocks_give_the_same_answer(monkeypatch):
    rng = np.random.default_rng(0)
    vectors = rng.normal(size=(1000, 16)).astype(np.float16)
    query = rng.normal(size=16).astype(np.float32)
    expected = vectors.astype(np.float32) @ query
    monkeypatch.setattr(semantic_search, "SCORE_BLOCK_ROWS", 64)  # 16 blocks, last one partial
    assert score(vectors, query) == pytest.approx(expected, rel=1e-4, abs=1e-4)


def test_top_unique_orders_by_score_and_skips_duplicate_listings():
    """rets_property has 3 exact duplicate rows; one listing must not take two of the top 5."""
    scores = np.array([0.2, 0.9, 0.9, 0.5, 0.7])
    ids = np.array(["a", "dup", "dup", "b", "c"])
    assert top_unique(scores, ids, 3) == [("dup", 0.9), ("c", 0.7), ("b", 0.5)]


def test_top_unique_with_fewer_listings_than_k():
    assert top_unique(np.array([0.1, 0.3]), np.array(["a", "b"]), 5) == [("b", 0.3), ("a", 0.1)]
