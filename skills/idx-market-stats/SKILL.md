---
name: idx-market-stats
description: Market statistics from sold homes (california_sold) for a California city - median and average sale price, price per square foot, days on market, list-to-close ratio, and month-by-month trends. Use whenever the user asks how a market is doing, whether it's a good time to buy or sell somewhere, what homes sell for, price per sq ft, or how prices are trending. For finding specific listings, use idx-property-search instead.
metadata: {"openclaw": {"requires": {"bins": ["python3"]}}}
---

# IDX market statistics

When the user asks about a market rather than specific listings - "Is now a good time to buy in
San Diego?", "What's the average price per sq ft in Pasadena?", "How fast are condos selling in
Irvine?" - run **one** command:

```bash
python3 {baseDir}/market_stats.py overview --city "<city>"
```

It needs a city; if the user didn't name one, ask which city before running anything. Always use
`overview` for market questions: it returns the summary, the monthly trend and the overall
change together, and you need all three to describe a market. Re-run it for every market
question, even about a city you answered earlier - don't reuse numbers or wording from earlier
in the conversation. (`summary` returns the same minus the month-by-month rows; `trend` returns
only the months.)

## What comes back

One line of JSON. **Start with `reading`** - it has already turned the numbers into words:

| Field | Values |
| --- | --- |
| headline | one sentence summing up the market - quote it as-is |
| median_price_direction, price_per_sqft_direction | rising / falling / flat / unknown |
| speed | fast / moderate / slow |
| negotiation | at or above asking / near asking / below asking |
| market_lean | seller-leaning / buyer-leaning / balanced |

`reading` is `null` when the city has no sales. The other parts hold the numbers behind it:

**`summary`** - every residential sale in the city over the last 12 months:

| Field | Meaning |
| --- | --- |
| sold_count | number of closed sales |
| median_close_price | middle sale price - use this as the "typical" price |
| avg_close_price | mean sale price; higher than the median because a few expensive sales pull it up |
| avg_price_per_sqft | sale price divided by living area, averaged |
| avg_days_on_market | days from listing to accepted offer |
| list_to_close_pct | sale price as % of list price |

**`trend`** - one object per month, oldest first: `month`, `sales`, `median_close_price`,
`avg_price_per_sqft`, `avg_days_on_market`, `median_price_change_pct` and
`price_per_sqft_change_pct` (vs. the previous month), and `partial`. A month with
`"partial": true` is only partly covered by the data - ignore it when judging direction.

**`change_over_period`** - first full month vs. last full month (`from_month`, `to_month`):
`median_price_change_pct`, `price_per_sqft_change_pct`, `days_on_market_change` (in days).
`reading`'s directions come from these. It is `null` if there are fewer than two full months
(then `reading.headline` says there isn't enough data to call a trend).

## Replying

1. The `summary` numbers: homes sold, median price, price per sq ft, days on market, % of asking.
2. `reading.headline`, word for word.
3. For "is it a good time to buy/sell?": what `market_lean` means for the user - a
   seller-leaning market means competition and little room to negotiate; buyer-leaning means
   more choice and room for discounts; balanced is in between. Let the user decide - present
   it as what the sales data shows, not as financial advice.

Example:

"San Diego, last 12 months: 4,620 homes sold. Median price $950,000, $725/sq ft, 27.6 days on
market, 99.5% of asking.
San Diego: from Apr 2026 to Aug 2026 the median price was rising (+7.7%) and price per sq ft
was flat (-0.8%); homes sell at a fast pace (27.6 days on market) for near asking (99.5% of
list price), a seller-leaning market.
For a buyer, that means competition: expect to pay close to asking and to move quickly."

## Rules

- Never contradict `reading`. If it says seller-leaning, don't call the market buyer-friendly;
  if it says flat, don't call prices rising or falling.
- Only report numbers that are in the JSON. Never estimate, round into a range, or fill in a
  missing metric.
- Never compare to other cities, regions, national figures, or past years - this data has none
  of those. Don't explain *why* a number is what it is (e.g. "luxury homes", "lots") unless the
  JSON shows it.
- If `sold_count` is 0, say there are no recorded sales for that city and window, and check the
  spelling of the city with the user instead of guessing.

## Narrowing

`overview` (and `summary`/`trend`) accept the same optional filters:

- `--type <PropertySubType>`: SingleFamilyResidence, Condominium, Townhouse, Duplex,
  ManufacturedOnLand, StockCooperative, Cabin. Use when the user asks about one kind of home.
- `--zip <ZIP>`: one ZIP code within the city.
- `--months <N>`: look-back window in months (default 12).
