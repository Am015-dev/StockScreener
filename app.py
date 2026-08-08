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
import json
import os
import threading
import time
from collections import Counter

import pandas as pd
import yfinance as yf
from flask import Flask, Response, jsonify, render_template, request

import backtest as backtest_mod
import db as market_db
import cache_store
import journal
import market_clock
import portfolio_import
import screener

app = Flask(__name__)


@app.after_request
def _no_stale_html(resp):
    """Never let a browser serve yesterday's page.

    The app ships its whole UI in one template, so a cached copy hides every
    deployed change behind a hard refresh — which looks exactly like nothing
    having been deployed at all. JSON is live state and must never be cached
    either; static assets can be."""
    ct = (resp.headers.get("Content-Type") or "")
    if ct.startswith("text/html") or ct.startswith("application/json"):
        resp.headers["Cache-Control"] = "no-store, max-age=0"
        resp.headers["Pragma"] = "no-cache"
    return resp

RESULTS_CSV = os.environ.get("RESULTS_CSV", "/tmp/screener_results.csv")
ALERT_PROFILE = os.environ.get("ALERT_PROFILE", "/tmp/alert_profile.json")
TOP_N = 3

# How many stocks a scan started from the web page may touch.
#
# The full 1,500-stock scan takes ~7 minutes and several hundred MB. On the
# 512MB free instance that is not a slow scan, it is a dead one: the process
# is reaped and the page shows "This scan has stopped responding" — which is
# exactly what a visitor reported. The full universe is the scheduled job's
# work; the button's job is to answer a custom filter question quickly and
# actually finish. Clamping and SAYING SO beats failing and guessing why.
WEB_SCAN_MAX = int(os.environ.get("WEB_SCAN_MAX", "250"))

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
    "bt_rules": None,          # the rule set the shown simulation was run under
    "pending": [],             # qualified technically, awaiting verification
    "breadth": None,           # {"pct": .., "risk_factor": ..} market health
    "concentration": None,     # how many independent bets the list really holds
    "results_ts": None,        # when the shown results were produced
    "health": None,            # {"blocked_unverified": n} from the last scan
    "error": None,
    "restored": False,         # results came from storage, not a scan just run
    "last_progress_ts": None,  # when the running scan last said anything
    "published_preset": None,  # which scheduled-scan preset is on screen
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
    """Reload the previous run's picks — but never serve stale levels as if
    they were fresh: results older than a day are refused outright."""
    if os.path.exists(RESULTS_CSV):
        try:
            mtime = os.path.getmtime(RESULTS_CSV)
            age_h = (time.time() - mtime) / 3600
            if age_h > 24:
                _state["log"] = [f"Previous results are {age_h:.0f}h old — "
                                 f"discarded (entry/stop levels go stale). "
                                 f"Run a fresh scan."]
                return
            df = pd.read_csv(RESULTS_CSV)
            records = _records(df)
            _state["results"] = records
            _state["top_picks"] = records[:TOP_N]
            _state["status"] = "done"
            _state["finished_at"] = mtime
            _state["results_ts"] = mtime
            _state["log"] = [f"Loaded results from the scan {age_h:.1f}h ago — "
                             f"prices have moved since; rerun before acting."]
        except Exception:
            pass


# ---- published results: scans run on a schedule in CI, not here ----
# The heavy work (downloading and analysing thousands of stocks) happens on
# a GitHub Actions runner and lands on the `screener-data` branch. This
# instance just reads the finished JSON, so a page load costs a fetch
# instead of a five-minute scan — and a visitor never waits on Yahoo.
# A directory of published results committed alongside the code. Render
# wipes /tmp on every deploy, and a private repository cannot be read from
# raw.githubusercontent without a token — so without this a fresh instance
# starts empty and every deploy looks like nothing changed.
PUBLISHED_DIR = os.environ.get("PUBLISHED_DIR", os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "published"))
PUBLISHED_BASE = os.environ.get(
    "PUBLISHED_BASE",
    "https://raw.githubusercontent.com/Am015-dev/StockScreener/screener-data")
PUBLISHED_TTL = float(os.environ.get("PUBLISHED_TTL", "900"))   # re-check every 15 min
_published = {"ts": 0.0, "index": None}


