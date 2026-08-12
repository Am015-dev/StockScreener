# StockScreener

Free-data investment research tools for a small personal book, deployable
for free on Render. What the site actually offers, in the order it offers
it:

1. **A pre-trade check** — type a ticker and it is checked against what
   you already own: overlap (is this a trade you already hold under
   another name, measured on published return correlations), verified
   earnings dates, credit standing, and whether the buy genuinely widens
   your diversification (effective number of bets, before vs after).
2. **A credit report** — Merton/KMV distance-to-default computed from SEC
   XBRL filings and published share prices, with peer ranking, a 60-day
   history, driver attribution (debts vs share-price volatility), and its
   sources named. No default probability is quoted — the honest reasons
   are on the report itself. Banks and insurers are refused, not
   mis-measured.
3. **A pattern screen, kept as a research record.** The pullback entry
   signal was falsified by a permutation test on both shipped rule sets
   (statistically indistinguishable from coin-flip entry through identical
   exit code, p = 0.50 and p = 0.41 — see [STRATEGY.md](STRATEGY.md) and
   `/limits`).
   **The site recommends no trades.** The screen survives as a factual
   watchlist — liquid stocks near defended price levels with verified
   earnings dates — to feed the pre-trade check.

Everything below documents the screening machinery. Read it with the
falsification above in mind: it is a description of a filter, not of an
edge.

## What it screens for

- **Universe**: every Yahoo sector *except* Financial Services, US + 10
  European markets, market cap > $10B, discovered live via Yahoo's screener API
- **Trend**: price above the 200-day SMA
- **Pullback**: RSI(14) between 35 and 55 — a dip, not a collapse
- **Structure**: last pivot low below price (support), 60-day swing high above
  price (target), stop 1.5% below support
- **Quality gates** a chart-only screener lacks:
  - reward:risk ≥ 3
  - forward EPS > 0 (profitability gate)
  - no earnings within 10 days (no entries into binary events)
  - liquidity ≥ $200M/day traded
  - exclusion list for held-at-cap / permanently passed tickers
- **Sizing**: shares = min(max-risk-based, ticket-based), reported with the
  actual EUR risk per position

Every threshold above is a **user-adjustable filter in the web UI** — RSI band,
min R:R, market cap, liquidity, sectors, US/EU markets, earnings buffer,
profitability toggle, exclusion list, stop buffer, lookbacks, and sizing.
Defaults live in `DEFAULTS` at the top of [`screener.py`](screener.py); bad
input is clamped server-side, so you can't break a scan by playing.

## Validation layer (all free)

Beyond the original gates, each candidate is validated with data that costs
nothing extra:

- **Market regime gate** — entries are skipped (toggleable) when the region's
  benchmark (SPY for US, EURO STOXX 50 for EU) is below its own SMA200:
  pullback-buying in a falling market is a different, worse trade.
- **Relative strength (`rs_3m`)** — 3-month return minus the benchmark's, so
  "strong stock" is separated from "rising tide". Feeds the score.
- **Pullback volume character (`vol_ratio`)** — recent 10-day volume vs the
  prior 30 days. A healthy pullback is quiet (< 1); heavy-volume selling
  scores worse.
- **ATR noise gate (`stop_atr`)** — if the stop sits less than 1 ATR from
  price (adjustable), normal daily noise will trigger it; the setup is
  rejected as mechanically unsound.
- **EPS fallback** — Yahoo's `forwardEps` is often missing for EU names; the
  profitability gate falls back to trailing EPS (flagged `eps_fallback`)
  instead of falsely rejecting.
