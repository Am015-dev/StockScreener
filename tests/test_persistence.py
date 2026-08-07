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

# ---- scan snapshots, keyed by the filters that produced them ----
now = time.time()
STRICT = dict(P, min_rr=3.0)
LOOSE = dict(P, min_rr=2.0)


def snap(n_results, universe=600, ts=None):
    rows = [{"ticker": f"ZZ{i}", "score": 70 - i} for i in range(n_results)]
    return {"results": rows, "top_picks": rows[:3], "universe_size": universe,
            "results_ts": ts or time.time(), "backtest": {"n": 8}}


assert db.save_snapshot(STRICT, snap(1)) is True
assert db.save_snapshot(LOOSE, snap(7, universe=900)) is True

# each filter set keeps its OWN stored scan — switching back and forth
# must never hand back the other one's results
a = db.snapshot_for(STRICT)
b = db.snapshot_for(LOOSE)
assert a and len(a["results"]) == 1 and a["universe_size"] == 600
assert b and len(b["results"]) == 7 and b["universe_size"] == 900
assert a["_params"]["min_rr"] == 3.0 and b["_params"]["min_rr"] == 2.0
assert db.snapshot_for(dict(P, min_rr=1.25)) is None, \
    "filters never scanned must return nothing, not someone else's scan"
print("snapshots are keyed by filters: two filter sets, two independent scans")

# re-scanning the same filters replaces that row and leaves the other alone
assert db.save_snapshot(STRICT, snap(4, universe=650)) is True
assert len(db.snapshot_for(STRICT)["results"]) == 4
assert len(db.snapshot_for(LOOSE)["results"]) == 7, "re-scan touched the wrong row"

# presentation-only settings must not split the store
assert db.scan_hash(dict(STRICT, show_near=True)) == \
       db.scan_hash(dict(STRICT, show_near=False))
assert db.scan_hash(STRICT) != db.scan_hash(LOOSE)

# latest_snapshot is the cold-start case: newest across all filter sets
newest = db.latest_snapshot()
assert newest and len(newest["results"]) == 4, newest

idx = db.snapshot_index()
assert len(idx) == 2 and idx[0]["n_results"] == 4, idx
assert {round(s["params"]["min_rr"], 2) for s in idx} == {3.0, 2.0}
print(f"snapshot index lists {len(idx)} stored filter sets, newest first")

# numpy/pandas scalars in scan rows are coerced, not rejected — that is why
# the writer passes default=str
assert db.save_snapshot(STRICT, dict(snap(4, universe=650), odd=object())) is True

# a payload JSON genuinely cannot encode must leave the stored row intact
circular: dict = {"results_ts": now}
circular["self"] = circular
assert db.save_snapshot(STRICT, circular) is False
assert len(db.snapshot_for(STRICT)["results"]) == 4
print("a bad write cannot corrupt an existing stored scan")

print("\nALL PERSISTENCE TESTS PASSED")
