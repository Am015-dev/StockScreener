# StockScreener

Pullback-in-Uptrend swing screener (US + EU large caps) with quality & policy
gates, plus a small web UI you can deploy for free on Render.

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

## Fast reruns

The universe discovery and the 1-year OHLC download are cached in memory for
an hour. The first scan takes a few minutes; re-running with tweaked filters
reuses the cached data and finishes in seconds (only stage-2 fundamentals for
*new* survivors trigger fresh API calls, and those are cached too).

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
3. Open the service URL, press **Run screener**, and watch the live progress
   log. A full scan takes a few minutes; results are ranked by R:R and
   downloadable as CSV.

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
