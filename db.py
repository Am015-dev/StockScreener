"""Persistent market database — the product's memory.

Replaces "pickled DataFrames in a kv cache" with queryable, incrementally
updatable tables:

  instruments      ticker registry (symbol, kind, currency)
  bars_daily       canonical OHLCV, append-only, idempotent upserts
  strategy_configs every distinct set of *technical* rules, content-addressed
  signals          one row per (config, ticker, day) the rules fired
  signal_outcomes  how each signal resolved (target/stop/expired, R, MFE/MAE)
  edge_stats       precomputed per-ticker aggregates the UI reads directly

The headline query this enables: "with YOUR current rules, this stock's
setup hit the target before the stop N% of the time over 5 years" — an
indexed read of edge_stats, zero compute at page load.

Config identity is the hash of the technical parameters only — fundamentals
gates (profitability, earnings, analyst) can't be simulated historically on
free data, so they don't participate in the hash.

On Render's free tier this DB lives on ephemeral disk and rebuilds from a
simulation re-run after a deploy; on a persistent disk it accumulates.
"""

import hashlib
import json
import os
import sqlite3

DB_PATH = os.environ.get("MARKET_DB", "/tmp/screener_market.db")

# parameters that change what the simulation finds — nothing else
TECH_PARAMS = ("rsi_low", "rsi_high", "min_rr", "swing_lookback", "pivot_k",
               "stop_buffer_pct", "min_stop_atr", "max_support_dist_pct",
               "min_dollar_vol_m",
               # market gates: simulated since the regime/RS filters landed.
               # They MUST be part of config identity — otherwise the reuse
               # path would serve a simulation run under different rules.
               "require_market_uptrend", "min_rs_3m")
MIN_SAMPLE = 5   # never show a win rate computed from fewer signals

_SCHEMA = """
CREATE TABLE IF NOT EXISTS instruments (
    instrument_id INTEGER PRIMARY KEY,
    symbol   TEXT NOT NULL UNIQUE,
    kind     TEXT NOT NULL DEFAULT 'stock',
    currency TEXT NOT NULL DEFAULT 'USD'
);
CREATE TABLE IF NOT EXISTS bars_daily (
    instrument_id INTEGER NOT NULL,
    d  TEXT NOT NULL,
    o REAL, h REAL, l REAL, c REAL, volume REAL,
    PRIMARY KEY (instrument_id, d)
) WITHOUT ROWID;
CREATE INDEX IF NOT EXISTS bars_by_date ON bars_daily(d);
CREATE TABLE IF NOT EXISTS strategy_configs (
    config_id  INTEGER PRIMARY KEY,
    param_hash TEXT NOT NULL UNIQUE,
    params_json TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS signals (
    signal_id INTEGER PRIMARY KEY,
    config_id INTEGER NOT NULL,
    instrument_id INTEGER NOT NULL,
    d TEXT NOT NULL,
    source TEXT NOT NULL DEFAULT 'backtest',
    entry REAL, stop REAL, target REAL, rr REAL,
    UNIQUE (config_id, instrument_id, d, source)
);
CREATE TABLE IF NOT EXISTS signal_outcomes (
    signal_id INTEGER PRIMARY KEY,
    status TEXT NOT NULL,
    exit_date TEXT, exit_price REAL,
    outcome_r REAL, bars_held INTEGER,
    mfe_r REAL, mae_r REAL
);
CREATE TABLE IF NOT EXISTS backtest_runs (
    config_id INTEGER PRIMARY KEY,
    ran_at REAL, n_stocks INTEGER, n_trades INTEGER,
    d_from TEXT, d_to TEXT
);
CREATE TABLE IF NOT EXISTS snapshots (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    saved_at REAL NOT NULL,
    payload_json TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS edge_stats (
    config_id INTEGER NOT NULL,
    instrument_id INTEGER,              -- NULL row = whole-universe aggregate
    n INTEGER, wins INTEGER, win_rate REAL,
    avg_r REAL, total_r REAL,
    PRIMARY KEY (config_id, instrument_id)
);
"""


def _conn():
    conn = sqlite3.connect(DB_PATH, timeout=15)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.executescript(_SCHEMA)
    return conn


def config_hash(params: dict) -> str:
    subset = {k: params.get(k) for k in TECH_PARAMS}
    return hashlib.md5(json.dumps(subset, sort_keys=True).encode()).hexdigest()


def _config_id(conn, params: dict) -> int:
    h = config_hash(params)
    conn.execute("INSERT OR IGNORE INTO strategy_configs (param_hash, params_json) "
                 "VALUES (?, ?)",
                 (h, json.dumps({k: params.get(k) for k in TECH_PARAMS},
                                sort_keys=True)))
    return conn.execute("SELECT config_id FROM strategy_configs WHERE param_hash=?",
                        (h,)).fetchone()[0]


def _iid(conn, symbol: str, currency: str = "USD", kind: str = "stock") -> int:
    conn.execute("INSERT OR IGNORE INTO instruments (symbol, kind, currency) "
                 "VALUES (?, ?, ?)", (symbol, kind, currency))
    return conn.execute("SELECT instrument_id FROM instruments WHERE symbol=?",
                        (symbol,)).fetchone()[0]


