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

All parameters live at the top of [`screener.py`](screener.py).

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

| Route          | What it does                                   |
|----------------|------------------------------------------------|
| `/`            | UI: run button, live log, ranked results table |
| `POST /run`    | Starts a scan (409 if one is already running)  |
| `/status`      | JSON: state, log, results, rejection summary   |
| `/results.csv` | Latest results as CSV                          |
