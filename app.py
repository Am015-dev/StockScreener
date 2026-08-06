"""Web front-end for the pullback screener, built for Render's free plan.

The free plan sleeps when idle and wakes on request ("on demand"), so the app
runs the scan only when you press Run: a background thread does the work while
the page polls /status for live progress. Filters posted from the UI override
the screener defaults per run; universe/OHLC data is cached in screener.py for
an hour, so filter-tweak reruns are fast. The latest results are kept in
memory and mirrored to a CSV on the instance's ephemeral disk, so they survive
page reloads (but not a full spin-down — just press Run again).
"""

import io
import os
import threading
import time
from collections import Counter

import pandas as pd
import yfinance as yf
from flask import Flask, Response, jsonify, render_template, request

import backtest as backtest_mod
import cache_store
import journal
import portfolio_import
import screener

app = Flask(__name__)

RESULTS_CSV = os.environ.get("RESULTS_CSV", "/tmp/screener_results.csv")
TOP_N = 3

_lock = threading.Lock()
_state = {
    "status": "idle",          # idle | running | done | error
    "log": [],
    "started_at": None,
    "finished_at": None,
    "elapsed_s": None,
    "universe_size": None,
    "results": [],             # list of row dicts, sorted by score desc
    "top_picks": [],           # first TOP_N rows
    "rejection_summary": [],   # [{"reason": ..., "count": ...}]
    "near_misses": [],         # [{"ticker": ..., "reason": ...}]
    "params_used": None,
    "portfolio": None,         # book/cash/risk-budget summary when holdings given
    "journal": None,           # track-record scoreboard (see journal.py)
    "near_board": [],          # closest non-qualifying setups, with reasons
    "relax_hints": {},         # which filter change would surface more results
    "scanned": None,           # live progress: tickers checked so far this run
    "bt_status": "idle",       # simulation: idle | running | done | error
    "backtest": None,          # simulation results (see backtest.py)
    "error": None,
}


# ---- journal price lookups: cached scan data first, one batch download for
# ---- picks whose tickers have since dropped out of the universe
def _journal_bars(ticker):
    data = screener._cache.get("ohlc")
    if data is not None:
        try:
            hist = data[ticker].dropna()
            if len(hist):
                return hist
        except Exception:
            pass
    return None


def _journal_fetch_missing(tickers):
    out = {}
    try:
        ok, d = screener._yahoo_call(lambda: yf.download(
            tickers, period="1y", auto_adjust=True,
            group_by="ticker", threads=True, progress=False))
        if ok and d is not None and not d.empty:
            if not isinstance(d.columns, pd.MultiIndex):
                d = pd.concat({tickers[0]: d}, axis=1)
            for t in tickers:
                try:
                    h = d[t].dropna()
                    if len(h):
                        out[t] = h
                except Exception:
                    pass
    except Exception:
        pass
    return out


def _records(df: pd.DataFrame) -> list[dict]:
    """DataFrame -> list of dicts with NaN turned into JSON-safe None."""
    return df.astype(object).where(df.notna(), None).to_dict("records")


def _load_cached_csv():
    if os.path.exists(RESULTS_CSV):
        try:
            df = pd.read_csv(RESULTS_CSV)
            records = _records(df)
            _state["results"] = records
            _state["top_picks"] = records[:TOP_N]
            _state["status"] = "done"
            _state["finished_at"] = os.path.getmtime(RESULTS_CSV)
            _state["log"] = ["Loaded cached results from previous run."]
        except Exception:
            pass


_load_cached_csv()
try:
    _state["journal"] = journal.snapshot()
except Exception:
    pass


def _progress(msg):
    for line in str(msg).splitlines():
        if line.strip():
            _state["log"].append(line)
    # keep the log bounded
    if len(_state["log"]) > 500:
        _state["log"][:] = _state["log"][-500:]


def _on_partial(rows, scanned, total):
    """Stream qualified picks to the UI while later batches still download."""
    recs = sorted(rows, key=lambda r: r.get("score", 0), reverse=True)
    _state["results"] = recs
    _state["top_picks"] = recs[:TOP_N]
    _state["scanned"] = scanned
    _state["universe_size"] = total


