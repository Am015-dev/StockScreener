"""Unit tests for the simulation's institutional metrics: max drawdown,
profit factor, Sortino ratio, and the SPY benchmark — all against trade
sequences constructed so the correct answers are known by hand."""
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TMP = tempfile.mkdtemp(prefix="btm_")
os.environ["SCREENER_CACHE_DB"] = os.path.join(TMP, "cache.db")
os.environ["MARKET_DB"] = os.path.join(TMP, "market.db")
sys.path.insert(0, str(ROOT))

import numpy as np
import pandas as pd

import backtest


def T(i, r, month, status):
    return {"ticker": f"T{i}", "date": f"2025-{month:02d}-01",
            "exit_date": f"2025-{month:02d}-15", "outcome_r": r,
            "status": status, "rr_planned": 3.0, "entry": 100.0,
            "stop_px": 95.0, "target_px": 115.0, "bars_held": 10,
            "mfe_r": max(r, 0.2), "mae_r": min(r, -0.1)}


# ---- known sequence, one trade per month across 7 months ----
# cum R:   2, 5, 4, 3, 1.5, 5.5, 4.5   -> peak 5 then trough 1.5 = 3.5R MDD
seq = [(2.0, "target"), (3.0, "target"), (-1.0, "stop"), (-1.0, "stop"),
       (-1.5, "stop"), (4.0, "target"), (-1.0, "stop")]
trades = [T(i, r, i + 1, s) for i, (r, s) in enumerate(seq)]

res = backtest._aggregate(list(trades), scanned=50)
assert res["n"] == 7 and res["n_stocks"] == 50
assert res["wins"] == 3 and res["win_rate_pct"] == 43
assert res["total_r"] == 4.5 and res["avg_r"] == 0.64
assert res["profit_factor"] == 2.0, res["profit_factor"]      # 9 / 4.5
assert res["hit_target"] == 3 and res["hit_stop"] == 4 and res["expired"] == 0
assert res["mdd_r"] == 3.5, res["mdd_r"]                      # peak 5 -> 1.5
assert res["mdd_pct"] == 3.5                                  # at 1% risk/trade
assert res["return_pct"] == 4.5 and res["risk_pct_basis"] == 1.0
assert res["n_months"] == 7
# Sortino by hand: monthly returns at 1% risk = R/100;
# mean*12 / (downside_dev * sqrt(12)) = 2.57
assert res["sortino"] == 2.57, res["sortino"]
assert res["curve"][-1]["cum_r"] == 4.5
assert res["best"]["outcome_r"] == 4.0 and res["worst"]["outcome_r"] == -1.5

# order independence: _aggregate must sort by exit date itself, otherwise
# the drawdown/curve math silently depends on input order
res_rev = backtest._aggregate(list(reversed(trades)), scanned=50)
assert res_rev["mdd_r"] == 3.5 and res_rev["total_r"] == 4.5
print("metrics on known 7-trade sequence OK "
      f"(PF {res['profit_factor']}, MDD {res['mdd_r']}R, Sortino {res['sortino']})")

# ---- all wins: no losses -> PF undefined (None), zero drawdown,
# ---- no downside deviation -> Sortino undefined (None)
wins_only = [T(i, 1.0, i + 1, "target") for i in range(6)]
res_w = backtest._aggregate(wins_only, scanned=6)
assert res_w["profit_factor"] is None
assert res_w["mdd_r"] == 0.0 and res_w["sortino"] is None

# ---- fewer than 6 months of history -> Sortino suppressed, not faked
short = [T(i, r, (i % 3) + 1, "target" if r > 0 else "stop")
         for i, r in enumerate([2.0, -1.0, 1.0, -1.0, 3.0, -1.0])]
res_s = backtest._aggregate(short, scanned=6)
assert res_s["sortino"] is None and res_s["n_months"] == 3

# ---- no trades at all ----
assert backtest._aggregate([], scanned=5) == {"n": 0, "n_stocks": 5}
print("edge cases (all-wins / short window / empty) OK")

# ---- SPY benchmark: synthetic path 100 -> 110 -> 88 -> 130 ----
# return = +30.0%, max drawdown = 88/110 - 1 = -20.0%
idx = pd.bdate_range("2021-01-04", periods=260)
closes = np.concatenate([np.linspace(100, 110, 80),
                         np.linspace(110, 88, 60),
                         np.linspace(88, 130, 120)])
spy_frame = pd.concat({"SPY": pd.DataFrame(
    {"Open": closes, "High": closes, "Low": closes, "Close": closes,
     "Volume": np.full(260, 1e8)}, index=idx)}, axis=1)

backtest._fetch_chunk = lambda chunk, progress=print: spy_frame
spy = backtest._spy_benchmark(str(idx[0].date()), str(idx[-1].date()))
assert spy == {"return_pct": 30.0, "mdd_pct": 20.0}, spy
# a window Yahoo can't serve must yield None, never a made-up benchmark
backtest._fetch_chunk = lambda chunk, progress=print: None
assert backtest._spy_benchmark("2021-01-04", "2021-12-31") is None
print("SPY benchmark math OK (+30.0% return, -20.0% MDD on synthetic path)")

print("\nALL BACKTEST-METRICS TESTS PASSED")
