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

Also importable: run_screener(params) returns the results DataFrame + rejection
map, which is how the web app (app.py) drives it. Every threshold is a
parameter (see DEFAULTS) so the UI can expose them as filters. Universe and
OHLC downloads are cached for an hour, so re-running with different filter
values is fast.
"""

import os
import time
from collections import Counter

import numpy as np
import pandas as pd
import requests
import yfinance as yf

# ----------------------- PARAMETERS -----------------------
# All Yahoo sectors except Financial Services (the no-financials rule —
# enforced again at stage 2 as a safety net for EU strays).
ALL_SECTORS = [
    "Technology", "Healthcare", "Industrials", "Consumer Cyclical",
    "Consumer Defensive", "Energy", "Basic Materials",
    "Communication Services", "Utilities", "Real Estate",
]
EU_REGIONS = ["de", "fr", "ch", "gb", "it", "nl", "dk", "se", "no", "es"]
SECTOR_EXCLUDE = {"Financial Services"}  # hard rule, not user-tunable

DEFAULTS = {
    # universe
    "include_us": True,
    "include_eu": True,
    "sectors": list(ALL_SECTORS),
    "min_mkt_cap_b": 10.0,       # $B
    "min_dollar_vol_m": 200.0,   # $M/day traded (price x volume)
    "exclude": "AVGO, AMZN, GOOG, NVDA, AAL",  # held-at-cap or permanent passes
    # setup
    "rsi_low": 35.0,             # pullback zone, not collapse
    "rsi_high": 55.0,
    "min_rr": 3.0,
    "swing_lookback": 60,        # days for resistance (swing high)
    "pivot_k": 3,                # bars each side for a pivot low
    "stop_buffer_pct": 1.5,      # stop % below support
    "min_stop_atr": 1.0,         # stop must sit >= this many ATRs away (noise gate)
    # policy gates
    "require_profitable": True,  # forward EPS > 0  (the anti-AAL gate)
    "earnings_drop_days": 10,    # no entries into binary events
    "require_market_uptrend": True,  # benchmark (SPY / STOXX50) above its SMA200
    # sizing
    "ticket_eur": 250.0,
    "max_risk_eur": 90.0,        # ~0.75% of an ~€11.8k book
}
EURUSD = 1.08                    # rough conversion for sizing math

BENCHMARKS = {"US": "SPY", "EU": "^STOXX50E"}  # regime + relative-strength references

# Optional, free (finnhub.io free tier, no card): cross-checks earnings dates
# for US tickers. Leave unset and the screener still works — Yahoo-only dates,
# with an `earnings_unverified` flag when none is found.
FINNHUB_KEY = os.environ.get("FINNHUB_API_KEY", "")

FALLBACK_UNIVERSE = [
    "AAPL","MSFT","NVDA","AMZN","GOOG","META","AVGO","LLY","UNH","XOM",
    "JNJ","PG","HD","KO","PEP","COST","MRK","ABBV","CVX","WMT","CAT",
    "GE","GEV","BA","MU","AMD","QCOM","TXN","LIN","ETN","CCJ","TMUS",
    "RHM.DE","SIE.DE","SAP.DE","AIR.PA","MC.PA","ASML.AS","NESN.SW",
    "NOVN.SW","RHHBY","AZN.L","SHEL.L","ULVR.L","NOVO-B.CO","RACE.MI",
]

CACHE_TTL = 3600  # reuse universe/OHLC/fundamentals for an hour
# ----------------------------------------------------------


def _num(v, default, lo, hi, cast=float):
    try:
        return min(hi, max(lo, cast(v)))
    except (TypeError, ValueError):
        return default


def clean_params(overrides: dict | None) -> dict:
    """Merge user overrides onto DEFAULTS, coercing and clamping everything.

    Never raises on bad input — a garbage value falls back to its default.
    """
    o = overrides or {}
    p = dict(DEFAULTS)

    p["include_us"] = bool(o.get("include_us", p["include_us"]))
    p["include_eu"] = bool(o.get("include_eu", p["include_eu"]))
    if not (p["include_us"] or p["include_eu"]):
        p["include_us"] = True

    sectors = o.get("sectors", p["sectors"])
    if isinstance(sectors, (list, tuple)):
        picked = [s for s in ALL_SECTORS if s in sectors]
        p["sectors"] = picked or list(ALL_SECTORS)

    p["min_mkt_cap_b"] = _num(o.get("min_mkt_cap_b"), p["min_mkt_cap_b"], 0.5, 5000)
    p["min_dollar_vol_m"] = _num(o.get("min_dollar_vol_m"), p["min_dollar_vol_m"], 0, 100000)
    p["rsi_low"] = _num(o.get("rsi_low"), p["rsi_low"], 0, 100)
    p["rsi_high"] = _num(o.get("rsi_high"), p["rsi_high"], 0, 100)
    if p["rsi_low"] > p["rsi_high"]:
        p["rsi_low"], p["rsi_high"] = p["rsi_high"], p["rsi_low"]
    p["min_rr"] = _num(o.get("min_rr"), p["min_rr"], 0.5, 20)
    p["swing_lookback"] = _num(o.get("swing_lookback"), p["swing_lookback"], 10, 250, int)
    p["pivot_k"] = _num(o.get("pivot_k"), p["pivot_k"], 1, 10, int)
    p["stop_buffer_pct"] = _num(o.get("stop_buffer_pct"), p["stop_buffer_pct"], 0, 10)
    p["min_stop_atr"] = _num(o.get("min_stop_atr"), p["min_stop_atr"], 0, 5)
    p["require_profitable"] = bool(o.get("require_profitable", p["require_profitable"]))
    p["require_market_uptrend"] = bool(o.get("require_market_uptrend", p["require_market_uptrend"]))
    p["earnings_drop_days"] = _num(o.get("earnings_drop_days"), p["earnings_drop_days"], 0, 60, int)
    p["ticket_eur"] = _num(o.get("ticket_eur"), p["ticket_eur"], 1, 1e7)
    p["max_risk_eur"] = _num(o.get("max_risk_eur"), p["max_risk_eur"], 1, 1e6)

    exclude = o.get("exclude", p["exclude"])
    if isinstance(exclude, (list, tuple)):
        exclude = ",".join(exclude)
    p["exclude"] = str(exclude)
    return p


def _exclude_set(p: dict) -> set[str]:
    return {t.strip().upper() for t in p["exclude"].replace(";", ",").split(",") if t.strip()}


# ----------------------- cached market data -----------------------
_cache: dict = {
    "universe_key": None, "universe": None, "universe_ts": 0.0,
    "ohlc_key": None, "ohlc": None, "ohlc_ts": 0.0,
    "bench": None, "bench_ts": 0.0,   # region -> Close series
    "info": {},       # ticker -> (ts, info dict)
    "earnings": {},   # ticker -> (ts, days_to_earnings | None)
    "finnhub": {},    # ticker -> (ts, days_to_earnings | None)
}


def clear_cache():
    _cache.update(universe_key=None, universe=None, universe_ts=0.0,
                  ohlc_key=None, ohlc=None, ohlc_ts=0.0,
                  bench=None, bench_ts=0.0, info={}, earnings={}, finnhub={})


def _fresh(ts: float) -> bool:
    return time.time() - ts < CACHE_TTL


def build_universe(p: dict, progress=print) -> list[str]:
    min_cap = p["min_mkt_cap_b"] * 1e9
    key = (p["include_us"], p["include_eu"], tuple(p["sectors"]), round(min_cap))
    if _cache["universe_key"] == key and _fresh(_cache["universe_ts"]):
        age = int((time.time() - _cache["universe_ts"]) / 60)
        progress(f"Reusing cached universe ({len(_cache['universe'])} tickers, {age}m old).")
        return _cache["universe"]

    syms: list[str] = []
    try:
        if p["include_us"]:
            for sector in p["sectors"]:
                q = yf.EquityQuery("and", [
                    yf.EquityQuery("eq", ["region", "us"]),
                    yf.EquityQuery("eq", ["sector", sector]),
                    yf.EquityQuery("gt", ["intradaymarketcap", min_cap]),
                ])
                try:
                    res = yf.screen(q, size=250, sortField="intradaymarketcap", sortAsc=False)
                    got = [x["symbol"] for x in res.get("quotes", [])]
                    progress(f"  us/{sector}: {len(got)}")
                    syms += got
                except Exception as e:
                    progress(f"  us/{sector} failed: {e}")
        if p["include_eu"]:
            # sector filtered later via info (Yahoo's EU sector tagging is spotty)
            for region in EU_REGIONS:
                q = yf.EquityQuery("and", [
                    yf.EquityQuery("eq", ["region", region]),
                    yf.EquityQuery("gt", ["intradaymarketcap", min_cap]),
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
    _cache.update(universe_key=key, universe=out, universe_ts=time.time())
    return out


def _get_ohlc(universe: list[str], progress=print):
    key = hash(tuple(universe))
    if _cache["ohlc_key"] == key and _fresh(_cache["ohlc_ts"]):
        age = int((time.time() - _cache["ohlc_ts"]) / 60)
        progress(f"Reusing cached 1y OHLC ({age}m old) — filter-only rerun, fast.")
        return _cache["ohlc"]
    progress("Batch-downloading 1y OHLC for the whole universe (one call, ~1-2 min)...")
    data = yf.download(universe, period="1y", auto_adjust=True,
                       group_by="ticker", threads=True, progress=False)
    _cache.update(ohlc_key=key, ohlc=data, ohlc_ts=time.time())
    return data


def _get_info(ticker: str) -> dict:
    hit = _cache["info"].get(ticker)
    if hit and _fresh(hit[0]):
        return hit[1]
    info = yf.Ticker(ticker).info or {}
    _cache["info"][ticker] = (time.time(), info)
    return info


def _get_days_to_earnings(ticker: str) -> int | None:
    hit = _cache["earnings"].get(ticker)
    if hit and _fresh(hit[0]):
        return hit[1]
    days = None
    try:
        ed = yf.Ticker(ticker).get_earnings_dates(limit=4)
        future = ed[ed.index > pd.Timestamp.now(tz=ed.index.tz)]
        if len(future):
            days = (future.index.min() - pd.Timestamp.now(tz=ed.index.tz)).days
    except Exception:
        pass
    _cache["earnings"][ticker] = (time.time(), days)
    return days


def _get_benchmarks(progress=print) -> dict:
    """Region -> benchmark Close series (or None if unavailable)."""
    if _cache["bench"] is not None and _fresh(_cache["bench_ts"]):
        return _cache["bench"]
    bench = {}
    try:
        data = yf.download(list(BENCHMARKS.values()), period="1y", auto_adjust=True,
                           group_by="ticker", threads=True, progress=False)
        for region, sym in BENCHMARKS.items():
            try:
                close = data[sym]["Close"].dropna()
                bench[region] = close if len(close) >= 200 else None
            except Exception:
                bench[region] = None
    except Exception as e:
        progress(f"  benchmark download failed ({e}) — regime/RS checks disabled")
        bench = {region: None for region in BENCHMARKS}
    _cache.update(bench=bench, bench_ts=time.time())
    return bench


def _region(ticker: str) -> str:
    return "EU" if "." in ticker else "US"


def market_uptrend(close: pd.Series | None) -> bool | None:
    if close is None or len(close) < 200:
        return None
    return bool(float(close.iloc[-1]) > float(close.rolling(200).mean().iloc[-1]))


def rel_strength(close: pd.Series, bench_close: pd.Series | None, days: int = 63) -> float | None:
    """Stock return minus benchmark return over `days` bars, in pct points."""
    if bench_close is None or len(close) < days + 1 or len(bench_close) < days + 1:
        return None
    sr = float(close.iloc[-1] / close.iloc[-days - 1] - 1)
    br = float(bench_close.iloc[-1] / bench_close.iloc[-days - 1] - 1)
    return round((sr - br) * 100, 1)


def atr(hist: pd.DataFrame, period: int = 14) -> float | None:
    h, l, c = hist["High"], hist["Low"], hist["Close"]
    pc = c.shift(1)
    tr = pd.concat([h - l, (h - pc).abs(), (l - pc).abs()], axis=1).max(axis=1)
    v = float(tr.rolling(period).mean().iloc[-1])
    return v if np.isfinite(v) and v > 0 else None


def pullback_volume_ratio(vol: pd.Series, recent: int = 10, base: int = 30) -> float | None:
    """Recent volume vs the prior base period. < 1 = quiet pullback (healthy),
    > 1 = selling on rising volume (distribution risk)."""
    if len(vol) < recent + base:
        return None
    r = float(vol.tail(recent).mean())
    b = float(vol.iloc[-(recent + base):-recent].mean())
    return round(r / b, 2) if b > 0 else None


def _finnhub_days_to_earnings(ticker: str) -> int | None:
    """Free cross-check of the next earnings date (US symbols only on the free
    tier). Returns None when no key is set, on any error, or no date found."""
    if not FINNHUB_KEY or _region(ticker) != "US":
        return None
    hit = _cache["finnhub"].get(ticker)
    if hit and _fresh(hit[0]):
        return hit[1]
    days = None
    try:
        today = pd.Timestamp.now().normalize()
        r = requests.get(
            "https://finnhub.io/api/v1/calendar/earnings",
            params={"from": today.strftime("%Y-%m-%d"),
                    "to": (today + pd.Timedelta(days=90)).strftime("%Y-%m-%d"),
                    "symbol": ticker, "token": FINNHUB_KEY},
            timeout=10)
        events = (r.json() or {}).get("earningsCalendar", [])
        dates = [pd.Timestamp(e["date"]) for e in events if e.get("date")]
        future = [d for d in dates if d >= today]
        if future:
            days = int((min(future) - today).days)
    except Exception:
        pass
    _cache["finnhub"][ticker] = (time.time(), days)
    return days


# ----------------------- indicators -----------------------
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


# ----------------------- scoring -----------------------
def _clamp01(x: float) -> float:
    return min(max(x, 0.0), 1.0)


def score_row(row: dict, p: dict) -> tuple[int, str]:
    """0-100 composite quality score + a one-line human rationale.

    Weights: R:R (35%), relative strength vs benchmark (15%), pullback depth
    within the RSI band (15%), entry proximity to support (15%), pullback
    volume character (10%), distance to earnings (10%). Each data-quality
    flag costs 5 points.
    """
    rr_score = min(row["RR"] / 5.0, 1.0)
    span = max(p["rsi_high"] - p["rsi_low"], 1e-9)
    pullback = _clamp01((p["rsi_high"] - row["RSI"]) / span)
    dist_to_support = (row["price"] - row["support"]) / row["price"]
    support_prox = 1.0 - min(dist_to_support / 0.05, 1.0)  # within 5% of support = best
    dte = row.get("days_to_earnings")
    earn = 0.7 if dte is None else min(dte / 30.0, 1.0)
    rs = row.get("rs_3m")
    rs_score = 0.5 if rs is None else _clamp01((rs + 10.0) / 20.0)   # -10%..+10% -> 0..1
    vr = row.get("vol_ratio")
    vol_score = 0.5 if vr is None else _clamp01((1.5 - vr) / 1.0)    # 0.5x -> 1, 1.5x -> 0

    flags = [f for f in str(row.get("flags") or "").split(",") if f]
    score = round(100 * (0.35 * rr_score + 0.15 * rs_score + 0.15 * pullback +
                         0.15 * support_prox + 0.10 * vol_score + 0.10 * earn)
                  - 5 * len(flags))
    score = max(min(score, 100), 0)

    bits = [f"R:R {row['RR']:.1f}",
            f"entry {dist_to_support * 100:.1f}% above support",
            f"RSI {row['RSI']:.0f}"]
    if rs is not None:
        bits.append(f"RS vs mkt {rs:+.1f}%")
    if vr is not None:
        bits.append(f"pullback vol {vr:.2f}x" + (" (quiet)" if vr < 1 else ""))
    bits.append("earnings date unknown" if dte is None else f"earnings in {dte}d")
    if flags:
        bits.append("⚠ " + ", ".join(flags))
    return score, " · ".join(bits)


# ----------------------- the scan -----------------------
def run_screener(params: dict | None = None, progress=print) -> dict:
    """Full scan. `params` overrides DEFAULTS (see clean_params).

    Returns {"df": DataFrame, "rejections": {ticker: reason},
             "universe_size": int, "elapsed_s": float, "params": dict}.
    """
    p = clean_params(params)
    exclude = _exclude_set(p)
    allowed_sectors = set(p["sectors"])
    t0 = time.time()

    progress("Building universe via Yahoo sector screener...")
    universe = build_universe(p, progress)
    progress(f"Universe: {len(universe)} tickers  [{time.time()-t0:.0f}s]")

    rows = []
    rejections: dict[str, str] = {}

    def reject(t, reason):
        rejections[t] = reason

    data = _get_ohlc(universe, progress)

    bench = _get_benchmarks(progress)
    regime = {region: market_uptrend(close) for region, close in bench.items()}
    for region, up in regime.items():
        label = {True: "UPTREND", False: "DOWNTREND", None: "unknown"}[up]
        progress(f"Market regime {region} ({BENCHMARKS[region]}): {label}")
    if p["require_market_uptrend"] and not any(v for v in regime.values()):
        if all(v is False for v in regime.values()):
            progress("Both benchmarks below SMA200 — regime gate will reject everything. "
                     "Untick 'require market uptrend' to override.")

    t1 = time.time()
    for i, ticker in enumerate(universe, 1):
        if i % 50 == 0 or i == len(universe):
            progress(f"  scanning {i}/{len(universe)}  hits so far: {len(rows)}  [{time.time()-t1:.0f}s]")
        if ticker.split(".")[0].upper() in exclude:
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
            region = _region(ticker)

            # ---- Stage 1: cheap technical gates (no API calls) ----
            if p["require_market_uptrend"] and regime.get(region) is False:
                reject(ticker, f"market regime ({BENCHMARKS[region]} below SMA200)")
                continue
            dollar_vol = avg_vol * price
            if dollar_vol < p["min_dollar_vol_m"] * 1e6:
                reject(ticker, f"liquidity (${dollar_vol/1e6:.0f}M/day)")
                continue
            if price <= sma200:
                reject(ticker, "not in uptrend")
                continue
            r = rsi(hist["Close"])
            if not (p["rsi_low"] <= r <= p["rsi_high"]):
                reject(ticker, f"RSI {r:.0f} outside {p['rsi_low']:.0f}-{p['rsi_high']:.0f}")
                continue

            support = last_pivot_low(hist["Low"].tail(120), p["pivot_k"])
            if support is None or support >= price:
                reject(ticker, "no valid pivot support below price")
                continue
            stop = support * (1 - p["stop_buffer_pct"] / 100)
            resistance = float(hist["High"].tail(p["swing_lookback"]).max())
            if resistance <= price:
                reject(ticker, "price at/above swing high (no target)")
                continue
            risk_ps = price - stop
            reward_ps = resistance - price
            rr = reward_ps / risk_ps if risk_ps > 0 else float("nan")
            if not np.isfinite(rr) or rr < p["min_rr"]:
                reject(ticker, f"R:R {rr:.1f} < {p['min_rr']:.1f}")
                continue

            # ---- setup-quality validators (still free: same OHLC data) ----
            a = atr(hist)
            stop_atr = round(risk_ps / a, 2) if a else None
            if stop_atr is not None and stop_atr < p["min_stop_atr"]:
                reject(ticker, f"stop inside noise ({stop_atr:.1f} ATR < {p['min_stop_atr']:.1f})")
                continue
            rs_3m = rel_strength(hist["Close"], bench.get(region))
            vol_ratio = pullback_volume_ratio(hist["Volume"])
            flags: list[str] = []

            # ---- Stage 2: expensive per-ticker calls, survivors only ----
            progress(f"    [stage 2] {ticker}: technical setup found, fetching fundamentals...")
            info = _get_info(ticker)
            mktcap = info.get("marketCap") or 0
            fwd_eps = info.get("forwardEps")
            sector = info.get("sector")

            # ---- structural filters (fundamental) ----
            if sector in SECTOR_EXCLUDE:
                reject(ticker, f"excluded sector ({sector})")
                continue
            if sector and sector not in allowed_sectors:
                reject(ticker, f"sector not selected ({sector})")
                continue
            if mktcap < p["min_mkt_cap_b"] * 1e9:
                reject(ticker, f"mkt cap {mktcap/1e9:.1f}B < min")
                continue
            # profitability gate with a free fallback: Yahoo's forwardEps is
            # often missing for EU names — fall back to trailing EPS (flagged)
            # instead of falsely rejecting as "unprofitable".
            eps_used = fwd_eps
            if eps_used is None and info.get("trailingEps") is not None:
                eps_used = info["trailingEps"]
                flags.append("eps_fallback")
            if p["require_profitable"] and (eps_used is None or eps_used <= 0):
                reject(ticker, f"unprofitable/no EPS data (fwd_eps={fwd_eps})")
                continue

            # ---- earnings proximity (Yahoo, cross-checked via free Finnhub) ----
            days_to_earnings = _get_days_to_earnings(ticker)
            days_fh = _finnhub_days_to_earnings(ticker)
            if days_to_earnings is None and days_fh is not None:
                days_to_earnings = days_fh
                flags.append("earnings_from_finnhub")
            elif (days_to_earnings is not None and days_fh is not None
                  and abs(days_to_earnings - days_fh) > 2):
                days_to_earnings = min(days_to_earnings, days_fh)  # conservative
                flags.append("earnings_sources_disagree")
            if days_to_earnings is None:
                flags.append("earnings_unverified")
            if days_to_earnings is not None and days_to_earnings <= p["earnings_drop_days"]:
                reject(ticker, f"earnings in {days_to_earnings}d")
                continue

            # ---- sizing off YOUR risk, not a fixed ticket ----
            ticket_usd = p["ticket_eur"] * EURUSD
            max_risk_usd = p["max_risk_eur"] * EURUSD
            shares_by_risk = max_risk_usd / risk_ps
            shares_by_ticket = ticket_usd / price
            shares = round(min(shares_by_risk, shares_by_ticket), 4)
            actual_risk_eur = round(shares * risk_ps / EURUSD, 2)

            row = {
                "ticker": ticker, "name": info.get("shortName") or ticker,
                "sector": sector or "?",
                "price": round(price, 2),
                "support": round(support, 2), "stop": round(stop, 2),
                "resistance": round(resistance, 2),
                "RR": round(rr, 2), "RSI": round(r, 1),
                "rs_3m": rs_3m, "vol_ratio": vol_ratio, "stop_atr": stop_atr,
                "shares": shares, "risk_EUR": actual_risk_eur,
                "days_to_earnings": days_to_earnings,
                "flags": ",".join(flags),
            }
            row["score"], row["rationale"] = score_row(row, p)
            rows.append(row)
        except Exception as e:
            reject(ticker, f"data error: {e}")

    elapsed = time.time() - t0
    progress(f"Scan complete in {elapsed:.0f}s total.")
    df = pd.DataFrame(rows)
    if len(df):
        df = df.sort_values("score", ascending=False).reset_index(drop=True)
    return {"df": df, "rejections": rejections, "universe_size": len(universe),
            "elapsed_s": elapsed, "params": p}


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
        top = df.head(3)
        print("\n=== TOP PICKS TODAY ===")
        for _, r in top.iterrows():
            print(f"  {r['ticker']:10s} score {r['score']:3d}  {r['rationale']}")
        print()
        print(df.drop(columns=["rationale"]).to_string(index=False))
    else:
        print("No setups matched today.")
    summarize_rejections(result["rejections"])
