# Known issues and limits

Current as of 12 August 2026. This page exists because a screener that
only publishes what works is not evidence of anything. Everything below
is a limitation a reader could otherwise discover the expensive way.

## The entry signal is dead, and the site no longer shows a pick

This is the most important entry on the page. It used to hedge
("does not clear its own bar"); it no longer needs to.

A permutation test answered the prior question, twice. The real pullback
signal was run over hundreds of stocks, then compared against dozens of
runs of **coin-flip entry** on the same stocks over the same days,
managed by byte-for-byte identical code — same ATR stop, same target,
same holding cap, same costs, same one-trade-per-ticker rule. Only the
choice of which bars to enter was replaced. It has now been run on both
rule sets the site ships:

**balanced** (the defaults), 8 August 2026, 376 stocks, 25 seeds:

| | trades | avg outcome | win rate | profit factor |
|---|---|---|---|---|
| Pullback signal | 526 | +0.060R | 40% | 1.10 |
| **Random entry** | ~671 | **+0.066R** | **57.8%** | **1.214** |

z = −0.07, **p = 0.50** — twelve of twenty-five coin flips did as well
or better. Random entry beat the signal on all three measures.

**wide-net** (the rule set behind the published board), 12 August 2026,
657 stocks, 40 seeds:

| | trades | avg outcome | win rate | profit factor |
|---|---|---|---|---|
| Pullback signal | 3,658 | +0.080R | 43% | 1.14 |
| Random entry | ~4,186 | +0.070R | 58.6% | 1.209 |

z = +0.31, **p = 0.41** — the signal's tiny lead over the coin flips is
a third of one standard deviation, which is noise.

The honest reading: the RSI dip zone, the uptrend filter, the
reward:risk floor and the relative-strength gate are not selecting
anything. Whatever small positive expectancy appears comes from the
trade *management* — the stop placement and the time exit — and is
available to anyone entering at random.

**Because of this, the front page recommends no trade.** The former
headline — a named ticker with a stop and a target — was removed on
12 August 2026, the day the second test confirmed the first. The daily
list survives as what it factually is: liquid stocks near a price level
buyers previously defended, with earnings dates verified, useful as a
watchlist to run through the pre-trade check. It is not evidence that
buying them makes money.

The test lives in `scripts/null_test.py`, takes the preset name as an
input, and runs from the Actions tab — so both claims can be re-checked
rather than taken on trust.

## What the credit model cannot tell you

- **Banks, insurers and other financials are refused, not measured.**
  The model reads current liabilities as debt coming due against the
  firm's assets. For a bank the deposits ARE the liabilities; for an
  insurer they are claims funded by premiums already collected. Neither
  is refinancing risk, which is why the KMV literature excludes
  financials. Before this exclusion a health insurer ranked
  fourth-closest-to-trouble on this site because its medical claims
  payable were read as debt. Companies with SEC SIC codes 6000–6799 now
  get "not modelled" instead of a number.
- **No default probability is quoted, ever.** The distance-to-default is
  real and comparable; converting it to a percentage requires a
  proprietary default database. The textbook substitute returns
  0.000000% for companies that do sometimes default, so it is not used.
- **IFRS filers are not covered.** SEC XBRL under `us-gaap` only, so
  foreign private issuers filing under `ifrs-full` (BTI and similar)
  report as unmeasurable.
- **Volatilities that are not volatilities are refused.** Thinly traded
  secondary listings (OTC ADRs, London IOB lines) publish closes that
  sit still for days and jump — the standard deviation of that is a fact
  about a data feed. Anything above 150% annualised, or with more than
  20% of days unchanged to the cent, is refused with the reason stated.
- **The balance sheet is quarterly; the price is daily.** Between
  filings the distance is re-solved against the latest close with the
  filing held constant, and the report says so where the reader can see
  it.

## Coverage

- **The earnings calendar is US-only.** It comes from Nasdaq's published
  per-date calendar. Non-US listings — London, Paris, Zurich, Frankfurt,
  Milan, Copenhagen — cannot have their earnings dates verified from it,
  so with strict gates on they are **blocked, not published**. That is
  correct behaviour and a real coverage loss: for a European book, the
  tool currently finds US stocks. Fixing it needs a second calendar
  source.
- **Universe is ~7,650 US main-market tickers** from the SEC's
  company_tickers_exchange file, ordered by size, and the scheduled scan
  takes the largest 1,500. Anything smaller is not looked at.
- **Half-day sessions are treated as full sessions.** The freshness
  counter is correct on those days; the closing time is not.
- **The trading calendar is hard-coded through 24 December 2027.** Past
  that the page reports the market state as "unknown" rather than
  assuming every weekday trades.

## Data sources and what breaks

- **Yahoo rate-limits datacenter IPs.** Fundamentals and per-ticker
  earnings dates are the first casualties. When it happens the affected
  picks are blocked, not published with a footnote — but a scan during a
  hard throttle can return very few rows for that reason alone.
- **Finnhub cross-checking is off** unless `FINNHUB_API_KEY` is set. It
  is a free key; without it there is one fewer earnings source.
- **Analyst consensus and market cap come from Yahoo** and inherit its
  outages. A pick with a blank Analysts cell is a pick whose consensus
  could not be read, not one with no coverage.

## Hosting limits

- **Scans started from the page are capped at 250 stocks.** The instance
  has 512MB and a request timeout; a 1,500-stock scan run there is killed
  part-way, which previously surfaced as "This scan has stopped
  responding". The full universe is scanned on a schedule instead.
- **The scheduled scan runs on weekdays only, 13:00–21:00 UTC.** Outside
  those hours — including all weekend — the newest data is the last
  session's close, and the page says so.
- **Storage is ephemeral.** Render's free plan wipes the disk on every
  deploy, taking the journal, the simulation history and the stored
  snapshots with it. The browser mirrors the edge statistics to
  localStorage as a partial defence. A persistent disk (~$1–2/month)
  would fix it properly.
- **Published scans are read from a public data branch**, with the copy
  committed into the build as a fallback for when that read fails. The
  `/published` endpoint reports which of the two answered, per file.

## Measurement caveats

- **The simulation cannot apply the earnings or fundamentals gates
  historically.** It has no point-in-time record of what those values
  were on each past date, so the replayed rules are technical-only. Live
  picks pass strictly more gates than the simulated ones did. The panel
  states this.
- **Concentration is measured on 60 sessions of daily returns.** A pick
  with less overlapping history is reported as unmeasured, not as
  uncorrelated. Two names count as the same trade at correlation 0.70 or
  above — a judgement, not an optimised threshold.
- **The "Adds (R)" figure needs a measured edge.** Picks with no track
  record under the current rules carry no figure and are not ranked by
  it, because scoring an unmeasured edge as the average would let a
  setup with no history outrank one with a measured positive expectancy
  purely for being uncorrelated.
- **The all-signals simulation numbers are an artifact** of taking every
  signal at once, which no real account can do. The portfolio figures
  beside them are the honest ones; both are shown.

## Things that are not implemented

- Sortable table columns, and a per-row score breakdown on click.
- Any non-US earnings calendar.
- Intraday data. Everything here is daily bars.
- Any notion of tax, in a tool that sizes positions in euros.
