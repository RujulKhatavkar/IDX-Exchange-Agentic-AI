# IDX Exchange: Agentic AI Track

A multi agent real estate assistant built on [OpenClaw](https://github.com/openclaw/openclaw). Users ask questions over WhatsApp; the agent searches active MLS listings (`rets_property`), analyzes sold comps (`california_sold`), and answers real estate questions.

## Progress

| Week | Module | Status |
| --- | --- | --- |
| 0 | Environment setup: OpenClaw, MySQL import | Done |
| 1 | Architecture (see `docs/`) | Done |
| 2 | Natural language property search parser | Done |
| 3 | MySQL query layer | Done |
| 4 | Conversational multi-turn search | Done |
| 5 | Market statistics agent | Done |
| 6 | Embeddings and semantic search | Done |

## Repository layout

```
db/
  indexes.sql           Secondary + FULLTEXT indexes missing from the source dumps
skills/
  idx-property-search/
    SKILL.md            OpenClaw skill: when and how the agent searches
    parse_query.py      Free text query -> structured rets_property filters
    search_properties.py  Filters -> parameterized SQL -> rets_property / california_sold
    db.py               Connection pool + query execution
  idx-market-stats/
    SKILL.md            OpenClaw skill: when and how the agent answers market questions
    market_stats.py     california_sold -> city summary, monthly trend, market reading
  idx-semantic-search/
    SKILL.md            OpenClaw skill: when and how the agent searches by description
    nvidia_embed.py     NVIDIA embeddings API client (standard library only)
    embed_listings.py   One-time job: listing descriptions -> data/embeddings/ vector index
    semantic_search.py  Hard filters in SQL, then rank by meaning (cosine similarity)
tests/
  test_parse_query.py      17 parser test queries
  test_search_properties.py  Query-builder tests (SQL shape, params, pagination, limit clamp)
  test_market_stats.py     Market query builders, partial months, period change, reading labels
  test_semantic_search.py  Embedding request/text, filters, scoring, duplicate handling
docs/                   Architecture notes
```

## Week 2: query parser

Turns a message like *"3 bed condos in Irvine under $1.5M with a pool"* into:

```json
{"city": "Irvine", "maxPrice": 1500000, "beds": 3, "type": "Condominium", "pool": "True"}
```

Supported filters: city, min and max price, beds, baths, square feet, property type (matched to real `L_Type_` values), pool, view, and max HOA. Prices understand `900k`, `$1.5M`, `between 800k and 1.2M`, and `budget of $650,000`.

City matching uses `cities.txt`, exported from the database and kept out of the repo:

```bash
mysql -u root -p idx_exchange -N -e "SELECT DISTINCT L_City FROM rets_property WHERE L_City <> '' ORDER BY L_City;" > skills/idx-property-search/cities.txt
```

## Week 3: MySQL query layer

`search_properties.py` turns parser filters into a parameterized SQL query and runs it:

```bash
python3 skills/idx-property-search/search_properties.py search "3 bed condos in Irvine under $1.5M with a pool" --page 1 --limit 10
python3 skills/idx-property-search/search_properties.py comps --city "Irvine" --months 12
```

Both query builders (`build_active_listing_query`, `build_sold_comps_query`) are plain
functions with no DB access, so they're unit tested directly - no live database needed to
run `pytest`. Results are capped at 50 rows per query.

The source SQL dumps (`rets_property.sql`, `california_sold.sql`) define no indexes at all -
not even a primary key - so run `db/indexes.sql` once after importing both tables, or every
search is a full table scan over 500k+ rows:

```bash
mysql -u root -p idx_exchange < db/indexes.sql
```

## Week 4: multi-turn conversation

The handbook's Week 4 design is a long-running Node process holding a `Map<string,
UserSession>` per WhatsApp user. That doesn't fit here: `search_properties.py` runs fresh per
message (no persistent process to hold a map in), and OpenClaw's agent is a conversational LLM
that already has the full chat history in context - it doesn't need an external store to
remember what the user said two messages ago.

So instead: `merge_filters(known, new)` in `parse_query.py` is a pure function that layers a
new turn's parsed filters onto whatever was already established, and the calling agent (per
`SKILL.md`) is responsible for holding the filters JSON and passing it back in via `--known`
each turn:

```bash
python3 skills/idx-property-search/search_properties.py search "Find homes in Irvine"
# => filters: {"city": "Irvine"}
python3 skills/idx-property-search/search_properties.py search "under \$1.2M" --known '{"city":"Irvine"}'
# => filters: {"city": "Irvine", "maxPrice": 1200000}
python3 skills/idx-property-search/search_properties.py search "single family with at least 3 beds" --known '{"city":"Irvine","maxPrice":1200000}'
# => filters: {"city": "Irvine", "maxPrice": 1200000, "type": "SingleFamilyResidence", "beds": 3}
```

A new message's values always override `--known` on the same key (how a correction like
"actually 4 beds" takes effect); keys it doesn't mention carry forward unchanged.

## Week 5: market statistics

A second skill, `idx-market-stats`, answers market questions ("Is now a good time to buy in
San Diego?") from `california_sold`. One command returns everything the agent needs:

```bash
python3 skills/idx-market-stats/market_stats.py overview --city "Los Angeles" [--zip 90026] [--type Condominium]
```

- `summary`: sold count, median and average close price, price per sq ft, days on market,
  list-to-close ratio over the last 12 months.
- `trend`: the same per month, with month-over-month change.
- `change_over_period`: first full month vs. last full month.
- `reading`: those numbers turned into fixed labels (price direction, speed, negotiation room,
  seller-/buyer-leaning/balanced) plus a one-sentence `headline` the agent quotes.

Where this differs from the handbook, and why:

- **Median** - MySQL has no `MEDIAN()`, so the queries rank sales with `ROW_NUMBER()` and
  average the middle one or two rows. Checked against Python's `statistics.median`.
- **No pandas** - the handbook computes `pct_change` with pandas; here it's a few lines of
  Python, so the OpenClaw gateway's Python doesn't need pandas installed.
- **Bad dates** - a few sales have typo'd future close dates (2028, 2030, 2072), so every
  window has an upper bound of today, not just a lower one.
- **Partial months** - the dump covers 18 Mar to 17 Sep 2026, so those months have about half
  the sales days of the rest. `days_covered` flags them as `partial`, and the period change
  skips them; otherwise September's half month reads as a price drop. With six months of
  data, year-over-year comparisons aren't possible.
- **The reading is computed, not prompted** - with thresholds only written in `SKILL.md`, a
  small model (gpt-oss-20b) skipped the trend, invented comparisons ("higher than the state
  average"), and called a 28-day, 99.5%-of-asking market "buyer-friendly". `market_reading()`
  applies the thresholds in code, `summary` carries the reading too, and `SKILL.md` tells the
  agent to quote the headline and never contradict it.

## Week 6: embeddings and semantic search

A third skill, `idx-semantic-search`, finds listings by what they're *like* rather than by
numbers alone: *"charming craftsman with mountain views"*, *"quiet cul-de-sac near good
schools"*.

**Build the index once** (about 45 minutes for 54,891 active listings with a description):

```bash
python3 skills/idx-semantic-search/embed_listings.py --dry-run   # preview the text for 2 listings
python3 skills/idx-semantic-search/embed_listings.py             # build, or resume after an interruption
```

Each listing becomes one text - type, city, beds, baths, sq ft, year, price, then the
agent's description - embedded with NVIDIA's `nvidia/nemotron-3.5-embed-1b` (2048
dimensions). Vectors are saved as float16 in `data/embeddings/` (about 214 MB, gitignored:
it's derived MLS data). The job saves every 1,024 listings, so a rate limit or a closed laptop
lid only loses the current chunk.

**Search:**

```bash
python3 skills/idx-semantic-search/semantic_search.py search "charming craftsman in Pasadena under \$1.5M"
```

1. The Week 2 parser pulls out hard facts (city, price, beds, pool, view...) and they're
   applied as SQL filters, using the same column mapping as Week 3.
2. The request is embedded (`input_type: "query"`; listings use `"passage"`).
3. Remaining listings are ranked by cosine similarity - a dot product, since the vectors are
   unit length.
4. The top 5 come back with listing details and the start of the description, so the agent
   can quote why each one matched.

About 0.6 s per search once the index is in memory.

Where this differs from the handbook, and why:

- **NVIDIA instead of OpenAI embeddings** - free tier, and the chat model already runs on
  NVIDIA, so one key covers both.
- **A NumPy file instead of a vector database** - 55k vectors fit in memory and score in
  0.2 s, so a vector DB would add a service to run without making search noticeably faster.
- **Hybrid, not pure vector** - "under $1.5M" or "in Pasadena" must be exact, which
  similarity can't guarantee, so hard facts are SQL filters and only the rest is matched by
  meaning.
- **"near good schools" isn't a city** - the Week 2 parser reads "near X" as a city, which
  would match nothing. A city is only used as a filter if it's in `cities.txt`.
- **Duplicates** - `rets_property` has 3 listings stored twice; results skip repeats so one
  home can't take two of the top 5 spots.

## Setup

Requires Node 24.16+ (for OpenClaw), Python 3.10+, and MySQL 8.4.

```bash
npm install -g openclaw@latest
openclaw onboard --install-daemon
pip install -r requirements.txt
python3 -m pytest tests/ -v
```

Link the skills into OpenClaw so edits in this repo apply directly:

```bash
ln -s "$PWD/skills/idx-property-search" ~/.openclaw/skills/idx-property-search
ln -s "$PWD/skills/idx-market-stats" ~/.openclaw/skills/idx-market-stats
ln -s "$PWD/skills/idx-semantic-search" ~/.openclaw/skills/idx-semantic-search
openclaw gateway restart
```

The gateway runs the skills with whatever `python3` is first on its own PATH (on macOS with
Homebrew, `/opt/homebrew/bin/python3`), not this repo's venv - that Python needs
`mysql-connector-python` and `numpy` too:

```bash
/opt/homebrew/bin/python3 -m pip install --user --break-system-packages mysql-connector-python numpy
```

Semantic search needs `NVIDIA_API_KEY` (an `nvapi-...` key from build.nvidia.com) in `.env`,
and the index built as described in Week 6.

Give each WhatsApp chat its own session. By default, DMs share the agent's main session, which
`/new` can't reset, so old answers pile up and the agent repeats them instead of re-running
the skills:

```bash
openclaw config set session.dmScope per-channel-peer
```

## Data

MLS data is provided by IDX Exchange and is not included in this repository.
