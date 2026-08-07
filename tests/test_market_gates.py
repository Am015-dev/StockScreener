"""The simulation must apply the same market gates the live screener does.

Until these landed, the simulation ignored the benchmark regime and
relative-strength filters that the live tool applies to every pick — so
its profit factor described rules nobody was trading. These tests pin the
gates to constructed data where the correct answer is known, and pin the
config-identity rule that keeps the database from serving a simulation
run under different rules.
"""
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TMP = tempfile.mkdtemp(prefix="gates_")
os.environ["MARKET_DB"] = os.path.join(TMP, "market.db")
os.environ["SCREENER_CACHE_DB"] = os.path.join(TMP, "cache.db")
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).parent))

import numpy as np
import pandas as pd

import backtest
import db
import screener

N = 1600
IDX = pd.bdate_range("2022-01-03", periods=N)


def stock_frame(ticker: str, closes: np.ndarray) -> pd.DataFrame:
    """Repeating sawtooth: rises, dips to a pivot low, rises again — the
    shape the rules fire on, repeated often enough to yield many signals."""
    return pd.concat({ticker: pd.DataFrame(
        {"Open": closes, "High": closes * 1.02, "Low": closes * 0.97,
         "Close": closes, "Volume": np.full(N, 5e7)}, index=IDX)}, axis=1)


t = np.arange(N)
saw = 100 + t * 0.25 + np.sin(t / 6.0) * 6.0        # uptrend with regular dips
STOCK = stock_frame("ZZG", saw)

P = screener.clean_params({
    "min_dollar_vol_m": 1, "min_rr": 0.5, "rsi_low": 0, "rsi_high": 100,
    "min_stop_atr": 0, "pivot_k": 2, "stop_buffer_pct": 1.0,
    "max_support_dist_pct": 0, "swing_lookback": 30,
})

# ---- no gates: a baseline count of signals ----
base: list = []
backtest._simulate_block(P, STOCK, ["ZZG"], base, {})
assert len(base) > 10, f"need a decent sample to gate, got {len(base)}"
print(f"ungated baseline: {len(base)} signals")

screener._region = lambda tk: "US"

# ---- regime gate: benchmark below its own 200-day for the whole window ----
falling = pd.Series(np.linspace(400, 200, N), index=IDX)
bench_down = {"US": {"close": falling, "sma200": falling.rolling(200).mean()}}
gated: list = []
backtest._simulate_block(dict(P, require_market_uptrend=True), STOCK, ["ZZG"],
                         gated, bench_down)
assert gated == [], f"regime gate must block every signal, got {len(gated)}"

rising = pd.Series(np.linspace(200, 400, N), index=IDX)
bench_up = {"US": {"close": rising, "sma200": rising.rolling(200).mean()}}
open_regime: list = []
backtest._simulate_block(dict(P, require_market_uptrend=True), STOCK, ["ZZG"],
                         open_regime, bench_up)
assert len(open_regime) == len(base), \
    f"a rising benchmark must block nothing: {len(open_regime)} vs {len(base)}"
print("regime gate OK: blocks everything in a downtrend, nothing in an uptrend")

# ---- relative strength: benchmark outruns the stock -> RS is negative ----
strong_bench = pd.Series(100 * (1.004 ** t), index=IDX)   # compounds past the stock
bench_fast = {"US": {"close": strong_bench,
                     "sma200": strong_bench.rolling(200).mean()}}
lagging: list = []
backtest._simulate_block(dict(P, min_rs_3m=5.0), STOCK, ["ZZG"], lagging, bench_fast)
assert lagging == [], f"a lagging stock must be blocked, got {len(lagging)}"

# rises steadily — so the regime gate is genuinely open — but far slower
# than the stock, so relative strength is clearly positive. A FLAT
# benchmark would not work here: it never sits above its own 200-day
# average, so the regime gate would block everything and this test would
# pass for the wrong reason.
slow_bench = pd.Series(300 * (1.0002 ** t), index=IDX)
bench_flat = {"US": {"close": slow_bench,
                     "sma200": slow_bench.rolling(200).mean()}}
leading: list = []
backtest._simulate_block(dict(P, min_rs_3m=1.0), STOCK, ["ZZG"], leading, bench_flat)
assert len(leading) > 0, "an outperforming stock must still produce signals"
print(f"relative-strength gate OK: lagging blocked, leading kept "
      f"({len(leading)} signals)")

# a gate set to zero/absent must behave exactly like no gate at all
untouched: list = []
backtest._simulate_block(dict(P, min_rs_3m=0.0), STOCK, ["ZZG"], untouched, bench_flat)
assert len(untouched) == len(base), "min_rs_3m=0 must not filter anything"

# missing benchmark data must not silently drop every signal
no_bench: list = []
backtest._simulate_block(dict(P, require_market_uptrend=True, min_rs_3m=5.0),
                         STOCK, ["ZZG"], no_bench, {})
assert len(no_bench) == len(base), \
    "with no benchmark available the gates cannot apply — and must not pretend to"
print("absent gates and missing benchmarks both behave as no-ops")

# ---- config identity: the gates must change the hash ----
h = db.config_hash(P)
assert db.config_hash(dict(P, require_market_uptrend=not P["require_market_uptrend"])) != h
assert db.config_hash(dict(P, min_rs_3m=5.0)) != h
assert db.config_hash(dict(P, ticket_eur=999.0)) == h   # sizing is not a rule
print("config identity OK: market gates change the hash, sizing does not")

# and the reuse path must not hand back a run from different rules
assert db.record_backtest(P, [{"ticker": "ZZG", "date": "2024-01-05",
                               "exit_date": "2024-01-19", "outcome_r": 1.0,
                               "status": "target", "rr_planned": 3.0,
                               "entry": 100.0, "stop_px": 95.0,
                               "target_px": 115.0, "bars_held": 10,
                               "mfe_r": 1.2, "mae_r": -0.3}], n_stocks=1) == 1
assert db.load_backtest(P) is not None
assert db.load_backtest(dict(P, min_rs_3m=5.0)) is None, \
    "a different gate set must never reuse another configuration's simulation"
print("database reuse is scoped to the exact rule set")

print("\nALL MARKET-GATE TESTS PASSED")
