#!/usr/bin/env python3
"""Recommend active listings similar to one the user likes.

Usage:
    python3 recommend.py similar "<listing ID or address>" [--city "<city>"] [--top N]

Prints one line of JSON:
    {"target": {...}, "comp_window": [from, to], "candidates": N, "count": N, "results": [...]}
or, when the address matches more than one listing, {"matches": [...]} to choose from.

Each candidate gets the handbook's hybrid score, out of 100:

    price        20 / 12 / 5   within $50k / $150k / $300k of the target
    beds         15            same number of bedrooms
    city         15            same city
    sqft         10 / 5        within 300 / 700 sq ft
    description  0-40          cosine similarity of the two listings' Week 6 embeddings x 40

Candidates are every other active listing of the same property type. They're
scored all at once with NumPy, and the description part is a lookup in the
Week 6 index (no API call). A missing value scores 0 for that part.

The target and every recommendation also get a `price_check` against
recent sold comps (see comps.py).

The scoring helpers are pure functions (no DB, no API) and are unit tested directly.
"""

import argparse
import json
import sys
from datetime import date
from decimal import Decimal
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "idx-semantic-search"))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "idx-property-search"))
from comps import build_comps_query, comp_window, latest_sale_query, median_ppsf, price_assessment  # noqa: E402
from db import run_query  # noqa: E402
from semantic_search import build_details_query, load_index  # noqa: E402

DEFAULT_TOP = 5
MAX_TOP = 20
MAX_MATCHES = 10
DESCRIPTION_WEIGHT = 40

PRICE_POINTS = ((50_000, 20), (150_000, 12), (300_000, 5))   # (within $, points)
SQFT_POINTS = ((300, 10), (700, 5))
BEDS_POINTS = 15
CITY_POINTS = 15

SCORING_COLUMNS = """
    L_ListingID, L_Address, L_City, L_Type_ AS type,
    L_SystemPrice AS price, L_Keyword2 AS beds, LM_Int2_3 AS sqft
"""


def build_target_query(listing: str, city: str | None = None) -> tuple[str, tuple]:
    """Find the liked listing by its MLS number, or else by (part of) its street address."""
    sql = f"SELECT {SCORING_COLUMNS} FROM rets_property WHERE L_Status = 'Active'"
    listing = listing.strip()
    if listing.isdigit():
        sql += " AND (L_ListingID = %s OR L_DisplayId = %s)"
        params = [listing, listing]
    else:
        sql += " AND L_Address LIKE %s"
        params = [f"%{listing}%"]
    if city:
        sql += " AND L_City = %s"
        params.append(city)
    sql += f" LIMIT {MAX_MATCHES + 1}"
    return sql, tuple(params)


def unique_listings(rows: list[dict]) -> list[dict]:
    """rets_property stores a few listings twice; keep the first row of each."""
    seen, unique = set(), []
    for row in rows:
        if row["L_ListingID"] not in seen:
            seen.add(row["L_ListingID"])
            unique.append(row)
    return unique


def build_candidate_query(target: dict) -> tuple[str, tuple]:
    """Every other active listing of the same property type."""
    sql = f"SELECT {SCORING_COLUMNS} FROM rets_property WHERE L_Status = 'Active' AND L_ListingID <> %s"
    params: list = [target["L_ListingID"]]
    if target.get("type"):
        sql += " AND L_Type_ = %s"
        params.append(target["type"])
    return sql, tuple(params)


def tiered_points(diffs: np.ndarray, tiers: tuple) -> np.ndarray:
    """Points for the first tier each absolute difference falls under; NaN (missing) scores 0."""
    points = np.zeros(len(diffs))
    for limit, value in reversed(tiers):   # widest tier first, narrower tiers overwrite it
        points[diffs < limit] = value
    return points


def as_numbers(values) -> np.ndarray:
    return np.array([np.nan if v is None else float(v) for v in values], dtype=float)


def structured_points(target: dict, candidates: list[dict]) -> dict[str, np.ndarray]:
    """The handbook's 60 structured points, per part, for every candidate at once."""
    def diffs(key):
        if target.get(key) is None:
            return np.full(len(candidates), np.nan)
        return np.abs(as_numbers(c[key] for c in candidates) - float(target[key]))

    beds, city = target.get("beds"), (target.get("L_City") or "").lower()
    return {
        "price": tiered_points(diffs("price"), PRICE_POINTS),
        "beds": np.array([BEDS_POINTS if beds is not None and c["beds"] == beds else 0
                          for c in candidates], dtype=float),
        "city": np.array([CITY_POINTS if city and (c["L_City"] or "").lower() == city else 0
                          for c in candidates], dtype=float),
        "sqft": tiered_points(diffs("sqft") if target.get("sqft") else np.full(len(candidates), np.nan),
                              SQFT_POINTS),
    }


