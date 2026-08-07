"""Memory ceiling for the simulation path.

Render's free tier gives 512MB and keeps /tmp in RAM, so on-disk caches are
charged against the same budget as the process. An OOM restart traced back
to two things: a bars_daily table that stored every downloaded OHLCV row
and was never read back, and an unbounded price-chunk cache. This test
pins both, and the process RSS, so neither can creep back.
"""
import gc
import os
import resource
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TMP = tempfile.mkdtemp(prefix="mem_")
os.environ["MARKET_DB"] = os.path.join(TMP, "market.db")
os.environ["SCREENER_CACHE_DB"] = os.path.join(TMP, "cache.db")
os.environ["SCREENER_CACHE_MAX_MB"] = "16"     # smaller ceiling, faster test
sys.path.insert(0, str(ROOT))

import numpy as np
import pandas as pd

import backtest
import cache_store
import db
import screener

RSS_CEILING_MB = 320      # generous headroom under a 512MB instance
BARS = 1250               # ~5 years of daily bars
CHUNK = 100               # tickers per download batch, as the live sim streams
CHUNKS = 7                # ~700 stocks, a full universe pass


def rss_mb() -> float:
    return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024


def db_bytes() -> int:
    return sum(os.path.getsize(os.path.join(TMP, f))
               for f in os.listdir(TMP) if f.endswith((".db", ".db-wal")))


idx = pd.bdate_range("2021-01-04", periods=BARS)
rng = np.random.default_rng(7)


def make_chunk(offset: int):
    frames = {}
    for i in range(CHUNK):
        c = 100 + rng.normal(0, 1, BARS).cumsum()
        frames[f"ZM{offset + i}"] = pd.DataFrame(
            {"Open": c, "High": c * 1.02, "Low": c * 0.97, "Close": c,
             "Volume": rng.uniform(1e6, 9e6, BARS)}, index=idx)
    return pd.concat(frames, axis=1), list(frames)


P = screener.clean_params({})

# ---- the legacy write-only bars table must be dropped AND its space
# ---- actually returned to the filesystem
import datetime
import sqlite3
import time

legacy = sqlite3.connect(os.environ["MARKET_DB"])
legacy.executescript(db._SCHEMA)
legacy.execute("""CREATE TABLE IF NOT EXISTS bars_daily (
    instrument_id INTEGER, d TEXT, o REAL, h REAL, l REAL, c REAL, volume REAL,
    PRIMARY KEY (instrument_id, d))""")
day0 = datetime.date(2021, 1, 4)
legacy.executemany(
    "INSERT INTO bars_daily VALUES (?,?,?,?,?,?,?)",
    [(t, (day0 + datetime.timedelta(days=b)).isoformat(), 1., 2., 3., 4., 5.)
     for t in range(400) for b in range(700)])       # 280k rows, as a real run left
legacy.commit()
legacy.close()

before = db_bytes()
assert before > 10e6, f"fixture should be big enough to matter, got {before/1e6:.0f}MB"
t0 = time.time()
db._drop_dead_weight()
after = db_bytes()
# NB: the footprint must SHRINK. A VACUUM in WAL mode writes the rebuilt
# database into the log, so without a truncating checkpoint this DOUBLES
# instead — which is how the first version of this migration behaved.
assert after < before * 0.1, \
    f"space not reclaimed: {before/1e6:.0f}MB -> {after/1e6:.0f}MB (WAL not checkpointed?)"
assert not sqlite3.connect(os.environ["MARKET_DB"]).execute(
    "SELECT 1 FROM sqlite_master WHERE name='bars_daily'").fetchone()
print(f"legacy bars table reclaimed: {before/1e6:.0f}MB -> {after/1e6:.2f}MB "
      f"in {time.time() - t0:.2f}s")

# and it must not pay that cost again on every subsequent startup
t0 = time.time()
db._drop_dead_weight()
assert time.time() - t0 < 0.1, "migration must skip once there is nothing to drop"

assert not hasattr(db, "record_bars"), \
    "record_bars stored rows nothing ever read — it must not come back"
assert "bars_daily" not in db._SCHEMA, "bars_daily must not be recreated"

# two full simulation passes, exactly how the live run streams them
for run in (1, 2):
    trades: list = []
    for ci in range(CHUNKS):
        frame, tickers = make_chunk(ci * CHUNK)
        cache_store.put(f"btc:run{run}chunk{ci}", frame)
        backtest._simulate_block(P, frame, tickers, trades, {})
        del frame
        gc.collect()
    db.record_backtest(dict(P, min_rr=3.0 + run), trades, n_stocks=CHUNKS * CHUNK)
    print(f"  pass {run}: {len(trades)} trades · RSS {rss_mb():.0f} MB · "
          f"databases {db_bytes() / 1e6:.0f} MB")

peak, stored = rss_mb(), db_bytes()
assert peak < RSS_CEILING_MB, f"peak RSS {peak:.0f}MB exceeds {RSS_CEILING_MB}MB"

# the cache must respect its ceiling rather than growing with every chunk
cap_mb = cache_store.MAX_BYTES / 1e6
cache_mb = os.path.getsize(os.path.join(TMP, "cache.db")) / 1e6
# the FILE must shrink, not just the row count: on a RAM-backed /tmp the
# file size is the memory cost, and DELETE alone never returns pages
assert cache_mb < cap_mb * 1.3, \
    f"cache file grew to {cache_mb:.0f}MB against a {cap_mb:.0f}MB ceiling"
print(f"cache file held at {cache_mb:.0f}MB against a {cap_mb:.0f}MB ceiling")

# small, expensive-to-re-earn entries must survive eviction
cache_store.put("yahoo_auth", {"crumb": "abc", "cookies": {"A": "1"}})
for i in range(20):
    frame, _ = make_chunk(9000 + i * CHUNK)
    cache_store.put(f"btc:flood{i}", frame)
    del frame
hit, auth = cache_store.fetch("yahoo_auth", 86400)
assert hit and auth["crumb"] == "abc", \
    "eviction must target bulk price frames, never small credentials"
print("eviction spares small entries (auth crumb survived a cache flood)")

# and the simulation still works after everything has been evicted
final: list = []
frame, tickers = make_chunk(0)
backtest._simulate_block(P, frame, tickers, final, {})
assert len(final) > 0, "simulation must still run with a cold cache"

print(f"\npeak RSS {peak:.0f} MB (ceiling {RSS_CEILING_MB}) · "
      f"databases {stored / 1e6:.0f} MB")
print("ALL MEMORY TESTS PASSED")
