"""Tiny SQLite-backed cache for the screener's downloaded market data.

Second cache level under the in-memory one in screener.py: same TTL, but it
survives process restarts. On Render's free tier the disk is wiped on
spin-down, so this mainly pays off locally or with a persistent disk — it is
strictly best-effort and must never break a scan, hence the broad excepts.

Values are pickled (DataFrames, dicts, ints, None) into a single kv table.
"""

import os
import pickle
import sqlite3
import time

DB_PATH = os.environ.get("SCREENER_CACHE_DB", "/tmp/screener_cache.db")

# Hard ceiling on the cache. Render's free tier keeps /tmp in RAM, so an
# unbounded cache is charged against the 512MB instance limit — a 5-year,
# 600-stock simulation alone cached ~70MB of price chunks. When the cache
# exceeds this, the oldest entries are dropped: they are re-downloadable by
# definition, which is exactly what makes them safe to evict.
MAX_BYTES = int(os.environ.get("SCREENER_CACHE_MAX_MB", "48")) * 1024 * 1024


def _conn():
    conn = sqlite3.connect(DB_PATH, timeout=5)
    conn.execute("CREATE TABLE IF NOT EXISTS kv (k TEXT PRIMARY KEY, ts REAL, v BLOB)")
    return conn


def fetch(key: str, ttl: float) -> tuple[bool, object]:
    """Returns (hit, value); a stored value may legitimately be None."""
    try:
        with _conn() as c:
            row = c.execute("SELECT ts, v FROM kv WHERE k = ?", (key,)).fetchone()
        if row and time.time() - row[0] < ttl:
            return True, pickle.loads(row[1])
    except Exception:
        pass
    return False, None


def put(key: str, value) -> None:
    freed = 0
    try:
        with _conn() as c:
            c.execute("INSERT OR REPLACE INTO kv (k, ts, v) VALUES (?, ?, ?)",
                      (key, time.time(), pickle.dumps(value)))
            freed = _prune(c)
    except Exception:
        pass
    if freed:
        _vacuum()   # deleting rows does not shrink the file, and on a
                    # RAM-backed /tmp the file size is the memory cost


def _prune(c) -> int:
    """Evict oldest-first until the cache fits MAX_BYTES. Returns bytes freed.

    Small keys (auth crumbs, FX rates, the universe list) are cheap and
    expensive to re-earn, so eviction only ever targets the bulk entries —
    pickled price frames — which are large and trivially re-downloaded."""
    row = c.execute("SELECT COALESCE(SUM(LENGTH(v)), 0) FROM kv").fetchone()
    total = row[0] if row else 0
    if total <= MAX_BYTES:
        return 0
    freed = 0
    for k, size in c.execute(
            "SELECT k, LENGTH(v) FROM kv WHERE LENGTH(v) > 262144 "
            "ORDER BY ts ASC").fetchall():
        c.execute("DELETE FROM kv WHERE k = ?", (k,))
        freed += size
        if total - freed <= MAX_BYTES:
            break
    return freed


def _vacuum() -> None:
    """Rewrite the database so freed pages return to the filesystem. Must
    run outside a transaction, hence its own autocommit connection."""
    try:
        c = sqlite3.connect(DB_PATH, timeout=5, isolation_level=None)
        try:
            c.execute("VACUUM")
        finally:
            c.close()
    except Exception:
        pass


def delete(key: str) -> None:
    try:
        with _conn() as c:
            c.execute("DELETE FROM kv WHERE k = ?", (key,))
    except Exception:
        pass


def clear() -> None:
    try:
        with _conn() as c:
            c.execute("DELETE FROM kv")
    except Exception:
        pass
