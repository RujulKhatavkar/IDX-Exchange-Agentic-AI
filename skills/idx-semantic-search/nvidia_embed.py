"""Minimal client for NVIDIA's hosted embedding API (OpenAI-compatible).

Uses only the standard library, so neither the repo venv nor the OpenClaw
gateway's Python needs the openai or requests packages. The key comes from
NVIDIA_API_KEY in the repo-root .env (loaded the same way db.py loads the
MySQL credentials) or the environment.

nemotron-3.5-embed-1b returns 2048-dim vectors already normalized to unit
length, so cosine similarity is a plain dot product. It is asymmetric:
listings are embedded with input_type="passage" and searches with
input_type="query" - mixing the two up degrades ranking.
"""

import json
import os
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "idx-property-search"))
from db import _load_env_file  # noqa: E402

API_URL = "https://integrate.api.nvidia.com/v1/embeddings"
MODEL = "nvidia/nemotron-3.5-embed-1b"
DIMENSION = 2048
MAX_BATCH = 256          # largest batch verified against the live API
MAX_RETRIES = 6
RETRY_STATUSES = {429, 500, 502, 503, 504}


def build_request_body(texts: list[str], input_type: str) -> dict:
    if input_type not in ("passage", "query"):
        raise ValueError(f"input_type must be 'passage' or 'query', not {input_type!r}")
    if not texts or len(texts) > MAX_BATCH:
        raise ValueError(f"send between 1 and {MAX_BATCH} texts per request, not {len(texts)}")
    # truncate=END: the model reads at most ~512 tokens; cut long remarks
    # at the end rather than failing the whole batch.
    return {"model": MODEL, "input": texts, "input_type": input_type,
            "encoding_format": "float", "truncate": "END"}


def parse_response(payload: dict, expected: int) -> list[list[float]]:
    """Return embeddings in input order (sorted by each item's index)."""
    data = sorted(payload["data"], key=lambda item: item.get("index", 0))
    if len(data) != expected:
        raise ValueError(f"asked for {expected} embeddings, got {len(data)}")
    return [item["embedding"] for item in data]


def _api_key() -> str:
    _load_env_file()
    key = os.environ.get("NVIDIA_API_KEY", "").strip()
    if not key:
        raise RuntimeError("NVIDIA_API_KEY is not set - add it to the repo-root .env")
    return key


def embed(texts: list[str], input_type: str) -> list[list[float]]:
    """Embed up to MAX_BATCH texts, retrying rate limits and server errors with backoff."""
    body = json.dumps(build_request_body(texts, input_type)).encode()
    headers = {"Authorization": f"Bearer {_api_key()}", "Content-Type": "application/json"}
    for attempt in range(MAX_RETRIES):
        request = urllib.request.Request(API_URL, data=body, headers=headers)
        try:
            with urllib.request.urlopen(request, timeout=120) as response:
                return parse_response(json.load(response), len(texts))
        except urllib.error.HTTPError as error:
            if error.code not in RETRY_STATUSES or attempt == MAX_RETRIES - 1:
                raise RuntimeError(f"NVIDIA embeddings HTTP {error.code}: {error.read().decode()[:300]}") from None
            wait = float(error.headers.get("Retry-After") or 2 ** attempt)
        except (urllib.error.URLError, TimeoutError):
            if attempt == MAX_RETRIES - 1:
                raise
            wait = 2 ** attempt
        time.sleep(wait)
    raise AssertionError("unreachable")