def _published_get(path: str):
    """A published file: from the copy shipped with this build if present,
    otherwise over the network. The local copy needs no token and survives
    the disk wipe, so it is tried first."""
    local = os.path.join(PUBLISHED_DIR, path)
    try:
        if os.path.exists(local):
            with open(local) as f:
                return json.load(f)
    except Exception:
        pass
    import requests as rq
    headers = {}
    tok = os.environ.get("PUBLISHED_TOKEN") or os.environ.get("GITHUB_TOKEN")
    if tok:
        headers["Authorization"] = f"token {tok}"
    try:
        r = rq.get(f"{PUBLISHED_BASE}/{path}", headers=headers, timeout=20)
        if r.status_code == 200:
            return r.json()
    except Exception:
        pass
    return None


def _published_index(force: bool = False):
    if not force and _published["index"] is not None \
            and time.time() - _published["ts"] < PUBLISHED_TTL:
        return _published["index"]
    idx = _published_get("index.json")
    if idx is not None:
        _published.update(index=idx, ts=time.time())
    return idx


def _adopt_published(preset: dict) -> bool:
    """Load a published scan into the live state and store it under its
    filters, so it behaves exactly like a scan run here."""
    payload = _published_get(f"{preset['preset']}.json")
    if not payload or not payload.get("results_ts"):
        return False
    params = payload.get("params_used")
    if not params:
        return False
    payload, dropped = _drop_offmethod(payload)
    market_db.save_snapshot(params, {k: payload.get(k) for k in SNAPSHOT_KEYS})
    if not _load_snapshot(params):
        return False
    if dropped:
        _state["log"].append(
            f"{dropped} published pick(s) dropped — produced before the rules "
            f"tightened and no longer within the methodology.")
    # a published simulation arrives ready to display, so the analytics
    # are populated before the reader does anything
    if payload.get("backtest"):
        _state["backtest"] = payload["backtest"]
        _state["bt_rules"] = payload.get("bt_rules")
        _state["bt_status"] = "done"
    _state["published_preset"] = preset["preset"]
    _state["log"].append(
        f"Results published by the scheduled scan ({preset['preset']} preset) — "
        f"no scanning was needed to show them.")
    return True


def _load_published(force: bool = False) -> bool:
    """Adopt the freshest published preset that is newer than what we have."""
    idx = _published_index(force)
    if not idx or not idx.get("presets"):
        return False
    best = max(idx["presets"], key=lambda p: p.get("results_ts") or 0)
    have = float(_state.get("results_ts") or 0)
    if float(best.get("results_ts") or 0) <= have:
        return False
    return _adopt_published(best)


def _methodology_violations(row: dict) -> list[str]:
    """Why this stored row would not be produced by the current rules.

    Results outlive the code that made them. A snapshot or a published file
    written before a rule tightened will happily keep being served, so a
    methodology fix reaches the code and not the screen — which is exactly
    what happened when the RSI ceiling, the reward:risk cap and the
    earnings gate all landed while the page carried on showing rows that
    violated all three."""
    why = []
    try:
        rsi = row.get("RSI")
        ceiling = screener.METHODOLOGY_MAX.get("rsi_high")
        if rsi is not None and ceiling is not None and float(rsi) > ceiling:
            why.append(f"RSI {float(rsi):.0f} above the {ceiling:g} pullback ceiling")
        rr = row.get("RR")
        if rr is not None and float(rr) > screener.RR_SANE_MAX:
            why.append(f"reward:risk {float(rr):.1f} above {screener.RR_SANE_MAX:g}")
        price, support = row.get("price"), row.get("support")
        dist_max = screener.METHODOLOGY_MAX.get("max_support_dist_pct")
        if price and support and dist_max is not None:
            # measured off support, exactly as the screener measures it
            # (screener.py: (price / support - 1) * 100). Dividing by price
            # instead gives a smaller number, so a row the screener would
            # now reject at 5.2% reads as 4.9% here and survives the
            # revalidation — a filter looser than the rule it enforces.
            dist = (float(price) / float(support) - 1) * 100
            if dist > dist_max:
                why.append(f"entry {dist:.1f}% above support (max {dist_max:g}%)")
    except (TypeError, ValueError):
        pass
    return why


