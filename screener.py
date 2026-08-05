"""
Pullback-in-Uptrend Swing Screener — with quality & policy gates
----------------------------------------------------------------
Replicates the TradingView/Yahoo pivot scan (support/resistance, R:R ranking)
and adds the filters a chart-only screener lacks:
  - profitability gate (forward EPS > 0)
  - earnings-proximity drop (no entries into binary events)
  - risk-based position sizing against YOUR max risk
  - exclusion list (held-at-cap or permanently passed tickers)

Install once:   pip install yfinance pandas numpy
Run:            python screener.py
Output:         screener_results.csv (ranked by R:R)

Also importable: run_screener() returns the results DataFrame + rejection map,
which is how the web app (app.py) drives it.
"""

import time
from collections import Counter

import numpy as np
import pandas as pd
import yfinance as yf

# ----------------------- PARAMETERS (edit here) -----------------------
# --- Universe: discovered by SECTOR via Yahoo's screener API (no scraping) ---
# Loops every sector EXCEPT Financial Services (your exclusion rule, enforced
# at the universe level), across US + European markets, cap-filtered upstream.

SECTORS = [
    "Technology", "Healthcare", "Industrials", "Consumer Cyclical",
    "Consumer Defensive", "Energy", "Basic Materials",
    "Communication Services", "Utilities", "Real Estate",
    # "Financial Services"  <- deliberately absent: the no-financials rule
]
EU_REGIONS = ["de", "fr", "ch", "gb", "it", "nl", "dk", "se", "no", "es"]
SECTOR_EXCLUDE = {"Financial Services"}  # safety net for EU strays at stage 2

EXCLUDE = {"AVGO", "AMZN", "GOOG", "NVDA", "AAL"}   # held-at-cap or permanent passes

MIN_MKT_CAP   = 10e9        # $10B
MIN_DOLLAR_VOL = 200e6      # $/day traded (price x volume) — works for high-priced stocks
RSI_LOW, RSI_HIGH = 35, 55  # pullback zone, not collapse
MIN_RR        = 3.0
TICKET_EUR    = 250.0       # actual position size, not €2000
MAX_RISK_EUR  = 90.0        # ~0.75% of an ~€11.8k book
EARNINGS_DROP_DAYS = 10     # no entries into binary events
SWING_LOOKBACK  = 60        # days for resistance (swing high)
PIVOT_K         = 3         # bars each side for a pivot low
STOP_BUFFER_PCT = 1.5       # stop % below support
REQUIRE_PROFITABLE = True   # forward EPS > 0  (the anti-AAL gate)
EURUSD = 1.08               # rough conversion for sizing math

FALLBACK_UNIVERSE = [
    "AAPL","MSFT","NVDA","AMZN","GOOG","META","AVGO","LLY","UNH","XOM",
    "JNJ","PG","HD","KO","PEP","COST","MRK","ABBV","CVX","WMT","CAT",
    "GE","GEV","BA","MU","AMD","QCOM","TXN","LIN","ETN","CCJ","TMUS",
    "RHM.DE","SIE.DE","SAP.DE","AIR.PA","MC.PA","ASML.AS","NESN.SW",
    "NOVN.SW","RHHBY","AZN.L","SHEL.L","ULVR.L","NOVO-B.CO","RACE.MI",
]
# ----------------------------------------------------------------------


