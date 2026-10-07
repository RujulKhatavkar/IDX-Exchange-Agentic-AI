---
name: idx-property-search
description: Turn a free text home search (city, price, beds, baths, sq ft, property type, pool, view, HOA) into a structured filter object for the rets_property MLS table. Use whenever the user asks to find, search for, or show properties or listings.
metadata: {"openclaw": {"requires": {"bins": ["python3"]}}}
---

# IDX property search: query parsing

When the user asks to find, search for, or show homes, condos, townhomes, land or listings:

1. Run the parser with the user's message exactly as written:

   ```bash
   python3 {baseDir}/parse_query.py "<the user's message>"
   ```

2. The script prints one line of JSON. Keys and the rets_property columns they filter:

   | Key | Column | Meaning |
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

   Keys the user did not mention are omitted.

3. Reply with the filters in plain language, for example:
   "Searching Irvine: condos, 3+ beds, up to $1,500,000, private pool."

4. If `city` is missing, ask which city before searching. Ask only for the city; every other filter is optional.

Rules:

- Never invent a filter the parser did not return.
- If the user corrects a value ("actually 4 beds"), rerun the parser on the corrected request rather than editing the JSON by hand.
- Database search is not wired up yet (Week 3). Return the filter object only; do not claim to have found listings.
