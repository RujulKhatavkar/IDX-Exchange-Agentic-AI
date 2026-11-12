#!/usr/bin/env python3
"""Index local reference PDFs and Week 5 JSON reports; retrieve cited evidence."""
import argparse
import json
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "idx-semantic-search"))
from nvidia_embed import MAX_BATCH, MODEL, embed

INDEX = Path(__file__).resolve().parents[2] / "data" / "rag" / "index.json"


def chunk_text(text, chunk_size=600, overlap=100):
    if not 0 <= overlap < chunk_size:
        raise ValueError("Require 0 <= overlap < chunk_size")
    text = " ".join(text.split())
    chunks = []
    for start in range(0, len(text), chunk_size - overlap):
        chunks.append(text[start:start + chunk_size])
        if start + chunk_size >= len(text):
            break
    return chunks


def read_source(path):
    """Keep page numbers and original paths for every PDF passage."""
    path = Path(path).resolve()
    if path.suffix.lower() == ".pdf":
        from pypdf import PdfReader
        pages = [(i + 1, page.extract_text() or "")
                 for i, page in enumerate(PdfReader(path).pages)]
    else:
        text = path.read_text()
        if path.suffix.lower() == ".json":
            # Week 5 command output: preserve all numbers and coverage dates.
            text = json.dumps(json.loads(text), ensure_ascii=False, indent=2)
        pages = [(None, text)]
    chunks = [{"source": path.name, "path": str(path), "page": page, "text": chunk}
              for page, text in pages for chunk in chunk_text(text)]
    if not chunks:
        raise ValueError(f"No extractable text in {path}")
    return chunks


def build_index(paths, destination=INDEX, embed_fn=embed):
    chunks = [chunk for path in paths for chunk in read_source(path)]
    if not chunks:
        raise ValueError("At least one nonempty source is required")
    vectors = []
    for start in range(0, len(chunks), MAX_BATCH):
        batch = chunks[start:start + MAX_BATCH]
        vectors.extend(embed_fn([c["text"] for c in batch], "passage"))
    matrix = np.asarray(vectors, dtype=float)
    if matrix.ndim != 2 or len(matrix) != len(chunks) or not np.isfinite(matrix).all():
        raise ValueError("Invalid document embeddings")
    if np.any(np.linalg.norm(matrix, axis=1) == 0):
        raise ValueError("Zero document embedding")
    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_suffix(".tmp")
    temporary.write_text(json.dumps({"model": MODEL, "chunks": chunks, "vectors": vectors}))
    temporary.replace(destination)
    return {"sources": len(paths), "chunks": len(chunks), "index": str(destination)}


def retrieve(query, index, top=4, min_score=0.35, embed_fn=embed):
    if not query.strip():
        raise ValueError("Question must not be empty")
    if index["model"] != MODEL:
        raise ValueError("Embedding model changed; rebuild the index")
    vectors = np.asarray(index["vectors"], dtype=float)
    q = np.asarray(embed_fn([query], "query")[0], dtype=float)
    if (vectors.ndim != 2 or len(vectors) != len(index["chunks"])
            or q.shape != (vectors.shape[1],) or not np.isfinite(vectors).all()
            or not np.isfinite(q).all() or np.linalg.norm(q) == 0
            or np.any(np.linalg.norm(vectors, axis=1) == 0)):
        raise ValueError("Invalid index or query embedding")
    scores = (vectors @ q) / (np.linalg.norm(vectors, axis=1) * np.linalg.norm(q))
    hits = [{**index["chunks"][int(i)], "citation": f"S{n + 1}", "score": round(float(scores[i]), 4)}
            for n, i in enumerate(np.argsort(-scores, kind="stable")[:max(1, min(top, 10))])
            if scores[i] >= min_score]
    return {"question": query, "status": "evidence_found" if hits else "insufficient_evidence",
            "evidence": hits}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--index", type=Path, default=INDEX)
    sub = parser.add_subparsers(dest="command", required=True)
    build = sub.add_parser("index")
    build.add_argument("sources", nargs="+", type=Path)
    ask = sub.add_parser("retrieve")
    ask.add_argument("question")
    ask.add_argument("--top", type=int, default=4)
    ask.add_argument("--min-score", type=float, default=0.35)
    args = parser.parse_args()
    if args.command == "index":
        output = build_index(args.sources, args.index)
    else:
        output = retrieve(args.question, json.loads(args.index.read_text()), args.top, args.min_score)
    print(json.dumps(output))


if __name__ == "__main__":
    main()
