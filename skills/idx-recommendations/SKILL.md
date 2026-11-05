---
name: idx-recommendations
description: Recommend active listings similar to one specific home the user likes, with a price check against recent sold comps - "show me homes like 380 Dearborn Street", "more like the second one", "anything similar to listing 1176236993?", "is that one priced fairly?". Use whenever the user points at a particular listing and wants comparable homes or a price check on it. For searching by filters use idx-property-search; for searching by a description use idx-semantic-search.
metadata: {"openclaw": {"requires": {"bins": ["python3"]}}}
---

# IDX recommendations

Use this when the user points at **one particular home** - by address, by listing ID, or as
"the second one" from results you showed earlier - and wants similar homes or wants to know
if it's priced fairly.

Run **one** command:

```bash
python3 {baseDir}/recommend.py similar "<listing ID or address>"
```

- If the home came from earlier results, pass its `L_ListingID` from those results - it's
  exact. Otherwise pass the street address as the user wrote it ("380 Dearborn").
- Add `--city "<city>"` if the user named the city.
- Re-run it for every request - don't reuse results from earlier in the conversation.

## What comes back

One line of JSON.

**If `target` is null:**
- `matches` has several homes: the address matched more than one. List them (address, city,
  price) and ask which one, then re-run with that one's `L_ListingID`.
- `matches` is empty: no active listing has that address. Say so and ask the user to check it.

**Otherwise:**
- `target` - the home the user likes, with its own `price_check`.
- `results` - the 5 most similar active homes, best first. Each has `score` (out of 100),
  `breakdown` (`price`, `beds`, `city`, `sqft` and `description` points),
  `L_Address`, `L_City`, `price`, `beds`, `baths`, `sqft`, `remarks_snippet`, and `price_check`.
- `price_check.verdict` - already worded: "priced 14.0% below recent comps",
  "in line with recent comps", or "not enough recent comps to judge the price".
  `comp_count` is how many sales it's based on, `comp_price` the comp-based price.
- `comp_window` - the dates of the sales the comps come from.

## Replying

1. One line on the home they like, with its `price_check.verdict` word for word.
2. Each result: address and city, price, beds/baths, sq ft, a short quote from
   `remarks_snippet`, and its `price_check.verdict` word for word.
3. End with one line saying the price check compares to homes of the same type and similar
   size sold in that city between the two `comp_window` dates.

Example:

"You like 380 Dearborn Street, Pasadena ($1,015,000) - priced 14.0% below recent comps.
Similar homes:
1. 119 W Washington, Pasadena - $975,000, 2 bd / 1 ba, 1,257 sq ft.
   \"Charming Remodeled 1910 Craftsman Bungalow\" - priced 19.3% below recent comps.
2. ...
Price checks compare each home to same-type homes of similar size sold in its city
(2026-03-21 to 2026-09-17)."

## Rules

- Never change or soften a verdict, and never call a home a "good deal" or "overpriced" - say
  what the verdict says. The check only knows city, type and size, so it can't see a view,
  a lake, a pool or the condition of the home. If the user asks why a home is above or below
  comps, say that, rather than guessing a reason.
- Only report homes and facts in the JSON; describe a home only with words from its
  `remarks_snippet`.
- Don't show `score` or `breakdown` unless the user asks why a home was recommended - then
  explain it from the `breakdown` points (e.g. "same city, same beds, similar price, and a
  very similar description").
- This is not an appraisal or financial advice.
