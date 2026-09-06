# refprice — a cross-venue reference price, with the methodology on the page

[![ci](https://github.com/shunqo/refprice/actions/workflows/ci.yml/badge.svg)](https://github.com/shunqo/refprice/actions/workflows/ci.yml)

A volume-weighted median BTC/USDT price across four independent public venues, computed every five
minutes, published five minutes behind the clock, and **explicitly undefined whenever fewer than
three venues actually traded**.

The number is not the product. The product is the table underneath it:

```
venue        median dev (bp)   p95    worst   worst at   intervals   excluded
bybit                   0.00   0.49    1.28      —            238          0
okx                     0.19   0.99    2.05    18:20          238          0
kraken                  0.86   2.57    4.39    16:05          238          4
coinbase                1.08   4.67    5.89    17:40          238          0
```

**If you settle a trader's stop-out, a payout, or an evaluation breach on one venue's print, that
is the size of the disagreement you are exposed to.** Most firms that do this have never measured
it, and the first time they find out is in a dispute they cannot resolve because there is no
independent record of what the price was.

Built on the same collection layer as
[`mdq`](https://github.com/shunqo/mdq) — same source adapters, same rate limiter,
same refusal policy — as a declared dependency, not a copy-paste. Cloning `mdq` next to this repo
makes `run.sh` use the working copy instead of the published one.

---

## The methodology, which is the whole thing

A reference price is not an average of some prices. It is a published set of rules about which
prices count, what happens when they disagree, and — the part almost everybody skips — what the
answer is when there is not enough evidence to have one.

| Rule | Value | Why |
|---|---|---|
| `min_contributors` | 3 | Two venues can agree perfectly and be wrong together, and from two there is no way to tell. Below three, **no price is published.** |
| `outlier_mad_multiple` | 5.0 | Distance from the cross-venue median in units of the cross-venue MAD. Tighter than a monitoring threshold: the cost of admitting a bad print here is a wrong settlement price, not a noisy alert. |
| `outlier_min_bp` | 10.0 | Floor on the rejection band. When venues agree closely the MAD collapses and every venue starts to look like an outlier. Below 10 bp the disagreement is not economically real. |
| `require_volume` | true | A bar with no trades carries no price information. Counting it gives a thin venue a vote it did not earn. |

**Aggregation is a volume-weighted median, not a volume-weighted mean.** A venue holding most of
the volume can drag a mean arbitrarily far with a single bad print. It can move a weighted median
no further than its own quote, and only if it holds more than half the weight. The failure a
reference price has to survive is one venue being wrong and busy at the same time.

### Two decisions that were measured rather than chosen

**The five-minute interval.** The observation interval has to be long enough that every contributor
actually trades inside it. Measured against the four configured venues on 2026-09-05:

| interval | intervals with a price | mean contributors |
|---|---|---|
| 1 minute | 69.7% | **2.83** |
| **5 minutes** | **98.3%** | **3.49** |
| 15 minutes | 100.0% | 3.95 |

At one minute the mean contributor count is *below the required three* — the whole problem in one
number. Fifteen is fully covered and too coarse to be interesting. Five is the choice, and the 1.7%
of intervals it still cannot price are real: two of the four venues genuinely did not trade.

**The five-minute publication lag.** Venues do not finish reporting an interval at the same
instant; Coinbase Exchange's public candles endpoint runs three to five minutes behind the others,
measured. Publishing an interval the moment it closes would drop the slowest venues from every
print, quietly turning a four-venue index into a two-venue one at exactly the moments anyone is
looking at it. Intervals newer than the lag are computed and stored as **provisional**, recomputed
as late data arrives, and never headlined.

### Venue selection, and what is deliberately left out

Four venues, BTC/USDT spot: **OKX, Bybit, Kraken, Coinbase Exchange**.

- **Spot only.** A perpetual swap trades at a basis to spot that is the price of funding, not a
  disagreement about the value of bitcoin. Blending them would make the reference price move
  whenever funding did.
- **One quote currency.** BTC/USD and BTC/USDT differ by the USDT basis. A reference price has to
  be denominated in one thing.
- **OKX `BTC-USDT-SWAP` and Deribit `BTC-PERPETUAL` are excluded even from a perp index**, because
  their volume fields are denominated in **contracts**, not base currency. Feeding a contract count
  into a volume-weighted statistic alongside four venues reporting BTC would weight them by a
  different unit, and nothing in the output would look wrong. They stay out until that conversion
  is implemented and tested.
- **Binance, Stooq and Bitstamp are excluded** because their `robots.txt` disallows automated
  agents. Binance would materially improve the venue set. It stays out anyway.

### Known weakness, stated rather than buried

Two of the four venues are thin in BTC/USDT specifically: Kraken and Coinbase both run orders of
magnitude less volume in this pair than OKX and Bybit. **The weighted median is therefore decided
in practice by two venues**, with the other two acting as a sanity check rather than as weight.
That is survivable — it is part of why the aggregation is a median and why three contributors are
required — but it is a real limitation, and a production version would add depth.

---

## Running it

```bash
./run.sh          # one pass, then serve on http://127.0.0.1:8701/
./run.sh run      # one pass, no server
./run.sh show     # print the latest reference price and every contributor
./run.sh test     # 32 tests, no network
```

`refprice show` is the fastest way to see what the methodology is doing:

```
BTC/USDT 5-Minute Reference Rate: 79,781.55 at 2026-09-05 19:45 UTC (4 contributors)
    bybit           79,781.55  vol    22.2621    0.00 bp
    coinbase        79,796.54  vol     0.0524    1.88 bp
    kraken          79,802.60  vol     3.7850    2.64 bp
    okx             79,791.35  vol    15.9268    1.23 bp
```

Excluded venues are printed with an `x` and the reason.

### Exit codes

| Code | Meaning |
|---|---|
| 0 | Every final interval in the window has a reference price |
| 1 | Ran, but some intervals could not be priced |
| 2 | A venue is down, or the latest final interval is undefined |
| 3 | Could not run (refused bind, unwritable path) |

### Binding

Localhost by default, on port 8701. A wildcard bind is **refused**, by the same
`mdq.server.check_bind` the other demo uses.

---

## This is a demonstration, not a benchmark

It is not IOSCO-aligned. It has no governance committee, no oversight function, no audit trail
beyond this page and its SQLite file, no continuity guarantee, and no legal standing. **Do not
settle anything real on it.** What it demonstrates is that the person who built it knows what a
real one would need.

## Licence

MIT. See `LICENSE`.