def build_universe(progress=print) -> list[str]:
    syms: list[str] = []
    # 1) US, sector by sector
    try:
        for sector in SECTORS:
            q = yf.EquityQuery("and", [
                yf.EquityQuery("eq", ["region", "us"]),
                yf.EquityQuery("eq", ["sector", sector]),
                yf.EquityQuery("gt", ["intradaymarketcap", MIN_MKT_CAP]),
            ])
            try:
                res = yf.screen(q, size=250, sortField="intradaymarketcap", sortAsc=False)
                got = [x["symbol"] for x in res.get("quotes", [])]
                progress(f"  us/{sector}: {len(got)}")
                syms += got
            except Exception as e:
                progress(f"  us/{sector} failed: {e}")
        # 2) Europe, one query per region (sector filtered later via info)
        for region in EU_REGIONS:
            q = yf.EquityQuery("and", [
                yf.EquityQuery("eq", ["region", region]),
                yf.EquityQuery("gt", ["intradaymarketcap", MIN_MKT_CAP]),
            ])
            try:
                res = yf.screen(q, size=100, sortField="intradaymarketcap", sortAsc=False)
                got = [x["symbol"] for x in res.get("quotes", [])]
                progress(f"  {region}: {len(got)}")
                syms += got
            except Exception as e:
                progress(f"  {region} failed: {e}")
    except AttributeError:
        progress("  yf.screen unavailable — update yfinance: pip install -U yfinance")
    if not syms:  # final fallback: static core list
        syms = list(FALLBACK_UNIVERSE)
    seen, out = set(), []
    for t in syms:
        if t and t not in seen:
            seen.add(t)
            out.append(t)
    return out


def rsi(series: pd.Series, period: int = 14) -> float:
    delta = series.diff()
    gain = delta.clip(lower=0).rolling(period).mean()
    loss = (-delta.clip(upper=0)).rolling(period).mean()
    rs = gain / loss.replace(0, np.nan)
    return float((100 - 100 / (1 + rs)).iloc[-1])


def last_pivot_low(low: pd.Series, k: int) -> float | None:
    """Most recent bar that is the minimum of its +/- k neighbourhood."""
    vals = low.values
    for i in range(len(vals) - k - 1, k, -1):
        window = vals[i - k : i + k + 1]
        if vals[i] == window.min():
            return float(vals[i])
    return None


