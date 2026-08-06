"""Trade journal: records every scan's picks and grades them later.

The screener's credibility problem is that picks vanish — nothing ever says
whether last month's setups worked. This module fixes that:

  - after each successful scan, every pick (entry / stop / target as planned
    that day) is stored in SQLite;
  - on subsequent scans the daily bars since the pick date are replayed to
    decide what happened first: stop hit (-1R), target hit (+RR), or neither
    within EXPIRE_BARS bars (expired at the last close, marked to market);
  - a scoreboard aggregates the resolved picks (hit rate, average R, total R,
    split by score grade) so the tool grades itself in public.

Outcomes are simulated, not real fills: entry at the scan-day close, exit
exactly at stop/target — except gaps, where the bar's open is used (a gap
through your stop really does fill worse). Costs are not included.

R = one unit of planned risk (entry minus stop). A stopped-out pick is -1R;
a pick that hits a 3:1 target is +3R.

Like cache_store, the DB lives on ephemeral disk on Render's free plan —
the web UI mirrors the journal to the browser's localStorage and restores
it automatically when the server comes back empty (see /journal endpoints).
"""

import os
import sqlite3
import time
from datetime import date, datetime

EXPIRE_BARS = 40   # trading bars (~2 months) before an unresolved pick expires
DB_PATH = os.environ.get("JOURNAL_DB", "/tmp/screener_journal.db")

_FIELDS = ("scan_ts", "scan_date", "ticker", "name", "sector",
           "entry", "stop", "target", "rr", "score", "shares", "risk_eur",
           "status", "resolved_ts", "resolved_date", "exit_price", "outcome_r")


def _conn():
    conn = sqlite3.connect(DB_PATH, timeout=5)
    conn.execute("""CREATE TABLE IF NOT EXISTS picks (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        scan_ts REAL, scan_date TEXT, ticker TEXT, name TEXT, sector TEXT,
        entry REAL, stop REAL, target REAL, rr REAL, score INTEGER,
        shares REAL, risk_eur REAL,
        status TEXT DEFAULT 'open',
        resolved_ts REAL, resolved_date TEXT, exit_price REAL, outcome_r REAL,
        UNIQUE(ticker, scan_date))""")
    return conn


def record_picks(rows: list[dict], scan_ts: float | None = None) -> int:
    """Store today's picks. A ticker with a still-open pick is not re-recorded
    (the same setup persisting across days is one trade idea, not many).

    Picks whose safety gates could not verify (unverified/unavailable flags)
    are NEVER journaled — a track record polluted with trades the methodology
    itself would have blocked measures the plumbing, not the method."""
    rows = [r for r in rows
            if not any(bad in (r.get("flags") or "")
                       for bad in ("unverified", "unavailable"))]
    ts = scan_ts or time.time()
    day = date.fromtimestamp(ts).isoformat()
    added = 0
    try:
        with _conn() as c:
            open_now = {r[0] for r in
                        c.execute("SELECT ticker FROM picks WHERE status='open'")}
            for r in rows:
                try:
                    t = str(r["ticker"])
                    entry, stop, target = float(r["price"]), float(r["stop"]), float(r["resistance"])
                    if t in open_now or not (stop < entry < target):
                        continue
                    cur = c.execute(
                        """INSERT OR IGNORE INTO picks
                           (scan_ts, scan_date, ticker, name, sector, entry, stop,
                            target, rr, score, shares, risk_eur, status)
                           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,'open')""",
                        (ts, day, t, str(r.get("name") or t), str(r.get("sector") or "?"),
                         entry, stop, target, float(r.get("RR") or 0),
                         int(r.get("score") or 0), float(r.get("shares") or 0),
                         float(r.get("risk_EUR") or 0)))
                    added += cur.rowcount
                    open_now.add(t)
                except (KeyError, TypeError, ValueError):
                    continue
    except Exception:
        pass
    return added


def _replay(pick: dict, hist) -> tuple[str, float, object] | None:
    """Walk the daily bars after the pick date; first touch wins.

    Gap-aware: a bar opening beyond the stop/target exits at the open. When a
    single bar spans both levels, the stop is assumed hit first (conservative).
    Returns (status, exit_price, bar_date) or None if still open.
    """
    entry, stop, target = pick["entry"], pick["stop"], pick["target"]
    idx = hist.index
    if getattr(idx, "tz", None) is not None:
        idx = idx.tz_localize(None)
    cutoff = datetime.fromisoformat(pick["scan_date"])
    bars = 0
    last_close, last_date = None, None
    for i in range(len(hist)):
        if idx[i].to_pydatetime() <= cutoff:
            continue
        row = hist.iloc[i]
        o, h, l, cl = float(row["Open"]), float(row["High"]), float(row["Low"]), float(row["Close"])
        if o <= stop:
            return "hit_stop", o, idx[i]
        if o >= target:
            return "hit_target", o, idx[i]
        if l <= stop:
            return "hit_stop", stop, idx[i]
        if h >= target:
            return "hit_target", target, idx[i]
        bars += 1
        last_close, last_date = cl, idx[i]
    if bars >= EXPIRE_BARS:
        return "expired", last_close, last_date
    return None


