"""Historical simulation: replay the screener's exact technical rules over
every stock and every eligible trading day in the already-downloaded price
data, and grade each signal the same way the journal grades live picks.

Uses only data already in memory from the last scan — zero API calls.

Honest scope (stated in the UI too):
  - technical rules only: uptrend, dip zone, pivot support, R:R, ATR and
    liquidity gates. Fundamentals (profitability, earnings dates, analyst
    ratings) are not available historically on free data, so those gates
    are not simulated.
  - entries at the signal day's close; exits exactly at stop/target, at the
    open when the price gaps past a level; positions expire after
    EXPIRE_BARS bars marked to market. No costs. One open trade per ticker
    at a time — the same conventions as the live track record.
"""

import gc
import hashlib
import time

import numpy as np
import pandas as pd
import yfinance as yf

import cache_store
import screener

EXPIRE_BARS = 40      # same as the live journal
MIN_HISTORY = 260     # bars needed before the first eligible signal day
CURVE_POINTS = 200    # cap for the equity-curve payload
HIST_PERIOD = "5y"    # simulation depth — a 1y sample only yields biased
                      # quick-resolution trades (stops), so go deep
HIST_TTL = 86400      # 5y download cached for a day
BT_CHUNK = 100    # small batches: bounded memory on a 512MB instance


def _fetch_chunk(chunk: list[str], progress=print):
    """5y bars for ONE batch of tickers, disk-cached for a day. Small enough
    (~100 tickers) to never threaten a 512MB instance."""
    key = hashlib.md5((HIST_PERIOD + ":" + ",".join(chunk)).encode()).hexdigest()
    hit, stored = cache_store.fetch(f"btc:{key}", HIST_TTL)
    if hit and stored is not None:
        return stored
    d = None
    try:
        ok, d = screener._yahoo_call(lambda: yf.download(
            chunk, period=HIST_PERIOD, auto_adjust=True,
            group_by="ticker", threads=16, progress=False), scope="chart")
        if not ok:
            d = None
    except Exception as e:
        progress(f"  history batch failed: {e}")
        d = None
    if d is None or d.empty:
        return None
    if not isinstance(d.columns, pd.MultiIndex):
        d = pd.concat({chunk[0]: d}, axis=1)
    try:
        if float(d.memory_usage().sum()) < 40e6:
            cache_store.put(f"btc:{key}", d)
    except Exception:
        pass
    return d


def _rsi_series(close: pd.Series, period: int = 14) -> pd.Series:
    delta = close.diff()
    gain = delta.clip(lower=0).rolling(period).mean()
    loss = (-delta.clip(upper=0)).rolling(period).mean()
    rs = gain / loss.replace(0, np.nan)
    return 100 - 100 / (1 + rs)


def run_backtest(p: dict, data, universe: list[str], progress=print) -> dict:
    """Simulate params `p` (already clean_params'd). When `data` is None the
    5-year history is streamed batch by batch — downloaded, simulated,
    discarded — so the deep history never has to fit in memory at once."""
    trades: list[dict] = []
    scanned = 0
    if data is None:
        n_chunks = (len(universe) + BT_CHUNK - 1) // BT_CHUNK
        progress(f"Simulating {HIST_PERIOD} of history for {len(universe)} "
                 f"stocks in {n_chunks} batches (first run downloads; "
                 f"cached for the day)...")
        for ci in range(n_chunks):
            chunk = universe[ci * BT_CHUNK:(ci + 1) * BT_CHUNK]
            if ci:
                time.sleep(1)
            frame = _fetch_chunk(chunk, progress)
            if frame is None:
                progress(f"  batch {ci + 1}/{n_chunks}: no data — skipped")
                continue
            done = _simulate_block(p, frame, chunk, trades)
            scanned += done
            progress(f"  batch {ci + 1}/{n_chunks}: {done} stocks simulated — "
                     f"{len(trades)} historical trades so far")
            del frame
            gc.collect()
        if scanned == 0:
            raise RuntimeError("could not download the 5-year history — Yahoo "
                               "is throttling price data right now; try again "
                               "in a few minutes")
    else:
        scanned = _simulate_block(p, data, universe, trades)
    return _aggregate(trades, scanned)