def _run_scan(params):
    try:
        result = screener.run_screener(params, progress=_progress,
                                       on_partial=_on_partial)
        df = result["df"]
        rejections = result["rejections"]

        reasons = Counter(v.split(" (")[0] for v in rejections.values())
        _state["rejection_summary"] = [
            {"reason": reason, "count": n} for reason, n in reasons.most_common()
        ]
        _state["near_misses"] = [
            {"ticker": t, "reason": w} for t, w in rejections.items()
            if "R:R" in w or "earnings" in w or "unprofitable" in w
        ]
        records = _records(df) if len(df) else []
        _state["results"] = records
        _state["top_picks"] = records[:TOP_N]
        _state["universe_size"] = result["universe_size"]
        _state["elapsed_s"] = round(result["elapsed_s"])
        _state["params_used"] = result["params"]
        _state["portfolio"] = result.get("portfolio")
        _state["near_board"] = result.get("near") or []
        _state["relax_hints"] = result.get("relax_hints") or {}
        if len(df):
            df.to_csv(RESULTS_CSV, index=False)

        # track record: log today's picks, grade every still-open past pick
        try:
            added = journal.record_picks(records)
            resolved = journal.update_outcomes(_journal_bars, _journal_fetch_missing)
            _state["journal"] = journal.snapshot()
            if added or resolved:
                _progress(f"Journal: recorded {added} new pick(s), "
                          f"resolved {resolved} past pick(s).")
        except Exception as e:
            _progress(f"Journal update failed (results unaffected): {e}")

        _state["status"] = "done"
    except Exception as e:
        _state["error"] = f"{type(e).__name__}: {e}"
        _state["status"] = "error"
        _progress(f"Scan failed: {_state['error']}")
    finally:
        _state["scanned"] = None
        _state["finished_at"] = time.time()


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/defaults")
def defaults():
    return jsonify({"defaults": screener.DEFAULTS, "sectors": screener.ALL_SECTORS})


@app.route("/run", methods=["POST"])
def run():
    overrides = request.get_json(silent=True) or {}
    params = screener.clean_params(overrides)
    with _lock:
        if _state["status"] == "running":
            return jsonify({"ok": False, "message": "A scan is already running."}), 409
        _state.update(status="running", log=[], error=None,
                      started_at=time.time(), finished_at=None,
                      rejection_summary=[], near_misses=[], params_used=params,
                      near_board=[], relax_hints={}, scanned=None)
        threading.Thread(target=_run_scan, args=(params,), daemon=True).start()
    return jsonify({"ok": True, "params": params})


@app.route("/parse_portfolio", methods=["POST"])
def parse_portfolio():
    """Parse an uploaded broker CSV (Revolut transaction export or a simple
    ticker,shares,cost list) into holdings + cash. Nothing is stored server-
    side — the result goes back to the browser, which keeps it locally."""
    text = request.get_data(as_text=True) or ""
    if len(text) > 2_000_000:
        return jsonify({"ok": False, "error": "file too large (2MB max)"}), 413
    try:
        result = portfolio_import.parse_portfolio_csv(text)
    except ValueError as e:
        return jsonify({"ok": False, "error": str(e)}), 400
    except Exception as e:
        return jsonify({"ok": False, "error": f"could not parse: {e}"}), 400
    return jsonify({"ok": True, **result})


def _run_backtest_thread(params):
    try:
        bt = backtest_mod.run_backtest(params, screener._cache.get("ohlc"),
                                       screener._cache.get("universe") or [],
                                       progress=_progress)
        _state["backtest"] = bt
        _state["bt_status"] = "done"
        _progress(f"Simulation complete: {bt['n']} historical trades across "
                  f"{bt.get('n_stocks', 0)} stocks.")
    except Exception as e:
        _state["bt_status"] = "error"
        _progress(f"Simulation failed: {type(e).__name__}: {e}")


@app.route("/backtest", methods=["POST"])
def run_backtest_route():
    overrides = request.get_json(silent=True) or {}
    params = screener.clean_params(overrides)
    if screener._cache.get("ohlc") is None or not screener._cache.get("universe"):
        return jsonify({"ok": False, "message":
                        "Run a scan first — the simulation replays the prices "
                        "the scan downloaded."}), 400
    with _lock:
        if _state["bt_status"] == "running" or _state["status"] == "running":
            return jsonify({"ok": False, "message": "Something is already running."}), 409
        _state.update(bt_status="running", backtest=None)
        threading.Thread(target=_run_backtest_thread, args=(params,),
                         daemon=True).start()
    return jsonify({"ok": True})


