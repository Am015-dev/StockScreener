"""Latency gate: the database reads that sit on the /status and /run hot
paths must answer in under 800ms even at a representative data volume —
5,000 simulated trades across 800 instruments in the edge store and a
400-pick journal. A regression here means the UI stalls on every poll."""
import os
import sys
import tempfile
import time
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TMP = tempfile.mkdtemp(prefix="latency_")
os.environ["SCREENER_CACHE_DB"] = os.path.join(TMP, "cache.db")
os.environ["MARKET_DB"] = os.path.join(TMP, "market.db")
os.environ["JOURNAL_DB"] = os.path.join(TMP, "journal.db")
sys.path.insert(0, str(ROOT))

import pandas as pd

import db
import journal

BUDGET_S = 0.8

P = {"rsi_low": 35, "rsi_high": 55, "min_rr": 3.0, "swing_lookback": 60,
     "pivot_k": 3, "stop_buffer_pct": 1.0, "min_stop_atr": 0.5,
     "max_support_dist_pct": 8.0, "min_dollar_vol_m": 20}

# ---- seed the edge store: 5,000 trades over 800 symbols ----
base = date(2021, 1, 4)
trades = []
for i in range(5000):
    # (symbol, date) unique — the store replaces duplicate signal keys
    d = base + timedelta(days=(i // 800) * 11 + i % 3)
    r = [3.0, -1.0, -1.0, 0.4, 2.1][i % 5]
    trades.append({"ticker": f"ZZS{i % 800}", "date": d.isoformat(),
                   "exit_date": (d + timedelta(days=9)).isoformat(),
                   "outcome_r": r, "status": "target" if r > 0 else "stop",
                   "rr_planned": 3.0, "entry": 100.0, "stop_px": 95.0,
                   "target_px": 115.0, "bars_held": 7,
                   "mfe_r": 1.0, "mae_r": -0.5})
t0 = time.perf_counter()
n = db.record_backtest(P, trades)
print(f"seeded edge store: {n} trades in {time.perf_counter() - t0:.2f}s")
assert n == 5000, n


def timed(label, fn):
    """Best of three runs must beat the budget; returns the last result."""
    best, out = None, None
    for _ in range(3):
        t0 = time.perf_counter()
        out = fn()
        dt = time.perf_counter() - t0
        best = dt if best is None else min(best, dt)
    print(f"  {label}: {best * 1000:.0f}ms (budget {BUDGET_S * 1000:.0f}ms)")
    assert best < BUDGET_S, f"{label} took {best:.3f}s, budget {BUDGET_S}s"
    return out


edge = timed("db.edge_for (per-stock win rates on every scan)",
             lambda: db.edge_for(P))
assert edge.get("*") and edge["*"]["n"] == 5000
assert len(edge) > 700, len(edge)

rows = timed("db.export_edge (browser persistence mirror)", db.export_edge)
assert len(rows) >= 800

# ---- seed the journal: 400 picks, all resolved via synthetic bars ----
ts = time.time() - 86400 * 10
picks = [{"ticker": f"ZZJ{i}", "price": 100.0, "stop": 95.0,
          "resistance": 115.0, "RR": 3.0, "score": 40 + i % 60,
          "name": f"Fake {i}", "sector": "Tech", "shares": 1, "risk_EUR": 10}
         for i in range(400)]
assert journal.record_picks(picks, ts) == 400
idx = pd.bdate_range(end=pd.Timestamp.today(), periods=5)
up = pd.DataFrame({"Open": [101, 104, 108, 112, 116],
                   "High": [102, 105, 116, 116, 117],
                   "Low": [100, 103, 107, 111, 115],
                   "Close": [101, 104, 114, 115, 116]}, index=idx)
assert journal.update_outcomes(lambda t: up) == 400

snap = timed("journal.snapshot (track record on every /status poll)",
             journal.snapshot)
assert snap["n_resolved"] == 400 and snap["n_wins"] == 400

dump = timed("journal.export_all (browser persistence mirror)",
             journal.export_all)
assert len(dump) == 400

print("\nALL LATENCY GATES PASSED (budget 800ms per hot-path read)")