def record_bars(frame, tickers: list[str], ccy_of=None) -> int:
    """Idempotent upsert of a MultiIndex OHLCV frame into bars_daily."""
    total = 0
    try:
        with _conn() as c:
            for t in tickers:
                try:
                    sub = frame[t].dropna()
                except Exception:
                    continue
                if not len(sub):
                    continue
                iid = _iid(c, t, (ccy_of(t) if ccy_of else "USD"))
                rows = [(iid, str(idx)[:10], float(r["Open"]), float(r["High"]),
                         float(r["Low"]), float(r["Close"]), float(r["Volume"]))
                        for idx, r in sub.iterrows()]
                c.executemany("INSERT OR REPLACE INTO bars_daily "
                              "(instrument_id, d, o, h, l, c, volume) "
                              "VALUES (?,?,?,?,?,?,?)", rows)
                total += len(rows)
    except Exception:
        pass
    return total


def record_backtest(params: dict, trades: list[dict], ccy_of=None,
                    n_stocks: int = 0) -> int:
    """Persist a full simulation: replaces this config's previous signals and
    refreshes edge_stats. Idempotent per config — same rules, same rows."""
    try:
        with _conn() as c:
            cid = _config_id(c, params)
            old = [r[0] for r in c.execute(
                "SELECT signal_id FROM signals WHERE config_id=? AND source='backtest'",
                (cid,))]
            if old:
                c.executemany("DELETE FROM signal_outcomes WHERE signal_id=?",
                              [(i,) for i in old])
                c.execute("DELETE FROM signals WHERE config_id=? AND source='backtest'",
                          (cid,))
            for t in trades:
                iid = _iid(c, t["ticker"], (ccy_of(t["ticker"]) if ccy_of else "USD"))
                cur = c.execute(
                    "INSERT OR REPLACE INTO signals "
                    "(config_id, instrument_id, d, source, entry, stop, target, rr) "
                    "VALUES (?,?,?,'backtest',?,?,?,?)",
                    (cid, iid, t["date"], t.get("entry"), t.get("stop_px"),
                     t.get("target_px"), t.get("rr_planned")))
                c.execute(
                    "INSERT OR REPLACE INTO signal_outcomes "
                    "(signal_id, status, exit_date, outcome_r, bars_held, mfe_r, mae_r) "
                    "VALUES (?,?,?,?,?,?,?)",
                    (cur.lastrowid, t["status"], t.get("exit_date"),
                     t["outcome_r"], t.get("bars_held"),
                     t.get("mfe_r"), t.get("mae_r")))
            if trades:
                ds = sorted(t["date"] for t in trades)
                c.execute("INSERT OR REPLACE INTO backtest_runs "
                          "(config_id, ran_at, n_stocks, n_trades, d_from, d_to) "
                          "VALUES (?,?,?,?,?,?)",
                          (cid, __import__("time").time(), n_stocks,
                           len(trades), ds[0], ds[-1]))
            _refresh_edge(c, cid)
            return len(trades)
    except Exception:
        return 0


def load_backtest(params: dict) -> dict | None:
    """Rebuild a previously-run simulation straight from the database.

    Every simulated trade is already stored in signals + signal_outcomes,
    so a set of rules that has been simulated once never needs five years
    of prices downloaded again. Returns {"trades": [...], "n_stocks": n,
    "ran_at": ts} in exactly the shape _aggregate() consumes, or None when
    these rules have never been simulated."""
    try:
        with _conn() as c:
            row = c.execute("SELECT config_id FROM strategy_configs WHERE param_hash=?",
                            (config_hash(params),)).fetchone()
            if not row:
                return None
            cid = row[0]
            meta = c.execute("SELECT ran_at, n_stocks FROM backtest_runs "
                             "WHERE config_id=?", (cid,)).fetchone()
            trades = [
                {"ticker": sym, "date": d, "exit_date": ex,
                 "rr_planned": rr, "outcome_r": out_r, "status": status,
                 "entry": entry, "stop_px": stop, "target_px": target,
                 "bars_held": bars, "mfe_r": mfe, "mae_r": mae}
                for (sym, d, ex, rr, out_r, status, entry, stop, target,
                     bars, mfe, mae) in c.execute("""
                    SELECT i.symbol, s.d, o.exit_date, s.rr, o.outcome_r,
                           o.status, s.entry, s.stop, s.target, o.bars_held,
                           o.mfe_r, o.mae_r
                    FROM signals s
                    JOIN signal_outcomes o USING (signal_id)
                    JOIN instruments i USING (instrument_id)
                    WHERE s.config_id = ? AND s.source = 'backtest'""", (cid,))]
            if not trades:
                return None
            return {"trades": trades,
                    "n_stocks": (meta[1] if meta else 0) or 0,
                    "ran_at": (meta[0] if meta else None)}
    except Exception:
        return None


