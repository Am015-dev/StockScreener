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

## What the second credit read (Altman Z'') cannot tell you

- **It only appears alongside a working market-based read, not instead
  of one.** Distance to Default needs a share price; the Z'' needs only
  a balance sheet and an income statement, and could in principle be
  shown for a company the market-based model cannot reach at all — that
  is not wired up yet. Right now, if the Merton read did not succeed,
  no Z'' is computed either.
- **IFRS filers (20-F) do not get one this round.** The four extra tags
  it needs (total assets, current assets, retained earnings, operating
  income) are only tried against the `us-gaap` taxonomy — the IFRS
  filers `credit.py`'s main balance-sheet read already supports do not
  yet get the equivalent `ifrs-full` tags tried for this second read.
- **EBIT is approximated as operating income.** The two agree unless a
  company carries material non-operating income or expense above the
  interest line (an asset sale, an FX gain, equity-method earnings) —
  usually small, but a real source of divergence from a textbook-strict
  EBIT figure.
- **Already-measured companies do not get it retroactively.** The extra
  tags are only fetched when a company's filings are (re-)read, so a
  ticker already sitting in the published book from before this shipped
  shows no Z'' until it is next measured — coverage grows the same way
  the rest of the credit book already does, not all at once.

## What the VIX regime reading cannot tell you

- **It is one index, not a forecast.** A high or low reading describes
  where implied volatility has actually been, placed against its own
  trailing ~5 years (see `vix.py`) — it does not predict where it is
  going next, and nothing on this site sizes a position or gates a pick
  by it.
- **The 5-year lookback is a choice, not a law.** A longer window would
  include 2008- and 2020-scale spikes that would permanently raise the
  bar for what counts as "elevated"; a shorter one would be more
  reactive to whatever regime the market happens to be in right now.
  Five years was picked as a middle ground, not fitted to produce a
  particular answer.
- **A CBOE outage, not a calm market, is the most common reason this
  is silent.** No VIX line on the front page can mean either "today is
  ordinary" or "the reading could not be fetched this run" — both read
  the same way, by design, rather than guessing which one happened.

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
- **IFRS filers (20-F, `ifrs-full`) are read when they report in USD** —
  Shell, AstraZeneca, Novartis and similar. A filer that reports only in
  another currency — GBP, EUR, JPY; BTI is among them — is refused **with
  the currency named**, because converting a balance sheet at a guessed
  rate would be a number the model does not mean. `us-gaap` still wins
  when a filer tags both taxonomies, so which route answers does not flip
  between runs.
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

## What /investors (the 13F superinvestor page) cannot tell you

- **It is old on arrival, and gets older for weeks at a stretch.** A 13F
  shows positions as of quarter end, filed up to 45 days later — freshest
  possible is 45 days stale, and the day before the next quarter's
  filings land it is closer to 135. The reporting period AND the filed
  date are shown everywhere a holding renders, never a bare "current".
- **Long, US-listed equities only.** No shorts, no cash, no bonds, no
  non-US listings. A manager hedged flat against a name and a true
  believer render identically, because the filing itself cannot tell
  them apart.
- **Absence proves nothing.** Positions below the $100M/name reporting
  threshold, non-US listings, and confidential-treatment holdings are
  all invisible to a 13F. A ticker with no line on `/investors` is not
  a ticker no tracked manager owns — it may simply not be visible to
  this filing.
- **The roster is a curated ~24 managers, not the whole institutional
  market.** Chosen by hand in the spirit of Dataroma's own list (the
  idea is borrowed, the data is not — every filing is read directly from
  SEC EDGAR), and re-verified against SEC's own filer-name record every
  run rather than trusted once: a wrong or superseded CIK is refused
  loudly, not served silently. Every run also names, on the page itself,
  which tracked managers could not be read that week.
