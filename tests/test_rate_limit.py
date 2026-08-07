"""Simulate a hard Yahoo 429 on every live call and assert the scan
survives, degrades honestly, and fails closed when nothing can be served."""
import hashlib
import os
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TMP = tempfile.mkdtemp(prefix="ratelimit_")
os.environ["SCREENER_CACHE_DB"] = os.path.join(TMP, "cache.db")
os.environ["MARKET_DB"] = os.path.join(TMP, "market.db")
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).parent))

import numpy as np
import pandas as pd
import yfinance as yf
from yfinance.exceptions import YFRateLimitError

import cache_store
import screener
from _synth import pullback_hist

# no sleeping in tests, short cooldown, fully offline
screener.RETRY_DELAYS = ()
screener.RATE_LIMIT_COOLDOWN = 60
screener.QUOTE_RETRY_MAX_WAIT = 0
screener._yahoo_auth_session = lambda: (None, None)
screener._get_quote_v7 = lambda t: None


def boom(*a, **k):
    raise YFRateLimitError()  # yfinance >=1.x: fixed message, no-arg ctor


class BoomTicker:
    def __init__(self, t): self.t = t
    @property
    def info(self): boom()
    def get_earnings_dates(self, limit=4): boom()


yf.download = boom
yf.screen = boom
yf.Ticker = BoomTicker

tickers = ["FAKE1", "FAKE2", "FAKE3"]
ohlc = pd.concat({t: pullback_hist(i * 2) for i, t in enumerate(tickers)}, axis=1)

bench_close = pd.Series(np.linspace(300, 400, 260),
                        index=pd.bdate_range(end=pd.Timestamp.today(), periods=260))

params = {
    "min_dollar_vol_m": 10, "min_rr": 0.5, "rsi_low": 0, "rsi_high": 100,
    "min_stop_atr": 0, "earnings_drop_days": 0, "max_friction_pct": 0,
    "strict_gates": False, "max_support_dist_pct": 0,
    "exclude": "",
    "holdings": [{"ticker": "VUAA", "shares": 10, "cost": 80.0},
                 {"ticker": "FAKE1", "shares": 5, "cost": 60.0}],
    "cash_eur": 5000.0,
}

# ---------- case 1: everything rate-limited, warm in-memory price caches ----------
p = screener.clean_params(params)
key = (p["include_us"], p["include_eu"], tuple(p["sectors"]),
       round(p["min_mkt_cap_b"] * 1e9), p["universe_max"])
okey = hashlib.md5(",".join(tickers).encode()).hexdigest()
screener._cache.update(universe_key=key, universe=tickers, universe_ts=time.time(),
                       ohlc_key=okey, ohlc=ohlc, ohlc_ts=time.time(),
                       bench={"US": bench_close, "EU": bench_close}, bench_ts=time.time())

log = []
result = screener.run_screener(params, progress=lambda m: log.append(str(m)))
df = result["df"]
print("case1 rows:", len(df), "rejections:", result["rejections"])
assert len(df) > 0, "expected surviving setups despite rate limit"
assert all("fundamentals_unavailable" in f for f in df["flags"]), df["flags"].tolist()
assert all("earnings_unverified" in f for f in df["flags"])
assert any("rate-limited" in l for l in log), "expected rate-limit warning in log"
assert screener._rate_limited_now(), "cooldown should be open after hard 429s"
print("case 1 OK: scan survived total rate limiting, results flagged")

# ---------- case 2: cold memory cache, stale disk cache present ----------
cache_store.put(f"universe:{key}", tickers)
cache_store.put(f"ohlc:{okey}", ohlc)
cache_store.put("bench", {"US": bench_close, "EU": bench_close})
cache_store.put("info:FAKE1", {"marketCap": 50e9, "forwardEps": 5.0,
                               "sector": "Technology", "shortName": "Fake One"})
cache_store.put("earn:FAKE1", 45)
# age every row far past the TTL so only the stale path can serve them
with cache_store._conn() as c:
    c.execute("UPDATE kv SET ts = ts - 999999")
screener._cache.update(universe_key=None, universe=None, universe_ts=0,
                       ohlc_key=None, ohlc=None, ohlc_ts=0,
                       bench=None, bench_ts=0, info={}, earnings={}, finnhub={})
screener._rl.update(until={}, hits=0)  # cooldown expired, Yahoo still 429s

log2 = []
result2 = screener.run_screener(params, progress=lambda m: log2.append(str(m)))
df2 = result2["df"]
print("case2 rows:", len(df2))
assert len(df2) > 0, "expected results served from stale disk cache"
f2 = df2.set_index("ticker")
assert f2.loc["FAKE1", "sector"] == "Technology", "stale info should be used"
assert "fundamentals_unavailable" not in f2.loc["FAKE1", "flags"]
assert f2.loc["FAKE1", "days_to_earnings"] == 45, "stale earnings date should be used"
assert any("stale" in l or "last known universe" in l for l in log2), log2
print("case 2 OK: stale disk cache served universe/OHLC/bench/info/earnings")

# ---------- case 3: nothing cached at all -> graceful RuntimeError ----------
if os.path.exists(os.environ["SCREENER_CACHE_DB"]):
    os.remove(os.environ["SCREENER_CACHE_DB"])
screener._cache.update(universe_key=None, universe=None, universe_ts=0,
                       ohlc_key=None, ohlc=None, ohlc_ts=0,
                       bench=None, bench_ts=0, info={}, earnings={}, finnhub={})
screener._rl.update(until={}, hits=0)
try:
    screener.run_screener(params, progress=lambda m: None)
    raise AssertionError("expected RuntimeError with no data anywhere")
except RuntimeError as e:
    assert "rate-limiting" in str(e)
    print("case 3 OK: clear RuntimeError when no cache exists:", e)

print("\nALL RATE-LIMIT TESTS PASSED")