def _drop_offmethod(payload: dict) -> tuple[dict, int]:
    """Remove stored rows the current rules would no longer produce."""
    dropped = 0
    for key in ("results", "top_picks", "near_board", "pending"):
        rows = payload.get(key)
        if not isinstance(rows, list):
            continue
        keep = [r for r in rows if not _methodology_violations(r)]
        if key == "results":
            dropped = len(rows) - len(keep)
        payload[key] = keep
    if isinstance(payload.get("results"), list):
        payload["top_picks"] = payload["results"][:TOP_N]
    return payload, dropped


SNAPSHOT_KEYS = ("results", "top_picks", "rejection_summary", "near_misses",
                 "params_used", "portfolio", "near_board", "relax_hints",
                 "pending", "breadth", "concentration", "health",
                 "universe_size", "scanned",
                 "elapsed_s", "results_ts", "backtest", "bt_status",
                 "bt_rules")
SNAPSHOT_MAX_AGE_H = 24   # entry/stop levels go stale; never serve them as fresh


def _save_snapshot(params=None):
    """Persist the finished scan under the filters that produced it, so
    returning to those filters shows the same result without re-scanning
    and a different filter set keeps its own stored scan."""
    p = params or _state.get("params_used")
    if not p:
        return
    try:
        market_db.save_snapshot(p, {k: _state.get(k) for k in SNAPSHOT_KEYS})
    except Exception:
        pass


def _autoload_backtest(params) -> bool:
    """Show the simulation without anyone asking for it.

    A stored run rebuilds from the database in milliseconds, so requiring a
    button press to see any analytics at all was pure friction — and on a
    fresh instance it meant the whole analytics half of the page was blank."""
    if not params or _state.get("bt_status") == "running":
        return False
    try:
        stored = market_db.load_backtest(params)
        if not stored or not stored.get("trades"):
            return False
        res = backtest_mod._aggregate(stored["trades"], stored["n_stocks"])
        if not res.get("n"):
            return False
        res["from_db"] = True
        res["ran_at"] = stored.get("ran_at")
        _state["backtest"] = res
        _state["bt_status"] = "done"
        _state["bt_rules"] = {k: params.get(k) for k in market_db.TECH_PARAMS}
        return True
    except Exception:
        return False


def _load_snapshot(params=None) -> bool:
    """Rehydrate a stored scan: the one matching `params` when given, else
    the most recent under any filters (the cold-start case, before the page
    has told us what it is showing). Age is carried through to the UI so
    stale levels are always labelled, never presented as live."""
    snap = (market_db.snapshot_for(params) if params
            else market_db.latest_snapshot())
    if not snap:
        return False
    ts = snap.get("results_ts") or snap.get("_saved_at")
    if not ts:
        return False
    age_h = (time.time() - float(ts)) / 3600
    if age_h > SNAPSHOT_MAX_AGE_H:
        _state["log"] = [f"Stored results are {age_h:.0f}h old — entry and stop "
                         f"levels have gone stale, so they are not shown. "
                         f"Run a fresh scan."]
        # the simulation does NOT go stale the way prices do: it is a fixed
        # historical record, so keep it even when the picks are discarded
        if snap.get("backtest"):
            _state["backtest"] = snap["backtest"]
            _state["bt_status"] = "done"
        return False
    snap, dropped = _drop_offmethod(snap)
    for k in SNAPSHOT_KEYS:
        if snap.get(k) is not None:
            _state[k] = snap[k]
    _state["status"] = "done"
    _state["finished_at"] = ts
    _state["results_ts"] = ts
    _state["restored"] = True
    _state["params_used"] = snap.get("_params") or _state.get("params_used")
    if dropped:
        _state["log"].append(
            f"{dropped} stored pick(s) dropped — the rules have tightened since "
            f"that scan and they would no longer qualify. Re-run for a full list.")
    if not _state.get("backtest"):
        _autoload_backtest(_state.get("params_used"))
    n = len(_state.get("results") or [])
    _state["log"] = [
        f"Loaded the last scan from the database — {n} pick(s), "
        f"{age_h:.1f}h old. Prices have moved since; rerun before acting."]
    if _state.get("backtest"):
        _state["log"].append(
            "Simulation results restored too — no re-run needed unless you "
            "change the rules.")
    return True


