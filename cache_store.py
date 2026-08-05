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
    try:
        with _conn() as c:
            c.execute("INSERT OR REPLACE INTO kv (k, ts, v) VALUES (?, ?, ?)",
                      (key, time.time(), pickle.dumps(value)))
    except Exception:
        pass


def clear() -> None:
    try:
        with _conn() as c:
            c.execute("DELETE FROM kv")
    except Exception:
        pass