def _simulate_block(p: dict, data, tickers: list[str], trades: list) -> int:
    """Run the simulation for one batch of tickers against its frame.
    Appends to `trades`, returns how many stocks had enough history."""
    scanned = 0
    for ticker in tickers:
        try:
            hist = data[ticker].dropna()
        except Exception:
            continue
        if len(hist) < MIN_HISTORY:
            continue
        scanned += 1
        close, high, low = hist["Close"], hist["High"], hist["Low"]
        opens, vol = hist["Open"], hist["Volume"]
        rsi_s = _rsi_series(close)
        sma200 = close.rolling(200).mean()
        pc = close.shift(1)
        tr = pd.concat([high - low, (high - pc).abs(), (low - pc).abs()],
                       axis=1).max(axis=1)
        atr_s = tr.rolling(14).mean()
        dv30 = (vol * close).rolling(30).mean()
        res_roll = high.rolling(p["swing_lookback"]).max()

        c_v, o_v = close.values, opens.values
        h_v, l_v = high.values, low.values
        busy_until = -1
        # entries need a FULL resolution window ahead — otherwise the sample
        # only contains trades that resolved fast, i.e. mostly stop-outs
        for ti in range(210, len(hist) - EXPIRE_BARS):
            if ti <= busy_until:
                continue                       # one open trade per ticker
            price = float(c_v[ti])
            if not price > float(sma200.iloc[ti]):
                continue
            r = float(rsi_s.iloc[ti])
            if not (p["rsi_low"] <= r <= p["rsi_high"]):
                continue
            if float(dv30.iloc[ti]) < p["min_dollar_vol_m"] * 1e6:
                continue
            support = screener.last_pivot_low(
                low.iloc[max(0, ti - 119):ti + 1], p["pivot_k"])
            if support is None or support >= price:
                continue
            stop = support * (1 - p["stop_buffer_pct"] / 100)
            resistance = float(res_roll.iloc[ti])
            if resistance <= price:
                continue
            risk_ps = price - stop
            rr = (resistance - price) / risk_ps if risk_ps > 0 else float("nan")
            if not np.isfinite(rr) or rr < p["min_rr"]:
                continue
            a = float(atr_s.iloc[ti]) if np.isfinite(atr_s.iloc[ti]) else None
            if a and p["min_stop_atr"] and risk_ps / a < p["min_stop_atr"]:
                continue

            # replay forward — identical conventions to the live journal
            out_r = exit_i = None
            status = None
            last_j = ti + EXPIRE_BARS
            for j in range(ti + 1, last_j + 1):
                o, h, l = float(o_v[j]), float(h_v[j]), float(l_v[j])
                if o <= stop:
                    out_r, status, exit_i = (o - price) / risk_ps, "stop", j
                    break
                if o >= resistance:
                    out_r, status, exit_i = (o - price) / risk_ps, "target", j
                    break
                if l <= stop:
                    out_r, status, exit_i = -1.0, "stop", j
                    break
                if h >= resistance:
                    out_r, status, exit_i = rr, "target", j
                    break
            if out_r is None:
                exit_i = last_j
                out_r = (float(c_v[exit_i]) - price) / risk_ps
                status = "expired"
            busy_until = exit_i
            trades.append({"ticker": ticker,
                           "date": str(hist.index[ti])[:10],
                           "exit_date": str(hist.index[exit_i])[:10],
                           "rr_planned": round(rr, 2),
                           "outcome_r": round(float(out_r), 2),
                           "status": status})
    return scanned


def _aggregate(trades: list[dict], scanned: int) -> dict:
    if not trades:
        return {"n": 0, "n_stocks": scanned}

    rs = [t["outcome_r"] for t in trades]
    wins = [x for x in rs if x > 0]
    losses = [x for x in rs if x <= 0]
    total = float(sum(rs))
    gross_p, gross_l = float(sum(wins)), float(-sum(losses))

    trades.sort(key=lambda t: t["exit_date"])
    cum, curve = 0.0, []
    for t in trades:
        cum += t["outcome_r"]
        curve.append((t["exit_date"], round(cum, 2)))
    step = max(1, len(curve) // CURVE_POINTS)
    curve = curve[::step] + ([curve[-1]] if (len(curve) - 1) % step else [])

    by_status = {s: sum(1 for t in trades if t["status"] == s)
                 for s in ("target", "stop", "expired")}
    return {
        "n": len(trades), "n_stocks": scanned,
        "wins": len(wins), "win_rate_pct": round(len(wins) / len(trades) * 100),
        "avg_r": round(total / len(trades), 2), "total_r": round(total, 2),
        "profit_factor": (round(gross_p / gross_l, 2) if gross_l > 0 else None),
        "hit_target": by_status["target"], "hit_stop": by_status["stop"],
        "expired": by_status["expired"],
        "from": trades[0]["date"], "to": trades[-1]["date"],
        "curve": [{"date": d, "cum_r": v} for d, v in curve],
        "best": max(trades, key=lambda t: t["outcome_r"]),
        "worst": min(trades, key=lambda t: t["outcome_r"]),
    }