market_db._drop_dead_weight()   # reclaim the write-only bars table, once
# published results first: they are produced by the scheduled scan and are
# usually fresher than anything this instance still has after a restart
if not _load_published():
    if not _load_snapshot():
        _load_cached_csv()
try:
    _state["journal"] = journal.snapshot()
except Exception:
    pass


def _progress(msg):
    for line in str(msg).splitlines():
        if line.strip():
            _state["log"].append(line)
            _state["last_progress_ts"] = time.time()
    # keep the log bounded
    if len(_state["log"]) > 500:
        _state["log"][:] = _state["log"][-500:]


def _on_partial(rows, scanned, total, pending=None):
    """Stream qualified AND pending-verification picks while the scan runs."""
    recs = sorted(rows, key=lambda r: r.get("score", 0), reverse=True)
    _state["results"] = recs
    _state["top_picks"] = recs[:TOP_N]
    _state["scanned"] = scanned
    _state["universe_size"] = total
    _state["results_ts"] = time.time()
    if pending is not None:
        _state["pending"] = pending


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
        _state["health"] = result.get("health")
        _state["pending"] = result.get("pending") or []
        _state["breadth"] = result.get("breadth")
        _state["concentration"] = result.get("concentration")
        _state["results_ts"] = time.time()
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
        _state["restored"] = False
        if _autoload_backtest(params):
            _progress("Loaded this rule set's stored simulation — "
                      "the track record below needed no re-run.")
        _save_snapshot(params)
        if _state["pending"]:
            _start_auto_reverify(dict(params or {}))
    except Exception as e:
        _state["error"] = f"{type(e).__name__}: {e}"
        _state["status"] = "error"
        _progress(f"Scan failed: {_state['error']}")
    finally:
        _state["scanned"] = None
        _state["finished_at"] = time.time()


_auto = {"running": False, "cycles": 0}
_AUTO_MAX_CYCLES = 4


def _start_auto_reverify(params):
    with _lock:
        if _auto["running"]:
            return
        if _auto["cycles"] >= _AUTO_MAX_CYCLES:
            if _auto["cycles"] == _AUTO_MAX_CYCLES:   # say it once, then rest
                _auto["cycles"] += 1
                _progress(f"{len(_state['pending'])} pick(s) still can't be "
                          f"verified after {_AUTO_MAX_CYCLES} automatic attempts — "
                          f"leaving them as 'awaiting verification'. Their "
                          f"fundamentals may simply not be available today; "
                          f"a manual rerun later will try again.")
            return
        _auto["cycles"] += 1
        _auto["running"] = True
    threading.Thread(target=_auto_reverify, args=(params,), daemon=True).start()


