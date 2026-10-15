---
name: idx-property-search
description: Search active MLS listings (rets_property) and sold comps (california_sold) from a free text request - city, price, beds, baths, sq ft, property type, pool, view, HOA. Use whenever the user asks to find, search for, or show properties, listings, or recent sales/comps.
metadata: {"openclaw": {"requires": {"bins": ["python3"]}}}
---

# IDX property search

When the user asks to find, search for, or show homes, condos, townhomes, or listings:

## First message in a property search

Run the search with the user's message exactly as written:

```bash
python3 {baseDir}/search_properties.py search "<the user's message>" --page 1 --limit 10
```

This parses the message into filters (see below) and queries `rets_property` for active
listings matching them. It prints one line of JSON:
`{"filters": {...}, "page": N, "limit": N, "count": N, "results": [...]}`.

| Filter key | Column | Meaning |
| --- | --- | --- |
| city | L_City | exact city |
| minPrice / maxPrice | L_SystemPrice | price range in dollars |
| beds | L_Keyword2 | minimum bedrooms |
| baths | LM_Dec_3 | minimum bathrooms |
| sqft | LM_Int2_3 | minimum square feet |
| type | L_Type_ | SingleFamilyResidence, Condominium, Townhouse, Duplex, Triplex, Quadruplex, MobileHome, ManufacturedOnLand, Cabin, StockCooperative |
| pool | PoolPrivateYN | "True" or "False" |
| hasView | ViewYN | "True" or "False" |
| maxHoa | AssociationFee | maximum monthly HOA in dollars |

If `city` is missing from the parsed filters, ask which city before searching. Ask only for
the city; every other filter is optional.

**Remember the `filters` object this call returns** (in your own context - there is no
server-side session store) - it's what the next step builds on.

## Follow-up messages in the same conversation

The script is stateless: each call only parses the one message you give it. To refine a
search across turns (add a constraint, correct a value, or just page through more results),
pass the filters you remembered from the last call back in with `--known`, as a JSON string:

```bash
python3 {baseDir}/search_properties.py search "<the new message>" --known '<last filters JSON>' --page 1 --limit 10
```

Whatever the new message mentions overrides the matching key in `--known`; everything else
carries forward unchanged. This is also how a correction works - rerun with the corrected
request as `<the new message>` and the old `--known`, rather than editing the JSON by hand.
Example conversation:

```
User: "Find homes in Irvine"
  -> search "Find homes in Irvine"                                         => filters: {city: Irvine}
Agent: "What is your budget?"
User: "Under $1.2M"
  -> search "Under $1.2M" --known '{"city":"Irvine"}'                      => filters: {city: Irvine, maxPrice: 1200000}
Agent: "Any preferences - condo, townhome, or single family?"
User: "Single family with at least 3 beds"
  -> search "Single family with at least 3 beds" --known '{"city":"Irvine","maxPrice":1200000}'
                                                                            => filters: {city, maxPrice, type, beds}
Agent: [returns filtered results]
```

If the user just asks for more of the same results ("show me more"), pass the same `--known`
filters with an empty or minimal query and bump `--page` - don't re-describe the whole search.

Always update the `filters` you're holding onto with what the latest call returns, so the
next turn builds on the current state, not a stale one.

## Replying

Reply with the filters used and the results, for example:
"Searching Irvine: condos, 3+ beds, up to $1,500,000, private pool. Found 23, showing 10:"
then list each result's address, price, beds/baths, sqft, and days on market.

If `count` is 0, say so plainly and suggest loosening a filter rather than guessing why.

## Sold comps

For recent sold prices / comps in a city, instead run:

```bash
python3 {baseDir}/search_properties.py comps --city "<city>" --months 12
```

This queries `california_sold` and returns recent closed transactions for that city. It does
not take `--known` - comps are looked up fresh per city each time, not carried across turns.

Rules:

- Never invent a filter the parser did not return, and never invent listings - only report
  what is in `results`.
- Results are capped at 50 rows per query. Never ask for or construct a larger limit.