def save_snapshot(payload: dict) -> bool:
    """Store the latest completed scan so the next page load has data
    without re-scanning. One row: the newest snapshot replaces the old."""
    try:
        blob = json.dumps(payload, default=str)
    except (TypeError, ValueError):
        return False
    try:
        with _conn() as c:
            c.execute("INSERT OR REPLACE INTO snapshots (id, saved_at, payload_json) "
                      "VALUES (1, ?, ?)", (__import__("time").time(), blob))
        return True
    except Exception:
        return False


def latest_snapshot() -> dict | None:
    """The most recent stored scan, with its age, or None."""
    try:
        with _conn() as c:
            row = c.execute("SELECT saved_at, payload_json FROM snapshots "
                            "WHERE id = 1").fetchone()
        if not row:
            return None
        payload = json.loads(row[1])
        payload["_saved_at"] = row[0]
        return payload
    except Exception:
        return None


def _refresh_edge(conn, config_id: int) -> None:
    conn.execute("DELETE FROM edge_stats WHERE config_id=?", (config_id,))
    conn.execute("""
        INSERT INTO edge_stats (config_id, instrument_id, n, wins, win_rate,
                                avg_r, total_r)
        SELECT s.config_id, s.instrument_id, COUNT(*),
               SUM(o.outcome_r > 0),
               ROUND(AVG(o.outcome_r > 0) * 100, 1),
               ROUND(AVG(o.outcome_r), 2), ROUND(SUM(o.outcome_r), 2)
        FROM signals s JOIN signal_outcomes o USING (signal_id)
        WHERE s.config_id = ? AND o.status != 'open'
        GROUP BY s.instrument_id""", (config_id,))
    conn.execute("""
        INSERT OR REPLACE INTO edge_stats (config_id, instrument_id, n, wins,
                                           win_rate, avg_r, total_r)
        SELECT s.config_id, NULL, COUNT(*), SUM(o.outcome_r > 0),
               ROUND(AVG(o.outcome_r > 0) * 100, 1),
               ROUND(AVG(o.outcome_r), 2), ROUND(SUM(o.outcome_r), 2)
        FROM signals s JOIN signal_outcomes o USING (signal_id)
        WHERE s.config_id = ? AND o.status != 'open'""", (config_id,))


def edge_for(params: dict) -> dict:
    """{symbol: {n, wins, win_rate, avg_r}} for the given rules, plus the
    whole-universe row under key '*'. Small-sample rows are excluded — a
    win rate off 2 trades is noise dressed as insight."""
    out: dict = {}
    try:
        with _conn() as c:
            row = c.execute("SELECT config_id FROM strategy_configs WHERE param_hash=?",
                            (config_hash(params),)).fetchone()
            if not row:
                return out
            for sym, n, wins, wr, avg_r in c.execute("""
                SELECT COALESCE(i.symbol, '*'), e.n, e.wins, e.win_rate, e.avg_r
                FROM edge_stats e LEFT JOIN instruments i USING (instrument_id)
                WHERE e.config_id = ?""", (row[0],)):
                if sym == "*" or n >= MIN_SAMPLE:
                    out[sym] = {"n": n, "wins": wins, "win_rate": wr, "avg_r": avg_r}
    except Exception:
        pass
    return out


def export_edge() -> list[dict]:
    """All edge aggregates + their config hashes — a few KB, browser-mirrorable."""
    out = []
    try:
        with _conn() as c:
            for ph, sym, n, wins, wr, avg_r, tot in c.execute("""
                SELECT sc.param_hash, i.symbol, e.n, e.wins, e.win_rate,
                       e.avg_r, e.total_r
                FROM edge_stats e
                JOIN strategy_configs sc USING (config_id)
                LEFT JOIN instruments i USING (instrument_id)"""):
                out.append({"h": ph, "s": sym, "n": n, "w": wins,
                            "wr": wr, "ar": avg_r, "tr": tot})
    except Exception:
        pass
    return out


def restore_edge(rows: list[dict]) -> int:
    """Re-seed edge_stats from a browser backup after a disk wipe. Aggregates
    only — the underlying signals rebuild on the next simulation run."""
    added = 0
    if not isinstance(rows, list):
        return 0
    try:
        with _conn() as c:
            for r in rows[:20000]:
                try:
                    c.execute("INSERT OR IGNORE INTO strategy_configs "
                              "(param_hash, params_json) VALUES (?, '{}')",
                              (str(r["h"]),))
                    cid = c.execute("SELECT config_id FROM strategy_configs "
                                    "WHERE param_hash=?", (str(r["h"]),)).fetchone()[0]
                    iid = _iid(c, str(r["s"])) if r.get("s") else None
                    c.execute("INSERT OR REPLACE INTO edge_stats "
                              "(config_id, instrument_id, n, wins, win_rate, "
                              "avg_r, total_r) VALUES (?,?,?,?,?,?,?)",
                              (cid, iid, int(r["n"]), int(r["w"]),
                               float(r["wr"]), float(r["ar"]), float(r["tr"])))
                    added += 1
                except Exception:
                    continue
    except Exception:
        pass
    return added