def _auto_reverify(params, attempts=12, wait=30):
    """Picks blocked only for unverifiable data shouldn't need a human retry:
    poll for the fundamentals source, then rerun the scan automatically
    (prices are cached, so the rerun takes seconds)."""
    try:
        _progress(f"{len(_state['pending'])} pick(s) are awaiting verification — "
                  f"will re-verify automatically when the data source responds "
                  f"(checking every {wait}s)...")
        for _ in range(attempts):
            time.sleep(wait)
            if _state["status"] == "running" or _state["bt_status"] == "running":
                return          # user is driving; stand down
            if not _state["pending"]:
                return          # already resolved (manual rerun)
            sess, _crumb = screener._yahoo_auth_session()
            if sess is None:
                continue
            with _lock:
                if _state["status"] == "running":
                    return
                _state["status"] = "running"
            _progress("Fundamentals source is back — re-verifying pending picks...")
            _auto["running"] = False   # allow a follow-up cycle if still blocked
            _run_scan(params)
            return
    finally:
        _auto["running"] = False


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
    # bound the work so the request can never outlive the instance
    capped = None
    if params.get("universe_max", 0) > WEB_SCAN_MAX:
        capped = params["universe_max"]
        params["universe_max"] = WEB_SCAN_MAX
    with _lock:
        if _state["status"] == "running":
            return jsonify({"ok": False, "message": "A scan is already running."}), 409
        _state.update(status="running", log=[], error=None,
                      started_at=time.time(), last_progress_ts=time.time(),
                      finished_at=None,
                      rejection_summary=[], near_misses=[], params_used=params,
                      near_board=[], relax_hints={}, scanned=None, health=None,
                      pending=[])
        _auto["cycles"] = 0   # a manual run resets the auto-verify budget
        try:   # the latest scan's filters double as the daily-alert profile
            with open(ALERT_PROFILE, "w") as f:
                json.dump(params, f)
        except Exception:
            pass
        if capped:
            _state["log"].append(
                f"Live scan limited to the {WEB_SCAN_MAX} largest stocks (you "
                f"asked for {capped}) so it finishes on this server. The full "
                f"{capped}-stock scan runs on schedule — that is the one shown "
                f"by default.")
        _state["capped_universe"] = capped
        threading.Thread(target=_run_scan, args=(params,), daemon=True).start()
    return jsonify({"ok": True, "params": params, "capped_from": capped,
                    "universe_max": params["universe_max"]})


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
        # record which rules this simulation belongs to, so neither the page
        # nor a script can mistake a previous run's numbers for these rules
        _state["bt_rules"] = {k: params.get(k) for k in market_db.TECH_PARAMS}
        bt = backtest_mod.run_backtest(params, None,
                                       screener._cache.get("universe") or [],
                                       progress=_progress)
        _state["backtest"] = bt
        _state["bt_status"] = "done"
        _progress(("Simulation restored from the database: " if bt.get("from_db")
                   else "Simulation complete: ") +
                  f"{bt['n']} historical trades across "
                  f"{bt.get('n_stocks', 0)} stocks.")
        _save_snapshot(params)
    except Exception as e:
        _state["bt_status"] = "error"
        _progress(f"Simulation failed: {type(e).__name__}: {e}")


@app.route("/backtest", methods=["POST"])
def run_backtest_route():
    overrides = request.get_json(silent=True) or {}
    params = screener.clean_params(overrides)
    if not screener._cache.get("universe") and not market_db.load_backtest(params):
        return jsonify({"ok": False, "message":
                        "These rules have not been simulated yet, and there is "
                        "no scanned universe to simulate them over — run a "
                        "scan first."}), 400
    with _lock:
        if _state["bt_status"] == "running" or _state["status"] == "running":
            return jsonify({"ok": False, "message": "Something is already running."}), 409
        _state.update(bt_status="running", backtest=None, bt_rules=None)
        threading.Thread(target=_run_backtest_thread, args=(params,),
                         daemon=True).start()
    return jsonify({"ok": True})


# ---- Yahoo auth mirror: the crumb lives on ephemeral disk, so the browser
# ---- keeps a copy (like the journal) and hands it back after a redeploy
@app.route("/edge/export")
def edge_export():
    """Simulation aggregates — mirrored by the browser so per-stock win
    rates survive the free tier's disk wipes (same pattern as the journal)."""
    return jsonify({"edge": market_db.export_edge()})


@app.route("/edge/restore", methods=["POST"])
def edge_restore():
    body = request.get_json(silent=True) or {}
    added = market_db.restore_edge(body.get("edge") or [])
    # retro-annotate whatever is currently on screen
    if added and _state.get("results") and _state.get("params_used"):
        try:
            edge = market_db.edge_for(_state["params_used"])
            for row in _state["results"] + (_state.get("near_board") or []):
                e = edge.get(row.get("ticker"))
                if e:
                    row["hist"] = f"{e['win_rate']:.0f}% of {e['n']}"
                    row["hist_avg_r"] = e["avg_r"]
        except Exception:
            pass
    return jsonify({"ok": True, "restored": added})


@app.route("/snapshot/load", methods=["POST"])
def snapshot_load():
    """Serve the stored scan for the filters the page is currently showing.

    This is what makes re-scanning optional: change a filter and the page
    asks whether that combination has been scanned before. If it has, the
    stored result is loaded as-is. If it hasn't, nothing is invented — the
    page says so and waits for Run."""
    overrides = request.get_json(silent=True) or {}
    params = screener.clean_params(overrides)
    with _lock:
        if _state["status"] == "running":
            return jsonify({"ok": False, "found": False,
                            "message": "A scan is running."}), 409
        found = _load_snapshot(params)
    if found:
        return jsonify({"ok": True, "found": True,
                        "results_ts": _state.get("results_ts"),
                        "n_results": len(_state.get("results") or [])})
    stored = market_db.snapshot_index()
    return jsonify({"ok": True, "found": False, "n_stored": len(stored),
                    "message": "These filters have not been scanned yet."})