def description_similarity(target_id: str, candidate_ids: list[str],
                           vectors: np.ndarray, listing_ids: np.ndarray) -> np.ndarray:
    """Cosine similarity of each candidate's description to the target's (0 when either isn't indexed)."""
    row_of: dict[str, int] = {}
    for row, listing_id in enumerate(listing_ids):
        row_of.setdefault(str(listing_id), row)
    sims = np.zeros(len(candidate_ids))
    if target_id not in row_of:
        return sims
    target_vec = np.asarray(vectors[row_of[target_id]], dtype=np.float32)
    have = [i for i, cid in enumerate(candidate_ids) if cid in row_of]
    if have:
        rows = np.array([row_of[candidate_ids[i]] for i in have])
        sims[have] = np.asarray(vectors[rows], dtype=np.float32) @ target_vec
    return np.clip(sims, 0, 1)   # an unrelated description adds nothing; it doesn't subtract


def hybrid_scores(points: dict[str, np.ndarray], description_sims: np.ndarray) -> tuple[np.ndarray, dict]:
    """Total score out of 100 and the per-part breakdown it's made of."""
    parts = {**points, "description": description_sims * DESCRIPTION_WEIGHT}
    return sum(parts.values()), parts


def top_matches(totals: np.ndarray, parts: dict, candidates: list[dict], k: int) -> list[dict]:
    """Best k candidates, one per listing and one per address (the same home listed twice)."""
    seen_ids, seen_addresses, best = set(), set(), []
    for i in np.argsort(-totals, kind="stable"):
        cand = candidates[i]
        address = ((cand.get("L_Address") or "").lower(), (cand.get("L_City") or "").lower())
        if cand["L_ListingID"] in seen_ids or (address[0] and address in seen_addresses):
            continue
        seen_ids.add(cand["L_ListingID"])
        seen_addresses.add(address)
        best.append({
            "L_ListingID": cand["L_ListingID"],
            "score": round(float(totals[i]), 1),
            "breakdown": {name: round(float(values[i]), 1) for name, values in parts.items()},
        })
        if len(best) == k:
            break
    return best


def price_check(listing: dict, window: tuple[str, str]) -> dict:
    """Comp-validated price assessment for one listing (needs a city and sq ft)."""
    if not listing.get("L_City") or not listing.get("sqft"):
        return price_assessment(listing.get("price"), None, None, 0)
    rows = run_query(*build_comps_query(listing["L_City"], listing["sqft"], listing.get("type"), window))
    return price_assessment(listing["price"], listing["sqft"], median_ppsf(rows), len(rows))


def recommend(listing: str, city: str | None = None, top: int = DEFAULT_TOP) -> dict:
    top = max(1, min(top, MAX_TOP))
    matches = unique_listings(run_query(*build_target_query(listing, city)))
    if not matches:
        return {"target": None, "matches": [], "error": f"No active listing matches {listing!r}"}
    if len(matches) > 1:
        return {"target": None, "matches": matches[:MAX_MATCHES],
                "error": "More than one listing matches - ask which one (or pass its listing ID)"}
    target = matches[0]
    latest = run_query(*latest_sale_query(date.today()))[0]["latest"]
    window = comp_window(latest)
    target["price_check"] = price_check(target, window)

    candidates = unique_listings(run_query(*build_candidate_query(target)))
    if not candidates:
        return {"target": target, "comp_window": window, "candidates": 0, "count": 0, "results": []}
    vectors, listing_ids = load_index()
    sims = description_similarity(target["L_ListingID"], [c["L_ListingID"] for c in candidates],
                                  vectors, listing_ids)
    totals, parts = hybrid_scores(structured_points(target, candidates), sims)
    ranked = top_matches(totals, parts, candidates, top)

    details = {}
    for row in run_query(*build_details_query([r["L_ListingID"] for r in ranked])):
        row["remarks_snippet"] = " ".join((row["remarks_snippet"] or "").split())
        details.setdefault(row["L_ListingID"], row)
    results = [{**r, **details[r["L_ListingID"]]} for r in ranked if r["L_ListingID"] in details]
    for result in results:
        result["price_check"] = price_check(result, window)
    return {"target": target, "comp_window": window, "candidates": len(candidates),
            "count": len(results), "results": results}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)
    similar = sub.add_parser("similar", help="Active listings most similar to one listing")
    similar.add_argument("listing", help="Listing ID, or (part of) the street address")
    similar.add_argument("--city", help="Narrow an address match to one city")
    similar.add_argument("--top", type=int, default=DEFAULT_TOP)
    args = parser.parse_args()

    output = recommend(args.listing, args.city, args.top)
    print(json.dumps(output, default=lambda v: float(v) if isinstance(v, Decimal) else str(v)))


if __name__ == "__main__":
    main()