# ---- Yahoo auth mirror: the crumb lives on ephemeral disk, so the browser
# ---- keeps a copy (like the journal) and hands it back after a redeploy
@app.route("/auth/export")
def auth_export():
    hit, stored = cache_store.fetch("yahoo_auth", screener.YAHOO_AUTH_TTL)
    return jsonify({"auth": stored if hit else None})


@app.route("/auth/restore", methods=["POST"])
def auth_restore():
    body = request.get_json(silent=True) or {}
    a = body.get("auth") or {}
    try:
        if (a.get("crumb") and isinstance(a.get("cookies"), dict)
                and time.time() - float(a.get("ts", 0)) < screener.YAHOO_AUTH_TTL):
            cache_store.put("yahoo_auth", {"cookies": a["cookies"],
                                           "crumb": a["crumb"], "ts": a["ts"]})
            screener._yauth.update(session=None, crumb=None, ts=0.0)
            return jsonify({"ok": True, "restored": True})
    except (TypeError, ValueError):
        pass
    return jsonify({"ok": True, "restored": False})


def _crumb_hunter():
    """Yahoo's token endpoint throttles in waves; keep trying quietly in the
    background until a crumb is won, then it persists for a week."""
    import random
    while True:
        try:
            _, crumb = screener._yahoo_auth_session()
            if crumb:
                time.sleep(3600)
                continue
        except Exception:
            pass
        time.sleep(150 + random.random() * 120)


threading.Thread(target=_crumb_hunter, daemon=True).start()


@app.route("/status")
def status():
    return jsonify(_state)


@app.route("/diag/yahoo")
def diag_yahoo():
    """Probe each Yahoo API from THIS server and report raw status codes —
    evidence for which data paths are blocked from this host."""
    import requests as rq
    out = {}
    s = rq.Session()
    s.headers.update(screener._V7_UA)
    crumb = ""
    try:
        r = s.get("https://fc.yahoo.com", timeout=10)
        out["fc_cookie"] = f"{r.status_code}, cookies={len(s.cookies)}"
    except Exception as e:
        out["fc_cookie"] = f"ERR {type(e).__name__}: {e}"
    try:
        r = s.get("https://query1.finance.yahoo.com/v1/test/getcrumb", timeout=10)
        crumb = r.text.strip()
        out["getcrumb"] = f"{r.status_code}, body={r.text[:80]!r}"
    except Exception as e:
        out["getcrumb"] = f"ERR {type(e).__name__}: {e}"
    for name, url, params in [
        ("chart", "https://query1.finance.yahoo.com/v8/finance/chart/AAPL",
         {"range": "1d", "interval": "1d"}),
        ("v7_quote", "https://query1.finance.yahoo.com/v7/finance/quote",
         {"symbols": "AAPL", "crumb": crumb}),
        ("v7_quote_q2", "https://query2.finance.yahoo.com/v7/finance/quote",
         {"symbols": "AAPL", "crumb": crumb}),
        ("quoteSummary", "https://query2.finance.yahoo.com/v10/finance/quoteSummary/AAPL",
         {"modules": "price,defaultKeyStatistics,financialData", "crumb": crumb}),
    ]:
        try:
            r = s.get(url, params=params, timeout=10)
            out[name] = f"{r.status_code}, body={r.text[:100]!r}"
        except Exception as e:
            out[name] = f"ERR {type(e).__name__}: {e}"
    return jsonify(out)


@app.route("/journal/export")
def journal_export():
    """Full journal dump — the browser mirrors this to localStorage so the
    track record survives Render's ephemeral disk."""
    return jsonify({"picks": journal.export_all()})


@app.route("/journal/restore", methods=["POST"])
def journal_restore():
    """Re-import a browser-side backup after a server disk wipe."""
    body = request.get_json(silent=True) or {}
    added = journal.restore(body.get("picks") or [])
    _state["journal"] = journal.snapshot()
    return jsonify({"ok": True, "added": added})


@app.route("/results.csv")
def results_csv():
    if not _state["results"]:
        return Response("no results yet\n", status=404, mimetype="text/plain")
    buf = io.StringIO()
    pd.DataFrame(_state["results"]).to_csv(buf, index=False)
    return Response(buf.getvalue(), mimetype="text/csv",
                    headers={"Content-Disposition": "attachment; filename=screener_results.csv"})


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 8000)), debug=True)
