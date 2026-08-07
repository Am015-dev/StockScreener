"""Realism knobs in the simulation: costs, ATR stops, hold cap, liquidity.

A simulation that charges nothing and holds forever flatters the strategy.
These pin each knob against constructed data where the right answer is
known by hand, and pin that every one of them changes config identity —
otherwise the database would serve a cost-free run under rules that charge
costs, which is worse than not caching at all.
"""
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TMP = tempfile.mkdtemp(prefix="realism_")
os.environ["MARKET_DB"] = os.path.join(TMP, "market.db")
os.environ["SCREENER_CACHE_DB"] = os.path.join(TMP, "cache.db")
sys.path.insert(0, str(ROOT))

import numpy as np
import pandas as pd

import backtest
import db
import screener

N = 1600
IDX = pd.bdate_range("2022-01-03", periods=N)
t = np.arange(N)
saw = 100 + t * 0.25 + np.sin(t / 6.0) * 6.0     # uptrend with regular dips


def frame(closes, price_scale=1.0, share_vol=5e7):
    c = closes * price_scale
    return pd.concat({"ZZR": pd.DataFrame(
        {"Open": c, "High": c * 1.02, "Low": c * 0.97, "Close": c,
         "Volume": np.full(N, share_vol)}, index=IDX)}, axis=1)


BASE = screener.clean_params({
    "min_dollar_vol_m": 1, "min_rr": 0.5, "rsi_low": 0, "rsi_high": 100,
    "min_stop_atr": 0, "pivot_k": 2, "stop_buffer_pct": 1.0,
    "max_support_dist_pct": 0, "swing_lookback": 30,
    "require_market_uptrend": False, "min_rs_3m": 0,
    "cost_pct": 0, "min_price": 0, "min_share_vol": 0, "stop_mode": "pivot",
})
F = frame(saw)


def run(**over):
    out: list = []
    backtest._simulate_block(dict(BASE, **over), F, ["ZZR"], out, {})
    return out


free = run()
assert len(free) > 10, len(free)
print(f"baseline: {len(free)} trades, total {sum(x['outcome_r'] for x in free):.1f}R")

# ---- costs: every trade must be worse, by a predictable amount ----
charged = run(cost_pct=0.20)
assert len(charged) == len(free), "costs must not change WHICH trades are taken"
for a, b in zip(free, charged):
    assert b["outcome_r"] < a["outcome_r"], (a["outcome_r"], b["outcome_r"])
    # charge = cost_pct% of entry price, expressed in R
    expected = a["outcome_r"] - 0.002 * a["entry"] / (a["entry"] - a["stop_px"])
    assert abs(b["outcome_r"] - round(expected, 2)) <= 0.011, (a, b, expected)
drag = sum(a["outcome_r"] - b["outcome_r"] for a, b in zip(free, charged))
print(f"costs OK: 0.20% round trip drags {drag:.1f}R off {len(free)} trades "
      f"({drag / len(free):.3f}R each)")

# a tighter stop must be penalised MORE, since the same % cost is a bigger
# share of a smaller planned move
tight = run(cost_pct=0.20, stop_buffer_pct=0.1)
wide = run(cost_pct=0.20, stop_buffer_pct=5.0)
tight_drag = np.mean([0.002 * x["entry"] / (x["entry"] - x["stop_px"]) for x in tight])
wide_drag = np.mean([0.002 * x["entry"] / (x["entry"] - x["stop_px"]) for x in wide])
assert tight_drag > wide_drag, (tight_drag, wide_drag)
print(f"cost scales with stop distance: tight {tight_drag:.3f}R vs wide {wide_drag:.3f}R")

# ---- ATR stops ----
atr_trades = run(stop_mode="atr", stop_atr_mult=1.5)
assert atr_trades, "ATR mode must still produce trades"
for x in atr_trades:
    assert x["stop_px"] < x["entry"]
# the ATR stop must never sit ABOVE the pivot stop: structure invalidates
# the setup, so the wider of the two is the honest one
pivot_by_date = {x["date"]: x for x in free}
compared = 0
for x in atr_trades:
    ref = pivot_by_date.get(x["date"])
    if ref:
        assert x["stop_px"] <= ref["stop_px"] + 1e-6, (x, ref)
        compared += 1
assert compared > 5, f"expected overlapping signals to compare, got {compared}"
print(f"ATR stops OK: {len(atr_trades)} trades, never tighter than the pivot "
      f"stop on {compared} shared signals")

# a bigger multiple must not produce a tighter stop
wide_atr = run(stop_mode="atr", stop_atr_mult=3.0)
w = {x["date"]: x["stop_px"] for x in wide_atr}
for x in atr_trades:
    if x["date"] in w:
        assert w[x["date"]] <= x["stop_px"] + 1e-6

# ---- hold cap: shorter holds must resolve sooner ----
short = run(max_hold_bars=15)
long_ = run(max_hold_bars=40)
assert max(x["bars_held"] for x in short) <= 15, max(x["bars_held"] for x in short)
assert max(x["bars_held"] for x in long_) <= 40
assert sum(1 for x in short if x["status"] == "expired") >= \
       sum(1 for x in long_ if x["status"] == "expired"), \
    "a shorter cap must expire at least as many trades"
print(f"hold cap OK: 15-bar max held {max(x['bars_held'] for x in short)} bars, "
      f"40-bar max held {max(x['bars_held'] for x in long_)}")

# ---- liquidity floors ----
assert run(min_price=5.0), "a $100 stock must pass a $5 floor"
# NB: `saw` ramps 100 -> ~500, so the scale must keep the WHOLE series
# under $5 — a scale that only makes the first bars cheap lets the later
# ones qualify and the test passes for the wrong reason.
cheap = frame(saw, price_scale=0.005)         # $0.50 -> $2.50 throughout
assert (saw * 0.005).max() < 5.0
out: list = []
backtest._simulate_block(dict(BASE, min_price=5.0), cheap, ["ZZR"], out, {})
assert out == [], "a sub-$5 stock must be excluded entirely"

thin = frame(saw, share_vol=100_000)
out2: list = []
backtest._simulate_block(dict(BASE, min_share_vol=500_000), thin, ["ZZR"], out2, {})
assert out2 == [], "a stock under 500k shares/day must be excluded"
assert run(min_share_vol=500_000), "a 50M-share stock must pass"
print("liquidity floors OK: sub-$5 and sub-500k-share lines excluded")

# ---- every knob must change config identity ----
h = db.config_hash(BASE)
for k, v in (("cost_pct", 0.2), ("stop_mode", "atr"), ("stop_atr_mult", 2.0),
             ("max_hold_bars", 15), ("min_price", 5.0), ("min_share_vol", 5e5)):
    assert db.config_hash(dict(BASE, **{k: v})) != h, \
        f"{k} changes results, so it must change config identity"
print("config identity OK: every realism knob changes the hash")

print("\nALL REALISM TESTS PASSED")