- **CUSIP-to-ticker mapping is not complete.** Built from the SEC's own
  fails-to-deliver files (three consecutive half-months, merged), which
  measurably covers the large majority of names a large filer's 13F
  references but not all of them — roughly 7% of tickers across all
  tracked managers combined, measured on the first real run. A CUSIP
  that fails to map renders by the issuer's name as printed in the
  filing itself, never by a guessed ticker symbol.
- **This adds zero score points anywhere on the site.** `/today`, `/full`
  and the pre-trade check all show a holder count as a note, never as
  part of a rank or a filter. Buying what a well-known manager owned
  weeks ago, with no way to know whether they have since sold, is not a
  strategy this project has measured — so nothing here claims it is one.

## Coverage

- **The bulk earnings calendar is US-only** (Nasdaq's published per-date
  calendar); non-US listings — London, Paris, Zurich, Frankfurt, Milan,
  Copenhagen — have never had a date corroborated by it. **Non-US dates
  are read from Yahoo's per-ticker calendar instead, on the scheduled
  runner — one source, not two.** A US pick's date is checked against
  the complete bulk calendar (optionally cross-checked against Finnhub
  too); a European pick's date rests on Yahoo alone, and every place it
  renders — `/today`, `/full`, the pre-trade check — says so plainly
  (flagged "single source", never presented as if corroborated). A
  European name Yahoo cannot supply a date for is still **blocked, not
  published** — absence from one source proves nothing.
- **Universe is US main-market tickers plus ten European markets**, US
  names from the SEC's company_tickers_exchange file and EU names from
  per-market screens, ordered by size; the scheduled scan takes the
  largest 1,500 across both. Anything smaller is not looked at. (An
  earlier version of this page wrongly called the universe US-only while
  the blocked list on the front page was visibly full of Paris and
  London listings.)
- **Half-day sessions now close at the right time.** `market_clock.py`
  is backed by `exchange_calendars` (adopted over the hand-typed holiday
  table it used to carry), which knows early closes — Black Friday,
  Christmas Eve — and reports the market as closed from 1pm ET on those
  days instead of 4pm. If that library fails to import on a given
  instance, the module falls back to its old table, which still treats a
  half day as a full session; the freshness counter (sessions passed) is
  correct either way, only the closing-time label can be off on the
  fallback path.
- **The trading calendar is known roughly 18 months past the last
  deploy**, not a fixed date — the calendar is rebuilt each time the
  process starts, bounded to a window around "now" to keep the memory
  cost trivial. Past that window the page reports the market state as
  "unknown" rather than assuming every weekday trades.

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
- **Storage is ephemeral, for the journal specifically.** Render's free
  plan wipes the disk on every deploy. The simulation history and the
  stored snapshots (both live in `db.py`'s market database) now survive
  this — `app.py` restores that file from the scheduled scan's own
  published copy at boot and roughly hourly after. The journal has no
  server-side equivalent restore yet; the browser mirrors it to
  localStorage as a partial defence. A persistent disk (~$1–2/month)
  would fix the journal properly.
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
- **Today's Five's cooldown (`ranking.select_daily_five()`) starts cold.**
  It only knows what `scripts/scheduled_scan.py` has actually recorded
  and published to `recent_picks.json`; a fresh deployment, or one where
  that file has not yet accumulated a session's worth of history, ranks
  exactly as it did before this shipped — `ranked[:5]`, no cooldown
  applied, because there is nothing yet to cool down against. It also
  only ever applies to the anonymous default `/today` view — `/check`
  and `/full` are unaffected, since a cooldown against "what was shown
  publicly" has no meaning for a pre-trade check on a name the reader
  chose themselves.

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
- **Liquidity on /today is measured where it can be, proxied where it
  can't.** The scan publishes a real 30-day average dollar volume per
  ticker (`liquidity.json`, from the same price/volume bars it already
  downloads), and /today gates on it directly — a $50M/day floor, the
  same one the loosest published preset already enforces. A ticker
  liquidity.json has no figure for (an FX rate could not be established,
  or it never traded in the scanned window) falls back to a $2B
  market-value floor instead, and carries a stated flag saying so —
  never silently treated as a real measurement of the same thing.
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