def update_outcomes(get_bars, fetch_missing=None) -> int:
    """Resolve open picks against price history.

    get_bars(ticker) -> OHLC DataFrame or None (typically the scan's cached
    frame). Tickers it can't serve are batch-fetched via fetch_missing(list)
    -> {ticker: DataFrame} when provided. Returns how many picks resolved.
    """
    try:
        with _conn() as c:
            c.row_factory = sqlite3.Row
            open_picks = [dict(r) for r in
                          c.execute("SELECT * FROM picks WHERE status='open'")]
    except Exception:
        return 0
    if not open_picks:
        return 0

    bars: dict = {}
    missing: list[str] = []
    for p in open_picks:
        t = p["ticker"]
        if t in bars or t in missing:
            continue
        h = None
        try:
            h = get_bars(t)
        except Exception:
            pass
        if h is not None and len(h):
            bars[t] = h
        else:
            missing.append(t)
    if missing and fetch_missing is not None:
        try:
            bars.update(fetch_missing(missing) or {})
        except Exception:
            pass

    resolved = 0
    try:
        with _conn() as c:
            for p in open_picks:
                hist = bars.get(p["ticker"])
                if hist is None or not len(hist):
                    continue
                try:
                    hit = _replay(p, hist)
                except Exception:
                    continue
                if hit is None:
                    continue
                status, exit_price, bar_date = hit
                risk_ps = p["entry"] - p["stop"]
                r = round((exit_price - p["entry"]) / risk_ps, 2) if risk_ps > 0 else None
                c.execute("""UPDATE picks SET status=?, exit_price=?, outcome_r=?,
                             resolved_ts=?, resolved_date=? WHERE id=?""",
                          (status, round(float(exit_price), 4), r, time.time(),
                           str(bar_date)[:10], p["id"]))
                resolved += 1
    except Exception:
        pass
    return resolved


def _grade_stats(rows: list[dict]) -> dict:
    n = len(rows)
    wins = sum(1 for r in rows if (r["outcome_r"] or 0) > 0)
    total = round(sum(r["outcome_r"] or 0 for r in rows), 2)
    return {"n": n, "wins": wins,
            "hit_rate_pct": round(wins / n * 100) if n else None,
            "avg_r": round(total / n, 2) if n else None, "total_r": total}


def snapshot(open_cap: int = 50, recent_cap: int = 20) -> dict:
    """Scoreboard + open picks + recent outcomes, sized for the /status JSON."""
    try:
        with _conn() as c:
            c.row_factory = sqlite3.Row
            rows = [dict(r) for r in c.execute("SELECT * FROM picks")]
    except Exception:
        rows = []
    open_rows = sorted((r for r in rows if r["status"] == "open"),
                       key=lambda r: r["scan_date"], reverse=True)
    done_rows = sorted((r for r in rows if r["status"] != "open"),
                       key=lambda r: r["resolved_date"] or "", reverse=True)
    today = date.today()
    stats = _grade_stats(done_rows)
    return {
        "n_total": len(rows), "n_open": len(open_rows), "n_resolved": len(done_rows),
        "n_wins": stats["wins"], "hit_rate_pct": stats["hit_rate_pct"],
        "avg_r": stats["avg_r"], "total_r": stats["total_r"],
        "grades": {"strong": _grade_stats([r for r in done_rows if (r["score"] or 0) >= 70]),
                   "weak": _grade_stats([r for r in done_rows if (r["score"] or 0) < 70])},
        "open": [{"ticker": r["ticker"], "scan_date": r["scan_date"],
                  "entry": r["entry"], "stop": r["stop"], "target": r["target"],
                  "score": r["score"],
                  "days_open": (today - date.fromisoformat(r["scan_date"])).days}
                 for r in open_rows[:open_cap]],
        "recent": [{"ticker": r["ticker"], "scan_date": r["scan_date"],
                    "resolved_date": r["resolved_date"], "status": r["status"],
                    "outcome_r": r["outcome_r"], "score": r["score"]}
                   for r in done_rows[:recent_cap]],
    }


def export_all() -> list[dict]:
    """Full dump for the browser-side backup."""
    try:
        with _conn() as c:
            c.row_factory = sqlite3.Row
            return [{k: r[k] for k in _FIELDS} for r in c.execute("SELECT * FROM picks")]
    except Exception:
        return []


def restore(rows: list[dict]) -> int:
    """Re-insert a browser backup after the server disk was wiped.
    Existing (ticker, scan_date) rows are kept, not overwritten."""
    added = 0
    if not isinstance(rows, list):
        return 0
    try:
        with _conn() as c:
            for r in rows[:5000]:
                try:
                    vals = tuple(r.get(k) for k in _FIELDS)
                    if not vals[2] or vals[5] is None:   # ticker, entry required
                        continue
                    cur = c.execute(
                        f"INSERT OR IGNORE INTO picks ({','.join(_FIELDS)}) "
                        f"VALUES ({','.join('?' * len(_FIELDS))})", vals)
                    added += cur.rowcount
                except Exception:
                    continue
    except Exception:
        pass
    return added