@app.route("/limits")
def limits():
    """The known-issues page, served from the file that ships with the build.

    A screener that publishes only what works is not evidence of anything.
    Serving this from the repository rather than a hand-maintained HTML
    block means it cannot silently drift away from what the code does —
    it is reviewed in the same diff as the behaviour it describes.
    """
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                        "KNOWN_ISSUES.md")
    try:
        with open(path, encoding="utf-8") as f:
            text = f.read()
    except OSError:
        return Response("Known-issues file is missing from this build.\n",
                        status=500, mimetype="text/plain")
    return Response(text, mimetype="text/markdown; charset=utf-8")


@app.route("/changelog")
def changelog():
    """What changed and when, read from the deployed git history.

    Not a hand-written list: a hand-written changelog is a claim, and this
    one is the record. If git is unavailable in the running image the
    route says so rather than inventing entries.
    """
    import subprocess
    root = os.path.dirname(os.path.abspath(__file__))
    try:
        out = subprocess.run(
            ["git", "log", "-40", "--no-merges", "--date=short",
             "--pretty=format:%ad\t%s"],
            cwd=root, capture_output=True, text=True, timeout=10)
        if out.returncode != 0 or not out.stdout.strip():
            raise RuntimeError(out.stderr.strip() or "no history")
    except Exception as e:
        return Response(f"Change history unavailable in this build ({e}).\n"
                        f"The repository holds the full record.\n",
                        status=503, mimetype="text/plain")
    lines = ["# Changelog", "",
             "Generated from the deployed commit history — this is the record,",
             "not a summary of it.", ""]
    for row in out.stdout.splitlines():
        date, _, subject = row.partition("\t")
        lines.append(f"- **{date}** — {subject}")
    return Response("\n".join(lines) + "\n",
                    mimetype="text/markdown; charset=utf-8")


@app.route("/configs")
def configs_route():
    """Every simulated rule set with its metrics, and whether it clears the
    tradeable bar — profit factor >= 1.5, drawdown no worse than the
    benchmark, Sortino >= 1.0 — judged on the realistic portfolio."""
    rows = []
    for r in market_db.list_backtests():
        m = r.get("metrics") or {}
        pf_view = m.get("portfolio") or {}
        pf = pf_view.get("profit_factor", m.get("profit_factor"))
        mdd = pf_view.get("mdd_pct", m.get("mdd_pct"))
        sortino = pf_view.get("sortino", m.get("sortino"))
        spy_mdd = (m.get("spy") or {}).get("mdd_pct")
        fails = []
        if pf is not None and pf < 1.5:
            fails.append(f"profit factor {pf} < 1.5")
        if mdd is not None:
            if spy_mdd is not None and mdd > spy_mdd:
                fails.append(f"drawdown -{mdd}% worse than SPY -{spy_mdd}%")
            elif spy_mdd is None and mdd > 20:
                fails.append(f"drawdown -{mdd}% worse than -20%")
        if sortino is not None and sortino < 1.0:
            fails.append(f"Sortino {sortino} < 1.0")
        rows.append({
            "hash": r["hash"], "ran_at": r["ran_at"],
            "n_trades": r["n_trades"], "n_stocks": r["n_stocks"],
            "from": r["from"], "to": r["to"],
            "rules": {k: r["params"].get(k) for k in market_db.TECH_PARAMS},
            "profit_factor": pf, "mdd_pct": mdd, "sortino": sortino,
            "win_rate_pct": pf_view.get("win_rate_pct", m.get("win_rate_pct")),
            "return_pct": pf_view.get("return_pct", m.get("return_pct")),
            "spy": m.get("spy"),
            "clears_bar": (bool(m) and not fails),
            "fails": fails,
            "measured": bool(m),
        })
    cleared = [r for r in rows if r["clears_bar"]]
    return jsonify({"configs": rows, "n": len(rows), "n_clearing": len(cleared)})


