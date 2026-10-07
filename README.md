# IDX Exchange: Agentic AI Track

A multi agent real estate assistant built on [OpenClaw](https://github.com/openclaw/openclaw). Users ask questions over WhatsApp; the agent searches active MLS listings (`rets_property`), analyzes sold comps (`california_sold`), and answers real estate questions.

## Progress

| Week | Module | Status |
| --- | --- | --- |
| 0 | Environment setup: OpenClaw, MySQL import | Done |
| 1 | Architecture (see `docs/`) | Done |
| 2 | Natural language property search parser | Done |
| 3 | MySQL query layer | Next |

## Repository layout

```
skills/
  idx-property-search/
    SKILL.md          OpenClaw skill: when and how the agent uses the parser
    parse_query.py    Free text query -> structured rets_property filters
tests/
  test_parse_query.py 17 test queries
docs/                 Architecture notes
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
