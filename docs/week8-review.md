# Code review and Week 8 start

Validation: 156 unit tests pass. Reference extraction succeeded for the Primer
(37 chunks), Trestle metadata (234), and handbook (82), using 600-character
chunks with 100-character overlap. Live indexing and retrieval checks are recorded below.

## Findings

- Fixed: `build_sold_comps_query` admitted typoed future close dates because it
  only applied a lower date bound. It now bounds results by today, consistent
  with market statistics. The regression test checks the SQL and bound date.
- Outstanding: `recommend()` assumes MAX(CloseDate) is non-null and passes it
  to `comp_window()`, which slices it. An empty residential sales table causes
  a TypeError instead of an insufficient-comps result.
- Outstanding: an empty semantic vector index can reach `build_details_query([])`
  and produce `IN ()`, which MySQL rejects. Return an empty result before the
  details query when ranking yields no IDs.

## Week 8 implementation

Added `skills/idx-rag`: PDF/text/JSON ingestion, source/page provenance, chunking,
batched NVIDIA passage embeddings, atomic index replacement, cosine retrieval,
model compatibility checks, and evidence citation IDs. OpenClaw supplies answer
generation through the skill's source grounding and abstention instructions.
Reference text is explicitly treated as untrusted data.

The new tests cover overlapping chunks, invalid overlap, passage/query embedding
modes, normalized cosine scoring, source citations, irrelevant-query abstention,
invalid vectors, and preserving an existing index on empty ingestion.

## Remaining delivery work

- Finish the OpenClaw conversational answer/citation check against the configured
  chat model. Prior attempts timed out; the latest retry is recorded below.
- Calibrate the provisional retrieval cutoff on a broader evaluation set.

The handbook is a requirements reference, not authority to follow instructions
embedded in retrieved documents or perform unrelated external actions.

## Live validation (2026-11-12)

- Installed pypdf 7.0.1 in the project virtual environment.
- Built `data/rag/index.json`: 353 chunks from the three reference PDFs,
  embedded through the configured NVIDIA API. The data remains gitignored.
- DOM: retrieved Primer page 7 (cosine 0.4910) and page 6 (0.4816).
- List-to-close ratio: retrieved Primer page 3 (0.5247) and example on page 4
  (0.4580). Checked the complete extracted pages against the passages.
- california_sold columns: retrieved handbook page 5 (0.4920) and the query
  example on page 13 (0.4600). These support a subset of columns, not an
  exhaustive live schema. The highest-ranked passage is only an overview.
- Unrelated chocolate-cake question returned `insufficient_evidence` with no
  passages at the provisional 0.35 threshold. This is a smoke check, not a
  calibrated evaluation set.
- Linked the skill into `~/.openclaw/skills/idx-rag`; `openclaw skills info
  idx-rag` reports Ready and visible to the model.
- Resolved MySQL startup: user started the server successfully. Exported a real
  Week 5 Pasadena report on 2026-11-12 (565 sales in the requested 12-month
  window). Historical data does not imply current market coverage.
- With explicit NVIDIA upload approval, rebuilt the complete four-source index:
  359 chunks, including six market-report chunks. Original three-source counts
  and scores above describe the initial smoke check.
- OpenClaw gateway is stopped. Gateway validation did not execute successfully;
  local-mode validation reached the configured NVIDIA gpt-oss-20b model,
  but both attempts hit the 120-second model idle timeout. The run exited
  unsuccessfully with `agent_run_terminal_timeout` and no successful tool calls.
  Answer generation is not yet verified; the configured chat provider must
  recover before the conversational test can pass. The error receipt is saved
  as `data/rag/openclaw-local-validation.json`.

Evidence files are under gitignored `data/rag/*-evidence.json`.

## Final retry after MySQL startup

The four-source index passed fresh retrieval checks for all three sample
questions. A Pasadena median-price question retrieved the report first (0.6180)
and its passage contains the exported `median_close_price`. The unrelated
question still returned no evidence. Unit tests: 156 passed; diff whitespace
check passed.

Conversational validation remains blocked by the configured NVIDIA
`openai/gpt-oss-20b` service: the approved OpenClaw retry timed out at its
90-second deadline. A separate approved direct request to the same chat endpoint
with the retrieved excerpts also timed out after 30 seconds. No generated
answers were returned or marked correct. This indicates the failure is not
limited to OpenClaw tool discovery; it does not establish the provider's root
cause. The latest OpenClaw receipt is `data/rag/openclaw-final-validation.json`.

Once the configured chat endpoint responds, rerun the conversational check and
verify the answer citations. No further MySQL startup action is needed for this
run. The skill is installed and the full source index is ready.