@app.route("/snapshot/index")
def snapshot_index_route():
    """Which filter combinations already have a stored scan."""
    return jsonify({"snapshots": [
        {"saved_at": s["saved_at"], "n_results": s["n_results"],
         "min_rr": (s["params"] or {}).get("min_rr"),
         "rsi_low": (s["params"] or {}).get("rsi_low"),
         "rsi_high": (s["params"] or {}).get("rsi_high"),
         "universe_max": (s["params"] or {}).get("universe_max")}
        for s in market_db.snapshot_index()]})


@app.route("/snapshot/export")
def snapshot_export():
    """The last scan + simulation, mirrored by the browser so a redeploy
    (which wipes the free tier's disk) doesn't send you back to an empty
    page. Same pattern as the journal and edge-stat mirrors."""
    return jsonify({"snapshot": market_db.latest_snapshot()})


@app.route("/snapshot/restore", methods=["POST"])
def snapshot_restore():
    body = request.get_json(silent=True) or {}
    snap = body.get("snapshot") or {}
    if not isinstance(snap, dict) or not snap.get("results_ts"):
        return jsonify({"ok": True, "restored": False})
    # never let a mirror overwrite something newer that is already loaded
    have = _state.get("results_ts") or 0
    try:
        incoming = float(snap.get("results_ts") or 0)
    except (TypeError, ValueError):
        return jsonify({"ok": True, "restored": False})
    if incoming <= float(have):
        return jsonify({"ok": True, "restored": False})
    params = snap.get("_params")
    if not params:
        return jsonify({"ok": True, "restored": False})
    market_db.save_snapshot(params, {k: snap.get(k) for k in SNAPSHOT_KEYS})
    return jsonify({"ok": True, "restored": bool(_load_snapshot(params))})


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


# A scan that has said nothing for this long is not working, whatever its
# status field claims. Reporting that beats spinning silently forever.
STALL_AFTER_S = float(os.environ.get("STALL_AFTER_S", "300"))


@app.route("/status")
def status():
    st = dict(_state)
    if st.get("status") == "running":
        quiet_since = st.get("last_progress_ts") or st.get("started_at")
        quiet_for = time.time() - float(quiet_since or time.time())
        if quiet_for > STALL_AFTER_S:
            st["stalled_s"] = round(quiet_for)
    # Prices are only as fresh as the last session, and a weekend adds
    # hours but no sessions. Reporting both lets the page stop calling
    # Friday's close "10 hours old" on a Saturday.
    try:
        st["market"] = market_clock.state()
        if st.get("results_ts"):
            st["freshness"] = market_clock.staleness(float(st["results_ts"]))
    except Exception:
        pass
    return jsonify(st)


@app.route("/cancel", methods=["POST"])
def cancel():
    """Clear a scan that is no longer making progress, so the page can show
    stored results again instead of a spinner that never resolves. The
    worker thread is left to die on its own — killing it mid-download risks
    a half-written state, and it holds nothing the next scan needs."""
    with _lock:
        if _state["status"] != "running":
            return jsonify({"ok": False, "message": "Nothing is running."}), 409
        quiet_since = _state.get("last_progress_ts") or _state.get("started_at")
        quiet_for = time.time() - float(quiet_since or time.time())
        _state.update(status="error", finished_at=time.time(),
                      error=f"Scan abandoned after {round(quiet_for)}s without "
                            f"progress — it was most likely stuck waiting on "
                            f"Yahoo. Press Run to try again.")
        _state["log"].append(_state["error"])
    # fall back to whatever we can legitimately show instead of nothing
    restored = _load_published(force=True) or _load_snapshot()
    return jsonify({"ok": True, "restored_previous": bool(restored)})


@app.route("/published")
def published_route():
    """What the scheduled scan has published, and how old it is."""
    idx = _published_index()
    return jsonify({"base": PUBLISHED_BASE, "index": idx,
                    "using": _state.get("published_preset")})


