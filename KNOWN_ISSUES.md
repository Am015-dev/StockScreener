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
- **Sector-relative standing needs the sector to be well covered.** The
  percentile is computed against the exact SEC SIC code first, its
  2-digit major group second, and the whole measured book last — the
  first level with at least 5 other measured names. On the current book
  (~830 names) the exact code clears that bar for well under half of
  them; most names get the major-group comparison, and a rare industry
  gets the whole market, which the page states plainly rather than
  showing a sector figure that would mean nothing.
- **Distance at other horizons holds this quarter's volatility and the
  risk-free rate constant across the whole window.** That gets weaker
  the further out it is stretched — a refinancing, an acquisition, a
  rate-regime change can all happen inside five years and none of them
  are in the model — which is why the multi-horizon view stops at 5
  years rather than extending toward Moody's 10-year term structure.

## Coverage

- **The earnings calendar is US-only.** It comes from Nasdaq's published
  per-date calendar. Non-US listings — London, Paris, Zurich, Frankfurt,
  Milan, Copenhagen — cannot have their earnings dates verified from it,
  so with strict gates on they are **blocked, not published**. That is
  correct behaviour and a real coverage loss: for a European book, the
  tool currently finds US stocks. Fixing it needs a second calendar
  source.
- **Universe is US main-market tickers plus ten European markets**, US
  names from the SEC's company_tickers_exchange file and EU names from
  per-market screens, ordered by size; the scheduled scan takes the
  largest 1,500 across both. Anything smaller is not looked at. (An
  earlier version of this page wrongly called the universe US-only while
  the blocked list on the front page was visibly full of Paris and
  London listings.)
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

- **The pattern sweep still leans towards survivors.** The universe is
  ranked by dollar volume over the FIRST twenty sessions of the window,
  not the last, which removes the larger half of the problem — names that
  took off no longer join the list retroactively. What remains is that
  companies delisted during the window are absent from the price source
  altogether, and nothing here brings them back. Any positive number on
  /patterns should be read as the optimistic end. `pattern_sweep.py` now
  publishes the exact tickers each run tested (`universe_tickers` in
  `patterns.json`) so a future run's candidate pool can be checked
  directly; the run that produced the current numbers on /patterns did
  not, and cannot be checked retroactively. `credit.delisting_filing()`
  is the tool for that check when a ticker list exists: it reads SEC
  EDGAR's Form 25/25-NSE/15 filings and cross-checks against subsequent
  10-K/10-Q filings, because a bare Form 25 is not proof by itself —
  American Electric Power has three of them on file for a security other
  than its common stock and filed a 10-K in 2026 regardless. Run once for
  real against the 455 names in the CURRENT fallback universe
  (`universe_static.US_CORE`, not the pattern sweep's own window) as a
  sanity check on the tool itself: 446 resolved to a CIK, 152 had SOME
  delisting-family filing on record, and after the periodic-filing
  cross-check, zero were confirmed delisted — three (EA, Brown-Forman,
  Philip Morris) had filed within the past month and were correctly left
  unclassified rather than guessed at, and nine tickers had no CIK at all
  in the SEC's own bulk ticker file, a gap in that file rather than in
  the companies. That is a measurement of today's fallback list, not of
  the window /patterns actually ran over, and it should not be read as
  "survivorship bias is smaller than feared" — it says the tool works and
  the CURRENT list is not visibly stale, nothing more.
- **A shape covering half the market is largely compared against
  itself.** The comparison group deliberately includes the pattern's own
  hits, because the question is whether picking this shape beats picking
  from the stocks you could have picked. The cost is that a very common
  shape can never show a large number. This biases towards finding
  nothing, which is the safe direction.
- **Liquidity on /today is proxied by market value, not volume.** No
  per-name share volume is published, and inventing a dollar-volume
  figure from market cap would dress an assumption as a measurement. The
  filter is stated as what it is — a $2B floor — and should be replaced
  with real volume when it is available.
- **/today ranks on survivability, not on direction.** Nothing in the
  score forecasts a price. Two of its four components — how little a name
  overlaps what you hold, and whether a confirmed pattern is firing —
  only count once they have actually been earned; today neither has
  (no portfolio is ever supplied to this page, and nothing on
  `/patterns` has yet held up on data it was not chosen on), so every
  score is out of 60 rather than 100, and the page says so plainly
  rather than quoting a fixed denominator that would overstate how much
  went into the number.

## Things that are not implemented

- Any non-US earnings calendar.
- Intraday data. Everything here is daily bars.
- Any notion of tax, in a tool that sizes positions in euros.
