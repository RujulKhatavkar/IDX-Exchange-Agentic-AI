#!/usr/bin/env python3
"""Find active listings whose descriptions are closest in meaning to a free-text request.

Usage:
    python3 semantic_search.py search "<description>" [--top N] [--no-filters]

Prints one line of JSON:
    {"query": "...", "filters": {...}, "candidates": N, "count": N, "results": [...]}

Hard facts in the request (a known city, price, beds, baths, sq ft, type,
pool, view, HOA) are parsed with the Week 2 parser and applied as SQL
filters first; the rest - "charming", "craftsman", "mountain views",
"quiet cul-de-sac" - is matched by meaning. The request is embedded with
NVIDIA (input_type="query") and compared by cosine similarity against the
listing vectors built by embed_listings.py. --no-filters skips the SQL
step and ranks every indexed listing.

The query/ranking helpers are pure functions (no DB, no API) and are unit
tested directly.
"""

import argparse
import json
import sys
from decimal import Decimal
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "idx-property-search"))
from db import run_query  # noqa: E402
from embed_listings import INDEX_DIR  # noqa: E402
from nvidia_embed import embed  # noqa: E402
from parse_query import load_cities, parse_property_query  # noqa: E402
from search_properties import ACTIVE_LISTING_COLUMNS, FILTER_COLUMNS, YN_COLUMN_VALUES  # noqa: E402

DEFAULT_TOP = 5
MAX_TOP = 20
SCORE_BLOCK_ROWS = 8192   # rows converted float16 -> float32 at a time while scoring
REMARKS_SNIPPET_CHARS = 300


def hard_filters(parsed: dict, known_cities: list[str]) -> dict:
    """Keep the parser's filters, except a city that isn't a real city.

    The parser treats "near <anything>" as a city, so "near good schools"
    would become city="Good Schools" and match nothing. Descriptive phrases
    like that belong to the semantic match, not a SQL filter.
    """
    filters = {key: value for key, value in parsed.items() if value is not None}
    city = filters.get("city")
    if city and known_cities and city.lower() not in {c.lower() for c in known_cities}:
        del filters["city"]
    return filters


def build_candidate_query(filters: dict) -> tuple[str, tuple]:
    """L_ListingIDs of active listings matching the hard filters.

    Reuses search_properties.py's filter -> column mapping, so a filter
    means exactly the same thing here as in the Week 3 search.
    """
    sql = "SELECT DISTINCT L_ListingID FROM rets_property WHERE L_Status = 'Active'"
    params: list = []
    for key, (column, op) in FILTER_COLUMNS.items():
        value = filters.get(key)
        if value is None:
            continue
        if key in ("pool", "hasView"):
            value = YN_COLUMN_VALUES.get(value, value)
        sql += f" AND {column} {op} %s"
        params.append(value)
    return sql, tuple(params)


def score(vectors: np.ndarray, query: np.ndarray, rows: np.ndarray | None = None) -> np.ndarray:
    """Cosine similarity of `query` against `vectors` (or just `rows` of it).

    Both sides are unit length, so cosine similarity is the dot product.
    Scored in blocks so a float16 memory-mapped index is never copied to
    float32 all at once.
    """
    rows = np.arange(len(vectors)) if rows is None else rows
    query = query.astype(np.float32)
    out = np.empty(len(rows), dtype=np.float32)
    for start in range(0, len(rows), SCORE_BLOCK_ROWS):
        block = rows[start:start + SCORE_BLOCK_ROWS]
        out[start:start + len(block)] = np.asarray(vectors[block], dtype=np.float32) @ query
    return out


def top_unique(scores: np.ndarray, listing_ids: np.ndarray, k: int) -> list[tuple[str, float]]:
    """Best k (listing_id, score) pairs, highest first, one entry per listing.

    rets_property has a few exact duplicate rows (same L_ListingID twice),
    so the same listing can hold two adjacent ranks; keep only its first.
    """
    seen: set[str] = set()
    best: list[tuple[str, float]] = []
    for index in np.argsort(-scores, kind="stable"):
        listing_id = str(listing_ids[index])
        if listing_id in seen:
            continue
        seen.add(listing_id)
        best.append((listing_id, round(float(scores[index]), 4)))
        if len(best) == k:
            break
    return best


def build_details_query(listing_ids: list[str]) -> tuple[str, tuple]:
    placeholders = ", ".join(["%s"] * len(listing_ids))
    sql = f"""
        SELECT {ACTIVE_LISTING_COLUMNS}, LEFT(L_Remarks, {REMARKS_SNIPPET_CHARS}) AS remarks_snippet
        FROM rets_property
        WHERE L_Status = 'Active' AND L_ListingID IN ({placeholders})
    """
    return sql, tuple(listing_ids)


def load_index() -> tuple[np.ndarray, np.ndarray]:
    if not (INDEX_DIR / "vectors.npy").exists():
        raise SystemExit(f"No vector index at {INDEX_DIR} - run embed_listings.py first")
    vectors = np.load(INDEX_DIR / "vectors.npy", mmap_mode="r")
    listing_ids = np.load(INDEX_DIR / "listing_ids.npy")
    return vectors, listing_ids


def semantic_search(query: str, top: int = DEFAULT_TOP, use_filters: bool = True) -> dict:
    top = max(1, min(top, MAX_TOP))
    vectors, listing_ids = load_index()

    filters = hard_filters(parse_property_query(query), load_cities()) if use_filters else {}
    rows = None
    if filters:
        sql, params = build_candidate_query(filters)
        allowed = [row["L_ListingID"] for row in run_query(sql, params)]
        rows = np.flatnonzero(np.isin(listing_ids, allowed))
        if len(rows) == 0:
            return {"query": query, "filters": filters, "candidates": 0, "count": 0, "results": []}

    query_vector = np.asarray(embed([query], "query")[0], dtype=np.float32)
    scores = score(vectors, query_vector, rows)
    candidate_ids = listing_ids if rows is None else listing_ids[rows]
    ranked = top_unique(scores, candidate_ids, top)

    sql, params = build_details_query([listing_id for listing_id, _ in ranked])
    details = {}
    for row in run_query(sql, params):
        row["remarks_snippet"] = " ".join((row["remarks_snippet"] or "").split())  # MLS text has \r\r\n breaks
        details.setdefault(row["L_ListingID"], row)
    results = [
        {"similarity": similarity, **details[listing_id]}
        for listing_id, similarity in ranked
        if listing_id in details  # a listing that went inactive since indexing drops out
    ]
    return {
        "query": query,
        "filters": filters,
        "candidates": int(len(candidate_ids)),
        "count": len(results),
        "results": results,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)
    search = sub.add_parser("search", help="Top listings closest in meaning to a description")
    search.add_argument("query")
    search.add_argument("--top", type=int, default=DEFAULT_TOP)
    search.add_argument("--no-filters", action="store_true", help="Rank every listing; don't apply parsed filters")
    args = parser.parse_args()

    output = semantic_search(args.query, args.top, use_filters=not args.no_filters)
    print(json.dumps(output, default=lambda v: float(v) if isinstance(v, Decimal) else str(v)))


if __name__ == "__main__":
    main()