@app.route("/published/refresh", methods=["POST"])
def published_refresh():
    """Pull the newest published scan now instead of waiting for the poll."""
    with _lock:
        if _state["status"] == "running":
            return jsonify({"ok": False, "message": "A scan is running."}), 409
        adopted = _load_published(force=True)
    return jsonify({"ok": True, "adopted": adopted,
                    "results_ts": _state.get("results_ts"),
                    "n_results": len(_state.get("results") or [])})


def _send_alert(text: str) -> list[str]:
    """Deliver to whichever channels are configured via env vars."""
    import requests as rq
    sent = []
    tok = os.environ.get("ALERT_TELEGRAM_TOKEN")
    chat = os.environ.get("ALERT_TELEGRAM_CHAT")
    if tok and chat:
        try:
            r = rq.post(f"https://api.telegram.org/bot{tok}/sendMessage",
                        json={"chat_id": chat, "text": text}, timeout=15)
            if r.status_code == 200:
                sent.append("telegram")
        except Exception:
            pass
    hook = os.environ.get("ALERT_DISCORD_WEBHOOK")
    if hook:
        try:
            r = rq.post(hook, json={"content": text[:1900]}, timeout=15)
            if r.status_code in (200, 204):
                sent.append("discord")
        except Exception:
            pass
    return sent


@app.route("/alert", methods=["GET", "POST"])
def alert():
    """Scheduled entry point (GitHub Actions cron): rerun the saved filter
    profile and push the verified top picks to the configured channels."""
    with _lock:
        if _state["status"] == "running" or _state["bt_status"] == "running":
            return jsonify({"ok": False, "message": "busy"}), 409
        _state["status"] = "running"
    params = {}
    try:
        with open(ALERT_PROFILE) as f:
            params = json.load(f)
    except Exception:
        pass
    _run_scan(params)
    res = _state["results"] or []
    pend = _state["pending"] or []
    lines = [f"📈 Dip Finder — {time.strftime('%Y-%m-%d %H:%M UTC', time.gmtime())}"]
    br = _state.get("breadth") or {}
    if br.get("risk_factor", 1) < 1:
        lines.append(f"⚠ defensive market (breadth {br.get('pct')}%) — "
                     f"sizes throttled to {br['risk_factor']:g}x")
    if res:
        lines.append(f"{len(res)} verified pick(s):")
        for r in res[:TOP_N]:
            lines.append(f"  {r['ticker']}: buy ≈{r['price']}, stop {r['stop']}, "
                         f"target {r['resistance']}, {r['shares']} sh, "
                         f"risk €{r['risk_EUR']} (R:R {r['RR']}, score {r['score']})")
    else:
        lines.append("no verified picks today"
                     + (f" ({len(pend)} awaiting data verification)" if pend else ""))
    lines.append("https://pullback-screener.onrender.com")
    text = "\n".join(lines)
    sent = _send_alert(text)
    if not sent:
        return jsonify({"ok": True, "sent": [], "verified": len(res),
                        "note": "no channels configured — set ALERT_TELEGRAM_TOKEN"
                                "+ALERT_TELEGRAM_CHAT and/or ALERT_DISCORD_WEBHOOK"})
    return jsonify({"ok": True, "sent": sent, "verified": len(res),
                    "pending": len(pend)})


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
    # The export must carry the blocked picks too. The page shows them as
    # BLOCKED rows; a CSV that quietly contains only the 25 that passed
    # would let someone act on a filtered list while believing they had
    # the whole scan — the same fail-open the quarantine exists to stop.
    rows = [dict(r, row_type="pick") for r in _state["results"]]
    rows += [dict(r, row_type="BLOCKED", score=None, shares=None,
                  risk_EUR=None, cum_risk_EUR=None)
             for r in (_state.get("pending") or [])]
    df = pd.DataFrame(rows)
    if "row_type" in df.columns:      # lead with it; it changes how to read the row
        df.insert(0, "row_type", df.pop("row_type"))
    if _state.get("results_ts"):   # every exported row carries its scan time
        stamp = time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime(_state["results_ts"]))
        df.insert(0, "scan_time", stamp)
    df.to_csv(buf, index=False)
    return Response(buf.getvalue(), mimetype="text/csv",
                    headers={"Content-Disposition": "attachment; filename=screener_results.csv"})


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 8000)), debug=True)
