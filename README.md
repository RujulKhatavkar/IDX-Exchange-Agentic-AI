# IDX Exchange: Agentic AI Track

A multi agent real estate assistant built on [OpenClaw](https://github.com/openclaw/openclaw). Users ask questions over WhatsApp; the agent searches active MLS listings (`rets_property`), analyzes sold comps (`california_sold`), and answers real estate questions.

## Progress

| Week | Module | Status |
| --- | --- | --- |
| 0 | Environment setup: OpenClaw, MySQL import | Done |
| 1 | Architecture (see `docs/`) | Done |
| 2 | Natural language property search parser | Done |
| 3 | MySQL query layer | Done |

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
tests/
  test_parse_query.py      17 parser test queries
  test_search_properties.py  Query-builder tests (SQL shape, params, pagination, limit clamp)
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

## Setup

Requires Node 24.16+ (for OpenClaw), Python 3.10+, and MySQL 8.4.

```bash
npm install -g openclaw@latest
openclaw onboard --install-daemon
pip install -r requirements.txt
python3 -m pytest tests/ -v
```

Link the skill into OpenClaw so edits in this repo apply directly:

```bash
ln -s "$PWD/skills/idx-property-search" ~/.openclaw/skills/idx-property-search
openclaw gateway restart
```

## Data

MLS data is provided by IDX Exchange and is not included in this repository.