def run_screener(progress=print) -> dict:
    """Full scan. `progress` is called with human-readable status lines.

    Returns {"df": DataFrame, "rejections": {ticker: reason},
             "universe_size": int, "elapsed_s": float}.
    """
    t0 = time.time()
    progress("Building universe via Yahoo sector screener (~20 API calls, 30-60s)...")
    universe = build_universe(progress)
    progress(f"Universe: {len(universe)} tickers  [{time.time()-t0:.0f}s]")

    rows = []
    rejections: dict[str, str] = {}

    def reject(t, reason):
        rejections[t] = reason

    progress("Batch-downloading 1y OHLC for the whole universe (one call, ~1-2 min)...")
    data = yf.download(universe, period="1y", auto_adjust=True,
                       group_by="ticker", threads=True, progress=False)

    t1 = time.time()
    for i, ticker in enumerate(universe, 1):
        if i % 50 == 0 or i == len(universe):
            progress(f"  scanning {i}/{len(universe)}  hits so far: {len(rows)}  [{time.time()-t1:.0f}s]")
        if ticker.split(".")[0] in EXCLUDE:
            continue
        try:
            try:
                hist = data[ticker].dropna()
            except Exception:
                reject(ticker, "no data")
                continue
            if len(hist) < 210:
                reject(ticker, "insufficient history")
                continue

            price = float(hist["Close"].iloc[-1])
            sma200 = float(hist["Close"].rolling(200).mean().iloc[-1])
            avg_vol = float(hist["Volume"].tail(30).mean())

            # ---- Stage 1: cheap technical gates (no API calls) ----
            dollar_vol = avg_vol * price
            if dollar_vol < MIN_DOLLAR_VOL:
                reject(ticker, f"liquidity (${dollar_vol/1e6:.0f}M/day)")
                continue
            if price <= sma200:
                reject(ticker, "not in uptrend")
                continue
            r = rsi(hist["Close"])
            if not (RSI_LOW <= r <= RSI_HIGH):
                reject(ticker, f"RSI {r:.0f} outside {RSI_LOW}-{RSI_HIGH}")
                continue

            support = last_pivot_low(hist["Low"].tail(120), PIVOT_K)
            if support is None or support >= price:
                reject(ticker, "no valid pivot support below price")
                continue
            stop = support * (1 - STOP_BUFFER_PCT / 100)
            resistance = float(hist["High"].tail(SWING_LOOKBACK).max())
            if resistance <= price:
                reject(ticker, "price at/above swing high (no target)")
                continue
            risk_ps = price - stop
            reward_ps = resistance - price
            rr = reward_ps / risk_ps if risk_ps > 0 else float("nan")
            if not np.isfinite(rr) or rr < MIN_RR:
                reject(ticker, f"R:R {rr:.1f} < {MIN_RR}")
                continue

            # ---- Stage 2: expensive per-ticker calls, survivors only ----
            progress(f"    [stage 2] {ticker}: technical setup found, fetching fundamentals...")
            tk = yf.Ticker(ticker)
            info = tk.info or {}
            mktcap = info.get("marketCap") or 0
            fwd_eps = info.get("forwardEps")

            # ---- structural filters (fundamental) ----
            if info.get("sector") in SECTOR_EXCLUDE:
                reject(ticker, f"excluded sector ({info.get('sector')})")
                continue
            if mktcap < MIN_MKT_CAP:
                reject(ticker, f"mkt cap {mktcap/1e9:.1f}B < min")
                continue
            if REQUIRE_PROFITABLE and (fwd_eps is None or fwd_eps <= 0):
                reject(ticker, f"unprofitable/no EPS data (fwd_eps={fwd_eps})")
                continue

            # ---- earnings proximity ----
            days_to_earnings = None
            try:
                ed = tk.get_earnings_dates(limit=4)
                future = ed[ed.index > pd.Timestamp.now(tz=ed.index.tz)]
                if len(future):
                    days_to_earnings = (future.index.min() - pd.Timestamp.now(tz=ed.index.tz)).days
            except Exception:
                pass
            if days_to_earnings is not None and days_to_earnings <= EARNINGS_DROP_DAYS:
                reject(ticker, f"earnings in {days_to_earnings}d")
                continue

            # ---- sizing off YOUR risk, not a fixed ticket ----
            ticket_usd = TICKET_EUR * EURUSD
            max_risk_usd = MAX_RISK_EUR * EURUSD
            shares_by_risk = max_risk_usd / risk_ps
            shares_by_ticket = ticket_usd / price
            shares = round(min(shares_by_risk, shares_by_ticket), 4)
            actual_risk_eur = round(shares * risk_ps / EURUSD, 2)

            rows.append({
                "ticker": ticker, "price": round(price, 2),
                "support": round(support, 2), "stop": round(stop, 2),
                "resistance": round(resistance, 2),
                "RR": round(rr, 2), "RSI": round(r, 1),
                "shares": shares, "risk_EUR": actual_risk_eur,
                "days_to_earnings": days_to_earnings,
            })
        except Exception as e:
            reject(ticker, f"data error: {e}")

    elapsed = time.time() - t0
    progress(f"Scan complete in {elapsed:.0f}s total.")
    df = pd.DataFrame(rows)
    if len(df):
        df = df.sort_values("RR", ascending=False).reset_index(drop=True)
    return {"df": df, "rejections": rejections,
            "universe_size": len(universe), "elapsed_s": elapsed}


def summarize_rejections(rejections: dict[str, str], progress=print):
    if not rejections:
        return
    reasons = Counter(v.split(" (")[0] for v in rejections.values())
    progress("\n--- Rejection summary ---")
    for reason, n in reasons.most_common():
        progress(f"  {n:4d}  {reason}")
    interesting = {t: w for t, w in rejections.items()
                   if "R:R" in w or "earnings" in w or "unprofitable" in w}
    if interesting:
        progress("\n--- Near-misses (had a setup, failed a gate) ---")
        for t, why in interesting.items():
            progress(f"  {t:8s} {why}")


if __name__ == "__main__":
    result = run_screener()
    df = result["df"]
    if len(df):
        df.to_csv("screener_results.csv", index=False)
        print(df.to_string(index=False))
    else:
        print("No setups matched today.")
    summarize_rejections(result["rejections"])
