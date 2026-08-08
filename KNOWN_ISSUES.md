# Known issues and limits

Current as of 8 August 2026. This page exists because a screener that
only publishes what works is not evidence of anything. Everything below
is a limitation a reader could otherwise discover the expensive way.

## The strategy does not currently clear its own bar

This is the most important entry on the page.

The bar, set before the measurements were taken: profit factor ≥ 1.5,
maximum drawdown no worse than the S&P 500 over the same window, Sortino
≥ 1.0. The most recent five-year replay of the published rule set, run
through a realistic five-position book with costs:

| Measure | Result | Bar | Verdict |
|---|---|---|---|
| Profit factor | 0.62 | ≥ 1.5 | fails |
| Max drawdown | −283% of risk unit | not worse than SPY's −18.8% | fails |
| Sortino | −2.04 | ≥ 1.0 | fails |
| Return | — | vs SPY +82.5% | fails |

The best configuration found across a six-way parameter sweep — ATR
stops with a fifteen-bar time exit — reached profit factor 1.07, a 39%
win rate and a −18.4% drawdown. That is the only variant whose drawdown
matched the index, and it still does not clear the bar.

The site says this on its own front page, in the simulation panel,
without being asked. **No part of this tool should be traded on real
money until that verdict changes.** The picks are a hypothesis generator.

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
- **The repository is private**, so the site cannot read published scans
  from raw.githubusercontent without a token, and Actions minutes are
  metered. Published results are committed into the build as a fallback.

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
