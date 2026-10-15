---
name: idx-property-search
description: Search active MLS listings (rets_property) and sold comps (california_sold) from a free text request - city, price, beds, baths, sq ft, property type, pool, view, HOA. Use whenever the user asks to find, search for, or show properties, listings, or recent sales/comps.
metadata: {"openclaw": {"requires": {"bins": ["python3"]}}}
---

# IDX property search

When the user asks to find, search for, or show homes, condos, townhomes, or listings:

1. Run the search with the user's message exactly as written:

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

2. If `city` is missing from the parsed filters, ask which city before searching. Ask only
   for the city; every other filter is optional.

3. Reply with the filters used and the results, for example:
   "Searching Irvine: condos, 3+ beds, up to $1,500,000, private pool. Found 23, showing 10:"
   then list each result's address, price, beds/baths, sqft, and days on market.

4. If `count` is 0, say so plainly and suggest loosening a filter rather than guessing why.

5. If the user asks for more results, rerun with `--page 2` (etc.) rather than asking the
   database layer to return more than 10 at a time.

6. For recent sold prices / comps in a city, instead run:

   ```bash
   python3 {baseDir}/search_properties.py comps --city "<city>" --months 12
   ```

   This queries `california_sold` and returns recent closed transactions for that city.

Rules:

- Never invent a filter the parser did not return, and never invent listings - only report
  what is in `results`.
- If the user corrects a value ("actually 4 beds"), rerun the search on the corrected request
  rather than editing the JSON by hand.
- Results are capped at 50 rows per query. Never ask for or construct a larger limit.