- **Earnings-date cross-check** — optionally set `FINNHUB_API_KEY`
  ([finnhub.io](https://finnhub.io), free tier, no card) and US earnings dates
  are verified against a second source: a missing Yahoo date is filled in, a
  disagreement > 2 days uses the *earlier* date (conservative) and is flagged.
  Without a key everything still works; unverifiable dates get an
  `earnings_unverified` flag.

Data-quality issues never vanish silently: they show up in a `flags` column
and cost 5 score points each, so you always see *why* a name ranked lower.

## Portfolio awareness (optional)

Paste your holdings (`TICKER, shares, cost basis`, one per line) and free cash
into the **Portfolio** panel — or hit **📥 Import broker CSV** and upload your
broker's transaction export. The Revolut format is supported end-to-end: the
full event history (buys, sells, splits, mergers, corrections, dividends,
fees, cash top-ups/withdrawals, broker-migration transfers) is replayed to
reconstruct your *current* open positions with average cost basis, plus free
cash per currency converted to EUR. A simple `ticker,shares,cost` CSV works
too. The file is parsed once and never stored server-side — the result lands
in the editable holdings box (so you can fix e.g. EU ticker suffixes like
`BAYN → BAYN.DE`, which the import flags for you) and is saved in your
browser (localStorage). When present, every candidate is judged against
*your* book:

- **New / Add annotation** — held names show `ADD (+12.3%)` with unrealized
  P&L, so the question becomes "add?" instead of "buy?".
- **Fundability** — positions larger than free cash are shrunk to fit
  (`cash_capped` flag) or flagged `no_cash`; suggestions are things you can
  actually execute.
- **Sector-concentration guardrail** — the `sector_after` column shows what
  the sector's weight would become; past your cap (default 35%) it's flagged
  `sector_cap` and score-penalized. No more doubling down on what you're
  already overweight.
- **Aggregate risk budget** — instead of only a flat per-trade cap, the book's
  total open-risk ceiling (default 6% of book, minus risk already in the
  market) is walked down the ranking: `cum_risk_EUR` and a ✓/✗
  `fits_risk_budget` column show exactly how many of today's setups your
  budget covers.
- **Net-of-cost economics** — with your commission and spread, trades where
  round-trip costs eat more than 20% (adjustable) of the target profit are
  rejected outright (`friction` reason) — the classic small-ticket trap.

Without holdings everything behaves as before; the friction gate still runs
since it only needs your cost settings.

## Top picks & scoring

Results are ranked by a 0–100 composite score, and the top 3 are shown as
**Top picks today** cards with entry/stop/target, sizing, and a one-line
rationale. The score weighs:

| Weight | Component | Why |
|-------:|-----------|-----|
| 35% | reward:risk (capped at 5) | the point of the scan |
| 15% | relative strength vs benchmark | leader in a pullback, not a laggard |
| 15% | pullback depth (RSI position in the band) | deeper dip = better entry |
| 15% | entry proximity to support (within 5% = best) | tighter stop, less heat |
| 10% | pullback volume character (quiet = best) | distribution risk |
| 10% | days to earnings (30+ = best) | distance from binary events |

…minus 5 points per data-quality flag.

## Fast reruns & caching

Two cache levels, both with a 1-hour TTL:

1. **In-memory** — universe discovery, the 1-year OHLC batch download,
   benchmarks, and per-ticker fundamentals/earnings dates. Re-running with
   tweaked thresholds reuses everything and finishes in seconds; only
   universe-shaping changes (market cap, regions, sectors) or *new* stage-1
   survivors trigger fresh API calls.
2. **On-disk (SQLite)** — the same data mirrored to a small database
   (`SCREENER_CACHE_DB`, default `/tmp/screener_cache.db`), so a process
   restart within the hour reloads from disk instead of re-downloading (the
   log says so explicitly). Strictly best-effort: a missing or corrupt DB
   never breaks a scan. Note that on Render's free tier the filesystem is
   wiped on spin-down, so the disk cache mainly pays off locally or with a
   persistent disk.

## Run locally

```bash
pip install -r requirements.txt

# CLI: prints the ranked table and writes screener_results.csv
python screener.py

# Web UI: http://localhost:8000
python app.py
```

## Deploy on Render (free)

The repo contains a [`render.yaml`](render.yaml) blueprint:

1. Push this repo to GitHub.
2. In the [Render dashboard](https://dashboard.render.com): **New → Blueprint**,
   pick this repo, accept the defaults. That creates a free web service running
   `gunicorn app:app`.
3. Open the service URL. Scans run on a GitHub Actions schedule and are
   published to a data branch the site reads — a page load is a file
   read, and there is no Run button.

Notes on the free plan:

- The service **sleeps after ~15 minutes idle** and wakes on the next request
  (cold start ~1 min) — that's the "free on demand" model. Results are cached
  on the instance's disk, so they survive reloads but not a spin-down; just
  press Run again.
- The scan runs in a background thread, so the request that starts it returns
  immediately and the page polls `/status` — no request-timeout issues.
- Yahoo Finance occasionally rate-limits datacenter IPs. If a scan comes back
  with many "no data" rejections, wait a bit and rerun.

## Endpoints

| Route          | What it does                                             |
|----------------|----------------------------------------------------------|
| `/`            | UI: filters, top-pick cards, live log, ranked table      |
| `/defaults`    | JSON: default filter values + sector list                |
| `POST /run`    | Starts a scan; JSON body overrides any filter (409 if one is already running) |
| `/status`      | JSON: state, log, results, top picks, rejection summary  |
| `/results.csv` | Latest results as CSV                                    |
