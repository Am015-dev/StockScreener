"""The app must open with data instead of an empty form.

Two mechanisms are tested here:
  1. a simulation, once run, is rebuilt from the database — identical
     metrics, no price download;
  2. the last completed scan is stored and rehydrated, with its age
     carried through so stale levels are never presented as fresh.
"""
import os
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TMP = tempfile.mkdtemp(prefix="persist_")
os.environ["MARKET_DB"] = os.path.join(TMP, "market.db")
os.environ["JOURNAL_DB"] = os.path.join(TMP, "journal.db")
os.environ["SCREENER_CACHE_DB"] = os.path.join(TMP, "cache.db")
sys.path.insert(0, str(ROOT))

import backtest
import db

P = {"rsi_low": 35, "rsi_high": 55, "min_rr": 3.0, "swing_lookback": 60,
     "pivot_k": 3, "stop_buffer_pct": 1.0, "min_stop_atr": 0.5,
     "max_support_dist_pct": 8.0, "min_dollar_vol_m": 20}

# ---- a simulated run, of the shape _simulate_block produces ----
seq = [(3.0, "target"), (-1.0, "stop"), (2.0, "target"), (-1.0, "stop"),
       (-1.0, "stop"), (4.0, "target"), (0.5, "expired"), (-1.0, "stop")]
trades = [{"ticker": f"ZZP{i % 3}", "date": f"2025-{i + 1:02d}-05",
           "exit_date": f"2025-{i + 1:02d}-19", "outcome_r": r, "status": s,
           "rr_planned": 3.0, "entry": 100.0 + i, "stop_px": 95.0 + i,
           "target_px": 115.0 + i, "bars_held": 10, "mfe_r": 1.5, "mae_r": -0.6}
          for i, (r, s) in enumerate(seq)]

assert db.record_backtest(P, trades, n_stocks=42) == 8

# ---- rebuild straight from the database ----
stored = db.load_backtest(P)
assert stored is not None and len(stored["trades"]) == 8, stored
assert stored["n_stocks"] == 42
assert stored["ran_at"] and time.time() - stored["ran_at"] < 60

live = backtest._aggregate(list(trades), 42)
rebuilt = backtest._aggregate(stored["trades"], stored["n_stocks"])
for k in ("n", "n_stocks", "wins", "win_rate_pct", "avg_r", "total_r",
          "profit_factor", "hit_target", "hit_stop", "expired",
          "mdd_r", "mdd_pct", "sortino", "from", "to"):
    assert live[k] == rebuilt[k], (k, live[k], rebuilt[k])
assert live["portfolio"]["total_r"] == rebuilt["portfolio"]["total_r"]
print(f"database rebuild is metric-identical (PF {rebuilt['profit_factor']}, "
      f"MDD {rebuilt['mdd_r']}R, {rebuilt['n']} trades) — no download needed")

# unknown rules must not silently return someone else's simulation
assert db.load_backtest(dict(P, min_rr=99.0)) is None

# ---- run_backtest short-circuits on stored rules ----
def explode(*a, **k):
    raise AssertionError("run_backtest must not download when the DB has the run")


backtest._fetch_chunk = explode
backtest._spy_benchmark = lambda *a, **k: {"return_pct": 50.0, "mdd_pct": 15.0}
res = backtest.run_backtest(P, None, ["ZZP0", "ZZP1"], progress=lambda m: None)
assert res["from_db"] is True and res["n"] == 8, res
assert res["profit_factor"] == live["profit_factor"]
print("run_backtest served stored rules from the database without downloading")

# reuse=False forces a real run (and here proves it by hitting the guard)
try:
    backtest.run_backtest(P, None, ["ZZP0"], progress=lambda m: None, reuse=False)
    raise AssertionError("reuse=False should have attempted a download")
except AssertionError as e:
    assert "must not download" in str(e), e
print("reuse=False still forces a fresh simulation")

# ---- scan snapshots ----
now = time.time()
payload = {"results": [{"ticker": "ZZA", "score": 71}],
           "top_picks": [{"ticker": "ZZA", "score": 71}],
           "universe_size": 600, "results_ts": now, "backtest": {"n": 8}}
assert db.save_snapshot(payload) is True
back = db.latest_snapshot()
assert back["results"][0]["ticker"] == "ZZA"
assert back["universe_size"] == 600 and back["_saved_at"] >= now

# newest write wins — one row, always the latest scan
assert db.save_snapshot(dict(payload, universe_size=900)) is True
assert db.latest_snapshot()["universe_size"] == 900

# numpy/pandas scalars in scan rows are coerced, not rejected — that is why
# the writer passes default=str
assert db.save_snapshot(dict(payload, universe_size=900, odd=object())) is True

# a payload JSON genuinely cannot encode must leave the stored row intact
circular: dict = {"results_ts": now}
circular["self"] = circular
assert db.save_snapshot(circular) is False
assert db.latest_snapshot()["universe_size"] == 900
print("snapshot store keeps exactly the latest scan; a bad write can't corrupt it")

print("\nALL PERSISTENCE TESTS PASSED")
