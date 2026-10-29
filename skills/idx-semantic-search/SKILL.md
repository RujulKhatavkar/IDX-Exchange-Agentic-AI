---
name: idx-semantic-search
description: Find active listings by meaning - style, feel, character, setting or features described in words, like "charming craftsman with mountain views", "modern home with an open floor plan", "quiet cul-de-sac near good schools", "cozy cabin by the lake". Use whenever the user describes the kind of home they want rather than only numbers. For plain filter searches (city, price, beds, baths) use idx-property-search; for market questions use idx-market-stats.
metadata: {"openclaw": {"requires": {"bins": ["python3"]}}}
---

# IDX semantic search

Use this when the request describes **what a home is like**: a style (craftsman, Spanish,
mid-century, modern farmhouse), a feel (charming, cozy, light-filled, quiet), a setting
(mountain views, near the beach, cul-de-sac, near good schools) or features in words
(open floor plan, chef's kitchen, big backyard). If the request is only numbers and a city -
"3 beds in Irvine under $1.2M" - use idx-property-search instead.

Run **one** command with the user's message exactly as written:

```bash
python3 {baseDir}/semantic_search.py search "<the user's message>"
```

No city is needed - without one it searches all of California. Don't ask for a city first.
Re-run it for every new request, even a small change ("same but with a pool") - pass the full
request in words, and don't reuse results from earlier in the conversation.

## What comes back

One line of JSON: `{"query", "filters", "candidates", "count", "results": [...]}`.

- `filters` - the hard facts found in the message (city, price, beds, pool, view...) that were
  applied as exact filters before ranking.
- `candidates` - how many active listings passed those filters.
- `results` - the best matches, best first. Each has `similarity` (0 to 1, higher = closer
  in meaning), `L_Address`, `L_City`, `price`, `beds`, `baths`, `sqft`, `type`, `YearBuilt`,
  `DaysOnMarket` and `remarks_snippet` (the start of the listing's own description).

## Replying

For each result, give the address and city, price, beds/baths, sq ft, and **one short quote
from `remarks_snippet`** that shows why it matches. Example:

"Top matches for 'charming craftsman with mountain views':
1. 27601 Cedarwood Drive, Lake Arrowhead - $1,199,000, 4 bd / 4 ba, 3,174 sq ft.
   \"Exquisite Craftsman Style Mountain Home\"
2. ..."

## Rules

- Only report listings in `results`, and only facts in the JSON. Describe a home only with
  words from its `remarks_snippet` - never add features it doesn't mention.
- These are the closest matches, not guaranteed perfect ones. If a result clearly misses part
  of the request (no mention of views, say), say so rather than pretending it fits.
- If `count` is 0, say nothing matched the filters in `filters` and suggest loosening one
  (a higher price, fewer beds) - don't invent listings.
- Don't show `similarity` numbers unless the user asks how well something matched.
- `--top N` returns more results (up to 20) if the user asks for more.
