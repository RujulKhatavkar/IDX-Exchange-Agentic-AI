#!/usr/bin/env python3
"""One-time job: embed every active listing's description into a vector index.

Usage:
    python3 embed_listings.py              # build (or resume) the index
    python3 embed_listings.py --dry-run    # print the text for 2 listings, call nothing
    python3 embed_listings.py --rebuild    # delete the index and start over

Reads active rets_property rows that have L_Remarks, turns each into one
text (type, city, beds/baths, sqft, year, price, remarks - the handbook's
recipe), embeds them with NVIDIA in batches, and writes the index to
<repo>/data/embeddings/ (gitignored: it's derived MLS data):

    vectors.npy        float16 [N, 2048] - unit-length, one row per listing
    listing_ids.npy    L_ListingID for each row, same order
    meta.json          model, dimension, count, build time

Rows are read in id order and saved CHUNK_ROWS at a time under chunks/,
so an interrupted run (rate limit, laptop sleep) resumes after the last
saved chunk instead of re-embedding everything. The final files are
assembled from the chunks once every listing is done.
"""

import argparse
import json
import re
import shutil
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "idx-property-search"))
from db import run_query  # noqa: E402
from nvidia_embed import DIMENSION, MAX_BATCH, MODEL, embed  # noqa: E402

INDEX_DIR = Path(__file__).resolve().parents[2] / "data" / "embeddings"
CHUNK_ROWS = 1024

LISTING_TEXT_COLUMNS = """
    id, L_ListingID, L_Type_, L_City, L_Keyword2, LM_Dec_3, LM_Int2_3,
    YearBuilt, L_SystemPrice, L_Remarks
"""


def build_fetch_query(after_id: int, limit: int = CHUNK_ROWS) -> tuple[str, tuple]:
    """Next page of embeddable listings, keyset-paginated on the primary key."""
    sql = f"""
        SELECT {LISTING_TEXT_COLUMNS}
        FROM rets_property
        WHERE L_Status = 'Active' AND L_Remarks IS NOT NULL AND L_Remarks <> '' AND id > %s
        ORDER BY id
        LIMIT %s
    """
    return sql, (after_id, limit)


def readable_type(value: str | None) -> str:
    """SingleFamilyResidence -> Single Family Residence."""
    return re.sub(r"(?<=[a-z])(?=[A-Z])", " ", value) if value else "Home"


def build_listing_text(row: dict) -> str:
    """One embeddable description per listing; missing fields are left out."""
    facts = []
    if row.get("L_Keyword2") is not None:
        facts.append(f"{row['L_Keyword2']} beds")
    if row.get("LM_Dec_3") is not None:
        facts.append(f"{float(row['LM_Dec_3']):g} baths")
    if row.get("LM_Int2_3"):
        facts.append(f"{row['LM_Int2_3']:,} sq ft")
    if row.get("YearBuilt"):
        facts.append(f"built {row['YearBuilt']}")
    if row.get("L_SystemPrice"):
        facts.append(f"price ${row['L_SystemPrice']:,}")
    header = f"{readable_type(row.get('L_Type_'))} in {row.get('L_City') or 'California'}, CA."
    if facts:
        header += " " + ", ".join(facts) + "."
    remarks = " ".join((row.get("L_Remarks") or "").split())
    return f"{header} {remarks}".strip()[:8000]


def batches(items: list, size: int = MAX_BATCH):
    for start in range(0, len(items), size):
        yield items[start:start + size]


def chunk_paths() -> list[Path]:
    return sorted((INDEX_DIR / "chunks").glob("chunk_*.npz"))


def resume_point() -> int:
    """Highest rets_property.id already embedded (0 when starting fresh)."""
    paths = chunk_paths()
    if not paths:
        return 0
    with np.load(paths[-1]) as chunk:
        return int(chunk["ids"].max())


def embed_chunk(rows: list[dict]) -> np.ndarray:
    texts = [build_listing_text(row) for row in rows]
    vectors = []
    for batch in batches(texts):
        vectors.extend(embed(batch, "passage"))
    return np.asarray(vectors, dtype=np.float16)


def finalize() -> int:
    """Concatenate the chunks into the files semantic_search.py loads."""
    ids, listing_ids, vectors = [], [], []
    for path in chunk_paths():
        with np.load(path) as chunk:
            ids.append(chunk["ids"])
            listing_ids.append(chunk["listing_ids"])
            vectors.append(chunk["vectors"])
    all_vectors = np.concatenate(vectors)
    np.save(INDEX_DIR / "vectors.npy", all_vectors)
    np.save(INDEX_DIR / "listing_ids.npy", np.concatenate(listing_ids))
    (INDEX_DIR / "meta.json").write_text(json.dumps({
        "model": MODEL,
        "dimension": DIMENSION,
        "count": int(all_vectors.shape[0]),
        "built_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }, indent=2))
    return int(all_vectors.shape[0])


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--dry-run", action="store_true", help="Print the text for 2 listings and exit")
    parser.add_argument("--rebuild", action="store_true", help="Delete the existing index first")
    args = parser.parse_args()

    if args.dry_run:
        sql, params = build_fetch_query(0, 2)
        for row in run_query(sql, params):
            print(f"--- {row['L_ListingID']}\n{build_listing_text(row)}\n")
        return

    if args.rebuild and INDEX_DIR.exists():
        shutil.rmtree(INDEX_DIR)
    (INDEX_DIR / "chunks").mkdir(parents=True, exist_ok=True)

    total = run_query(
        "SELECT COUNT(*) AS n FROM rets_property WHERE L_Status = 'Active' AND L_Remarks IS NOT NULL AND L_Remarks <> ''"
    )[0]["n"]
    done = 0
    for path in chunk_paths():
        with np.load(path) as chunk:
            done += chunk["ids"].shape[0]
    after_id = resume_point()
    started, embedded_now = time.time(), 0
    print(f"{done:,} of {total:,} listings already embedded; resuming after id {after_id}", flush=True)

    while True:
        sql, params = build_fetch_query(after_id)
        rows = run_query(sql, params)
        if not rows:
            break
        vectors = embed_chunk(rows)
        ids = np.array([row["id"] for row in rows], dtype=np.int64)
        np.savez(INDEX_DIR / "chunks" / f"chunk_{ids.max():07d}.npz",
                 ids=ids, listing_ids=np.array([row["L_ListingID"] for row in rows]), vectors=vectors)
        after_id = int(ids.max())
        done += len(rows)
        embedded_now += len(rows)
        rate = (time.time() - started) / embedded_now
        print(f"{done:,}/{total:,} embedded ({done / total:.0%}), {rate * 1000:.0f} ms/listing", flush=True)

    count = finalize()
    print(f"Index written to {INDEX_DIR}: {count:,} listings x {DIMENSION} dims")


if __name__ == "__main__":
    main()
