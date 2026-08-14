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
from flask import Flask, Response, jsonify, redirect, render_template, request

import backtest as backtest_mod
import brief
import db as market_db
import cache_store
import credit
import journal
import market_clock
import portfolio_import
import analysis
import patterns
import plan
import pretrade
import ranking
import screener

app = Flask(__name__)
# /tmp is RAM-backed on the free instance, so an unbounded POST body is
# charged straight against the 512MB ceiling. The largest legitimate body
# is a portfolio CSV, already capped at 2MB by its own handler.
app.config["MAX_CONTENT_LENGTH"] = 4 * 1024 * 1024


@app.template_filter("num")
def _num(value, places: int = 2, dash: str = "—", sign: bool = False):
    """Format a number, or show a dash — never take the page down.

    `'%.2f'|format(None)` raises TypeError, and Jinja lets that escape as
    a 500. The whole product is one page, so a single null in one row of
    one table is total unavailability. Every number the templates print
    goes through here: a missing figure is a missing figure, and says so.
    """
    try:
        return ("%+.*f" if sign else "%.*f") % (int(places), float(value))
    except (TypeError, ValueError):
        return dash


@app.template_filter("usd_compact")
def _usd_compact(value, dash: str = "—"):
    """$57,843,260,493 -> "$57.8B" — a 13F position's raw dollar value is
    unreadable at a glance in a table row; this is display only, never
    used for any comparison or gate."""
    try:
        v = float(value)
    except (TypeError, ValueError):
        return dash
    sign = "-" if v < 0 else ""
    v = abs(v)
    for cut, suffix in ((1e12, "T"), (1e9, "B"), (1e6, "M"), (1e3, "K")):
        if v >= cut:
            return f"{sign}${v / cut:.1f}{suffix}"
    return f"{sign}${v:.0f}"


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
    "capped_universe": None,   # a page scan was trimmed from this many stocks
    "generation": 0,           # bumped on cancel; retires an abandoned worker
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
# The longest a single published file may take before the fetch is
# abandoned and the shipped copy answers instead. Generous, because the
# price book is 650KB over a free instance's egress; bounded, because a
# fetch with no ceiling is how a book stays empty forever.
PUBLISHED_FETCH_S = float(os.environ.get("PUBLISHED_FETCH_S", "75"))
_published = {"ts": 0.0, "index": None}


# Where each published file last came from, and why it did not. Without
# this, "the book is empty" is indistinguishable from "the file is
# missing" and from "the copy on disk will not parse" — and I spent an
# hour guessing between them instead of looking.
_published_reads: dict = {}


# Where each published file last came from. Without this, "serving a
# frozen copy shipped with the build" and "serving the live data
# branch" look identical — and I once deleted the shipped files on
# the strength of an index that had come from those very files.


def _published_get(path: str):
    """A published file: from the data branch, falling back to any copy
    shipped with this build.

    The network is tried FIRST, which is the opposite of the original
    order. A shipped copy is frozen at build time, so preferring it meant
    that the moment the scan stopped committing results into the deployed
    branch, the site would serve the same board forever with fresher data
    one request away. Reading the branch directly needs the repository to
    be public, which it now is.

    Which source answered is recorded, because the two are otherwise
    indistinguishable from outside — and that is exactly how the shipped
    files came to be deleted on the strength of an index that had been
    served by those very files.
    """
    local = os.path.join(PUBLISHED_DIR, path)

    def _fallback(why):
        try:
            if os.path.exists(local):
                with open(local) as f:
                    data = json.load(f)
                _published_reads[path] = f"shipped copy ({why})"
                return data
            _published_reads[path] = f"unavailable ({why}, no shipped copy)"
        except Exception as e:
            _published_reads[path] = f"shipped copy unreadable: {type(e).__name__}"
        return None

    import requests as rq
    url = f"{PUBLISHED_BASE}/{path}"
    tok = os.environ.get("PUBLISHED_TOKEN") or os.environ.get("GITHUB_TOKEN")

    # Anonymous FIRST, and the token only as a retry.
    #
    # Measured: this exact URL returns 200 with no header and 404 with a
    # token attached. raw.githubusercontent answers a token it does not
    # accept with 404, not 401 — so a credential that has expired, or was
    # scoped to something else, makes a PUBLIC file look deleted. The repo
    # needed that token while it was private; now it needs the opposite,
    # and nothing in the response distinguishes "gone" from "your token is
    # stale" except trying without it.
    attempts = [("data branch", {})]
    if tok:
        attempts.append(("data branch (with token)", {"Authorization": f"token {tok}"}))
    why = "no attempt made"
    for label, headers in attempts:
        got = {"done": threading.Event()}

        def _fetch(headers=headers, got=got):
            try:
                r = rq.get(url, headers=headers, timeout=20)
                got["code"] = r.status_code
                if r.status_code == 200:
                    got["value"] = r.json()
            except Exception as e:                          # noqa: BLE001
                got["error"] = type(e).__name__
            finally:
                got["done"].set()

        # A TOTAL bound, which requests does not give you: its timeout is
        # the gap between bytes, so a body that trickles in never trips it
        # at all. The price book is 650KB, and a fetch of it that never
        # returns leaves this book — and the credit standings and the
        # overlap measurement that read it — empty for the life of the
        # process, with nothing anywhere saying why. Watched exactly that
        # happen on the live instance: the warmer thread alive, running,
        # and two minutes into a single GET.
        threading.Thread(target=_fetch, name=f"get-{path}", daemon=True).start()
        if not got["done"].wait(PUBLISHED_FETCH_S):
            # the thread is abandoned, not killed — it will finish into a
            # dict nobody reads, and the next refresh tries again
            why = f"no answer within {PUBLISHED_FETCH_S:.0f}s"
            continue
        if "value" in got:
            _published_reads[path] = label
            return got["value"]
        why = got.get("error") or f"HTTP {got.get('code')}"
    return _fallback(why)


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
    # The track record travels with the results for the same reason. Render
    # wipes the disk on every deploy, so a journal that lives only here is
    # empty within hours of being written — the live site was reporting
    # "0 picks recorded" while the runner held the full history. Restoring
    # is additive: existing (ticker, scan_date) rows are kept, so a pick
    # logged locally is never overwritten by the published copy.
    rows = payload.get("journal_rows")
    if rows:
        try:
            added = journal.restore(rows)
            _state["journal"] = journal.snapshot()
            if added:
                _state["log"].append(
                    f"Track record: restored {added} logged pick(s) published "
                    f"by the scheduled scan — the local disk is wiped on every "
                    f"deploy, so the history lives with the results.")
        except Exception as e:
            _state["log"].append(f"Track-record restore skipped: {e}")
    _state["published_preset"] = preset["preset"]
    _state["log"].append(
        f"Results published by the scheduled scan ({preset['preset']} preset) — "
        f"no scanning was needed to show them.")
    return True


def _load_published(force: bool = False, override_newer: bool = False) -> bool:
    """Adopt the freshest published preset.

    `force` skips the index cache. `override_newer` additionally
    replaces results that are newer than the published ones — only
    ever set when a human explicitly asks for the full scan back.
    """
    idx = _published_index(force)
    if not idx or not idx.get("presets"):
        return False
    best = max(idx["presets"], key=lambda p: p.get("results_ts") or 0)
    have = float(_state.get("results_ts") or 0)
    # Two different questions, and they were sharing one flag. `force` only
    # ever meant "skip the index cache". `override_newer` means a human
    # asked for the published scan back.
    #
    # That second case had no way to say yes, and it made a recoverable
    # state permanent: a 250-stock scan started from the page is NEWER than
    # the 1,500-stock scheduled scan, so it blocked its own replacement.
    # Pressing Run once left the site on the smaller result — 0 rows, in
    # the run that found this — until the next scheduled scan hours later.
    # Automatic polling must still refuse; only an explicit request wins.
    if not override_newer and float(best.get("results_ts") or 0) <= have:
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
# usually fresher than anything this instance still has after a restart.
# SKIP_WARM suppresses it under test. Not for speed: importing this module
# once with a live network fetches the REAL published index and persists it
# to the cache, so a test that then installs a stub and reloads gets the
# real branch's answer instead of its fixture — which is how CI ended up
# adopting the "wide-net" preset in a test that had published "relaxed".
# The test that cares about startup adoption calls this itself.
if os.environ.get("SKIP_WARM") or not _load_published():
    if not _load_snapshot():
        _load_cached_csv()
try:
    _state["journal"] = journal.snapshot()
except Exception:
    pass


# A cancelled scan keeps running — the thread is deliberately not killed.
# Every write it makes afterwards has to be discarded, or its rows and the
# next scan's filters end up in _state together and /status reports the
# pair as a finished result.
def _stale(gen) -> bool:
    return gen is not None and gen != _state.get("generation", 0)


def _progress(msg, gen=None):
    if _stale(gen):
        return
    for line in str(msg).splitlines():
        if line.strip():
            _state["log"].append(line)
            _state["last_progress_ts"] = time.time()
    # keep the log bounded
    if len(_state["log"]) > 500:
        _state["log"][:] = _state["log"][-500:]


def _on_partial(rows, scanned, total, pending=None, gen=None):
    """Stream qualified AND pending-verification picks while the scan runs."""
    if _stale(gen):
        return
    recs = sorted(rows, key=lambda r: r.get("score", 0), reverse=True)
    _state["results"] = recs
    _state["top_picks"] = recs[:TOP_N]
    _state["scanned"] = scanned
    _state["universe_size"] = total
    _state["results_ts"] = time.time()
    if pending is not None:
        _state["pending"] = pending


def _run_scan(params):
    gen = _state.get("generation", 0)
    try:
        result = screener.run_screener(
            params,
            progress=lambda m: _progress(m, gen),
            on_partial=lambda *a, **k: _on_partial(*a, gen=gen, **k))
        if _stale(gen):
            # cancelled while this was still downloading; its results
            # belong to a page nobody is looking at any more
            return
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
            # score_parts is a dict per row — pandas would stringify it on
            # write and hand back a broken non-dict on the next read, and
            # this file exists purely for warm-restart continuity, not as
            # a permanent store; drop it rather than round-trip it wrong.
            df.drop(columns=["score_parts"], errors="ignore").to_csv(RESULTS_CSV, index=False)

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


@app.route("/brief")
def index():
    """Retired: this was the front page before Today's Five existed.

    It carried two things worth keeping — the pre-trade check and the
    credit lookup — and they are on the root page now, in a collapsed
    "check something before you trade" section, rather than living
    behind a second URL a reader had to already know about. Everything
    else it carried (a collapsed pattern-screen table showing Stop/Target
    numbers from the entry rule the same table called a coin flip) is
    gone rather than moved: /full and /patterns already carry that
    ground, and a third copy was a third place for it to drift.

    Redirects rather than 404s, so an old bookmark still lands somewhere.
    A literal "/" rather than url_for("today_page") — that endpoint has
    two rules ("/" and "/today") and which one url_for resolves to is not
    a contract worth depending on here.
    """
    return redirect("/", code=302)


@app.route("/full")
def full_board():
    """The whole table, for anyone who wants to see the working."""
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


# ---- the price book behind the pre-trade check ----
# 60 daily closes per scanned ticker, published by the scheduled scan.
# It exists so the check can measure correlation against a reader's own
# holdings without a per-ticker Yahoo call — those are rate-limited and
# fail outright from a datacenter IP, which is the whole reason this
# cannot be done live.
_book = {"ts": 0.0, "data": None}
BOOK_TTL = float(os.environ.get("BOOK_TTL", "3600"))
BOOKS_POLL_S = float(os.environ.get("BOOKS_POLL_S", "300"))
RESULTS_POLL_S = float(os.environ.get("RESULTS_POLL_S", "600"))


def _price_book(fetch: bool = False) -> dict:
    """Whatever is in memory. Only the warmer passes fetch=True.

    Same rule as the earnings calendar, for the same reason: this is a
    ~600KB read over the network, and a request that performs it is a
    request that can take ten seconds. The warmer owns the fetch; every
    request reads the result or gets nothing and says so.
    """
    if not fetch:
        return _book["data"] or {}
    t0 = time.time()
    data = _published_get("prices.json")
    if not data:
        # A refresh that comes back empty is a failed refresh, not an empty
        # book. Storing it anyway replaced a good copy with nothing on the
        # first transient miss — which is how a book that loaded 1,453
        # entries at boot was serving none of them ten minutes later.
        print(f"[warm] price book: refresh returned nothing, keeping "
              f"{len(_book['data'] or {})}", flush=True)
        return _book["data"] or {}
    _book.update(data=data, ts=time.time())
    print(f"[warm] price book fetched: "
          f"{len(pretrade._series_of(data))} tickers in "
          f"{time.time() - t0:.1f}s", flush=True)
    return data


_vols = {"ts": 0.0, "data": None}


def _vol_book(fetch: bool = False) -> dict:
    """Published per-ticker volatility, measured over the scan's full history.

    The price book is 60 closes, which is enough to correlate two stocks
    and not enough to estimate an annualised volatility the credit model
    can lean on. This file is one float per ticker computed from years of
    returns, so it is small enough to carry at full length.
    """
    if not fetch:
        return _vols["data"] or {}
    data = _published_get("vol.json")
    if not data:
        # A refresh that comes back empty is a failed refresh, not an empty
        # book. Storing it anyway replaced a good copy with nothing on the
        # first transient miss — which is how a book that loaded 1,453
        # entries at boot was serving none of them ten minutes later.
        print(f"[warm] volatility book: refresh returned nothing, keeping "
              f"{len(_vols['data'] or {})}", flush=True)
        return _vols["data"] or {}
    _vols.update(data=data, ts=time.time())
    print(f"[warm] volatility book: {len(data)} tickers", flush=True)
    return data


_creds = {"ts": 0.0, "data": None}


def _credit_book(fetch: bool = False) -> dict:
    """Credit standings computed by the scan and published.

    The SEC rate-limits by IP and refuses this host outright — every call
    times out here while the same request from another address answers in
    0.3 seconds. Computing these on a runner and reading the result is the
    same move the scan itself made for Yahoo, for the same reason, and it
    means a reader gets a credit standing with no outbound call and a peer
    ranking that already exists.
    """
    if not fetch:
        return _creds["data"] or {}
    data = _published_get("credit.json")
    if not data:
        # A refresh that comes back empty is a failed refresh, not an empty
        # book. Storing it anyway replaced a good copy with nothing on the
        # first transient miss — which is how a book that loaded 1,453
        # entries at boot was serving none of them ten minutes later.
        print(f"[warm] credit book: refresh returned nothing, keeping "
              f"{len(_creds['data'] or {})}", flush=True)
        return _creds["data"] or {}
    _creds.update(data=data, ts=time.time())
    print(f"[warm] credit book: {len(data)} companies", flush=True)
    return data


_earn_pub = {"ts": 0.0, "data": None}
_patterns_pub = {"ts": 0.0, "data": None}


def _patterns_book(fetch: bool = False) -> dict:
    """The published pattern sweep, warmed in the background like the rest.

    This exists because reading it inline cost the front page 75 seconds.
    _published_get is a NETWORK fetch bounded by PUBLISHED_FETCH_S, and
    the /today route called it on every cache miss — so a cold instance
    served the root only after that fetch finished or gave up. The
    release gate found it the honest way: twenty-three assertions passed
    and the twenty-fourth timed out navigating to "/".

    The other four books have always been warmed on a thread for exactly
    this reason. This one now is too, and the route reads whatever is in
    hand rather than going to the network in front of a reader.
    """
    if not fetch:
        return _patterns_pub["data"] or {}
    data = _published_get("patterns.json")
    if not data:
        return _patterns_pub["data"] or {}
    _patterns_pub.update(data=data, ts=time.time())
    n = sum(len(v or {}) for v in (data.get("horizons") or {}).values())
    print(f"[warm] pattern sweep: {n} combinations, "
          f"{len(data.get('tradeable') or [])} confirmed", flush=True)
    return data


def _earnings_book(fetch: bool = False) -> dict:
    """The earnings calendar the scan already built, published.

    The instance used to rebuild this from ~32 sequential Nasdaq calls
    after every deploy, and while it did, every pre-trade check answered
    "calendar is still loading — try again in a minute". That was the
    single most common thing a user actually saw this tool say. The
    runner has the finished map minutes before any deploy completes;
    reading it makes the block disappear.
    """
    if not fetch:
        return _earn_pub["data"] or {}
    data = _published_get("earnings.json")
    if not data:
        return _earn_pub["data"] or {}
    _earn_pub.update(data=data, ts=time.time())
    print(f"[warm] earnings calendar (published): "
          f"{len(data.get('map') or {})} companies", flush=True)
    return data


_liq_pub = {"ts": 0.0, "data": None}


def _liq_book(fetch: bool = False) -> dict:
    """Published 30-day average dollar volume per ticker, computed by the
    scan from the same OHLCV frame it already downloads.

    Real liquidity, not the market-cap proxy /today's filter fell back to
    because volume was never published anywhere. A ticker absent here
    (FX could not be established, or it never traded in the window) keeps
    the market-cap fallback — flagged, never silently treated as measured.
    """
    if not fetch:
        return _liq_pub["data"] or {}
    data = _published_get("liquidity.json")
    if not data:
        print(f"[warm] liquidity book: refresh returned nothing, keeping "
              f"{len(_liq_pub['data'] or {})}", flush=True)
        return _liq_pub["data"] or {}
    _liq_pub.update(data=data, ts=time.time())
    print(f"[warm] liquidity book: {len(data)} tickers", flush=True)
    return data


_inv13f_pub = {"ts": 0.0, "data": None}


def _inv13f_book(fetch: bool = False) -> dict:
    """Published 13F roll-up (superinvestors.py, via the weekly thirteenf.yml
    workflow) — who among a curated set of managers holds what, read from
    SEC filings.

    Unlike the other published books, absence of this file is a normal,
    expected state right after this feature ships (the first weekly run
    has not happened yet) and stays fully honest either way: `/investors`
    and the note-line on /check both render nothing rather than a zero
    when this book is empty, exactly like liquidity.json's unmapped-name
    handling never renders an absence as a measurement.
    """
    if not fetch:
        return _inv13f_pub["data"] or {}
    data = _published_get("investors13f.json")
    if not data:
        print(f"[warm] 13F book: refresh returned nothing, keeping "
              f"{'a book' if _inv13f_pub['data'] else 'nothing'}", flush=True)
        return _inv13f_pub["data"] or {}
    _inv13f_pub.update(data=data, ts=time.time())
    print(f"[warm] 13F book: {data.get('n_managers_ok')} managers, "
          f"{len(data.get('tickers') or {})} tickers", flush=True)
    return data


_regime_pub = {"ts": 0.0, "data": None}


def _regime_book(fetch: bool = False) -> dict:
    """Market regime (SPY / Euro Stoxx 50 vs 200-day SMA), computed by the scan.

    screener.run() already computes and gates on this internally
    (require_market_uptrend) but never published the verdict — so a name
    could clear every filter during a confirmed downtrend with nothing
    anywhere saying the backdrop itself had turned. This is one JSON file
    with two booleans, not a fourth outbound call from this instance.
    """
    if not fetch:
        return _regime_pub["data"] or {}
    data = _published_get("regime.json")
    if not data:
        print(f"[warm] market regime: refresh returned nothing, keeping "
              f"{_regime_pub['data']}", flush=True)
        return _regime_pub["data"] or {}
    _regime_pub.update(data=data, ts=time.time())
    print(f"[warm] market regime: "
          f"{', '.join(f'{k}={v}' for k, v in data.items() if k != 'as_of')}",
          flush=True)
    return data


def _regime_notes(regime: dict) -> list:
    """Plain-language notice for each region confirmed BELOW its 200-day
    average — silent on uptrend and silent when unmeasured, matching the
    rest of this page's rule of never flagging the normal case. A reader
    is told when the backdrop is working against every name on the list,
    not shown a gauge for it.
    """
    labels = {"US": "The S&P 500 (SPY)", "EU": "The Euro Stoxx 50"}
    return [f"{labels.get(region, region)} is below its 200-day average — a "
            f"defensive stretch. The usual playbook is smaller size and "
            f"faster exits; nothing below enforces that for you."
            for region in ("US", "EU") if regime.get(region) is False]


def _published_earnings() -> tuple[dict, bool, set]:
    """(ticker -> trading days to report, complete, single_source) —
    re-based to today, exactly as screener does for its own stored copy,
    refused when older than three days rather than served stale.

    `complete` is a claim about the US bulk calendar only — it says
    nothing about EU names. `single_source` names every ticker whose
    date came from the EU book (Yahoo per-ticker alone, no bulk
    calendar corroboration), so a caller can tell a US date (backed by a
    complete calendar) apart from a European one (backed by one source)
    even though both live in the same returned map.
    """
    book = _earnings_book()
    m, as_of = book.get("map") or {}, book.get("as_of")
    cal: dict = {}
    fresh = False
    if m and as_of:
        try:
            import datetime as _d
            shift = (_d.date.today()
                     - _d.date.fromisoformat(str(as_of))).days
            if shift <= 3:          # stale enough to be wrong about "clear"
                cal = {t: d - shift for t, d in m.items() if d - shift >= 0}
                fresh = True
        except (TypeError, ValueError):
            pass

    single_source: set = set()
    eu = book.get("eu") or {}
    eu_map, eu_as_of = eu.get("map") or {}, eu.get("as_of")
    if eu_map and eu_as_of:
        try:
            import datetime as _d
            eu_shift = (_d.date.today()
                       - _d.date.fromisoformat(str(eu_as_of))).days
            if eu_shift <= 3:
                for t, d in eu_map.items():
                    dd = d - eu_shift
                    if dd >= 0 and t not in cal:   # US source never overridden
                        cal[t] = dd
                        single_source.add(t)
        except (TypeError, ValueError):
            pass

    return cal, fresh and bool(book.get("complete")), single_source


_credit_view_memo = {"key": None, "data": {}}


def _credit_view() -> dict:
    """The published book, re-solved against the latest close.

    An entry is a filing plus a price, and only the price moves daily.
    Restating it here is arithmetic on data already in memory, and it is
    what lets the scheduled run stop re-measuring companies it measured
    yesterday just to pick up a newer price — which is the whole reason
    coverage had stalled at 97 names.

    A name that cannot be restated keeps its stored figure. Falling back
    to what was published is honest; showing nothing because a price is
    missing would lose a measurement that was genuinely made.
    """
    book = _credit_book()
    if not book:
        return {}
    key = (_creds["ts"], _book["ts"])
    if _credit_view_memo["key"] == key:
        return _credit_view_memo["data"]
    series = pretrade._series_of(_price_book()) or {}
    out = {}
    for t, rep in book.items():
        if not isinstance(rep, dict):
            continue
        closes = [c for c in (series.get(t) or []) if c]
        out[t] = (credit.restate(rep, closes[-1]) if closes else None) or rep
    n_re = sum(1 for r in out.values() if r.get("restated"))
    _credit_view_memo.update(key=key, data=out)
    print(f"[credit] restated {n_re} of {len(out)} against the latest close",
          flush=True)
    return out


def _results_refresher():
    """Adopt each new scan without waiting for a redeploy.

    Results were adopted once, at boot, so the only way a new scan reached
    the page was the scan job committing into the deployed branch — which
    made Render redeploy, hourly, wiping the earnings calendar, the price
    book and the credit standings every time. That is the "still loading,
    try again in a minute" a reader met on the hour.

    Polling the data branch instead means the scan never touches the
    build. It needed the repository to be public, because the branch could
    not be read without a token before.
    """
    first = True
    while True:
        time.sleep(20 if first else RESULTS_POLL_S)
        first = False
        try:
            if _load_published(force=True):
                print(f"[poll] adopted a newer scan: "
                      f"{len(_state.get('results') or [])} picks", flush=True)
        except Exception as e:
            print(f"[poll] published results unavailable: {e}", flush=True)


def _book_refresher():
    """Keep the books warm without ever making a request wait for them."""
    while True:
        try:
            _price_book(fetch=True)
        except Exception as e:
            print(f"[warm] price book refresh failed: {e}", flush=True)
        try:
            _vol_book(fetch=True)
        except Exception as e:
            print(f"[warm] volatility book refresh failed: {e}", flush=True)
        try:
            _credit_book(fetch=True)
        except Exception as e:
            print(f"[warm] credit book refresh failed: {e}", flush=True)
        try:
            _liq_book(fetch=True)
        except Exception as e:
            print(f"[warm] liquidity book refresh failed: {e}", flush=True)
        try:
            _inv13f_book(fetch=True)
        except Exception as e:
            print(f"[warm] 13F book refresh failed: {e}", flush=True)
        # The books are local file reads on this deployment, so refreshing
        # them is nearly free — and the scan publishes hourly, so an
        # instance that only looked once an hour could sit half an hour
        # behind data already sitting on its own disk.
        time.sleep(BOOKS_POLL_S)


@app.route("/check", methods=["POST"])
def check_trade():
    """What a reader cannot work out from a free screener: how much of this
    trade they already own, whether the earnings date is genuinely verified,
    and whether their book gets wider or just heavier."""
    _t_start = time.time()
    body = request.get_json(silent=True) or {}
    ticker = str(body.get("ticker") or "").strip().upper()
    if not ticker:
        return jsonify({"ok": False, "error": "no ticker given"}), 400
    holdings = body.get("holdings") or []
    # the shipped page sends objects, but a hand-edited localStorage or an
    # API client can send bare tickers, and pretrade did h.get(...) on them
    if isinstance(holdings, dict):
        holdings = [{"ticker": k, "shares": v} for k, v in holdings.items()]
    if isinstance(holdings, list):
        holdings = [{"ticker": h} if isinstance(h, str) else h
                    for h in holdings if h]
        holdings = [h for h in holdings if isinstance(h, dict)]
    if isinstance(holdings, str):
        # The page's own textarea teaches "TICKER, shares, cost — one per
        # line", and this parser used to flatten that on commas AND
        # whitespace alike — so "MSFT, 10, 300" became three holdings
        # named MSFT, 10 and 300, and the check answered "4 of your 6
        # positions could not be compared" about a two-position book,
        # with the numbers rendered as tickers in the reply. Parse the
        # format the page teaches: one holding per line, ticker first.
        def _is_num(tok):
            return tok and not tok.replace(".", "").replace("-", "").isdigit()

        parsed = []
        for line in holdings.splitlines() or [holdings]:
            fields = [f.strip() for f in line.split(",") if f.strip()]
            if len(fields) > 1 and all(not _is_num(f) for f in fields[1:]):
                # "MSFT, 10, 300" — the taught format; the ticker leads
                # and everything after it is a quantity, never a position
                fields = fields[:1]
            for f in fields:
                parsed.extend({"ticker": tok} for tok in f.split()
                              if _is_num(tok))
        holdings = parsed

    book = _price_book()
    # Earnings come from the same published scan, so the answer here is the
    # one the board used — not a second opinion that could disagree with it.
    # Never build inside the request — see _earnings_calendar(build=...).
    # "Not built yet" and "built and came back broken" both block the pick,
    # but only one is worth waiting out, so they are distinguished by
    # whether the cache key exists rather than by whether the result is
    # empty — an empty result looks identical in both cases.
    earn, complete, warming = {}, False, False
    try:
        earn, complete = screener._earnings_calendar(build=False)
        if not earn:
            hit, _ = cache_store.fetch(f"earncal:{screener.EARN_CAL_DAYS}",
                                       screener.EARN_CAL_TTL)
            warming = not hit
    except Exception:
        pass
    if not earn:
        # the calendar the scan published — same data, already built, so
        # a fresh deploy answers instead of asking the reader to wait out
        # a warm-up they cannot see
        earn, complete, single_source = _published_earnings()
        if earn:
            warming = False
    else:
        # The live-built calendar above is Nasdaq's US-only bulk feed and
        # is warm on nearly every request, so this branch — not the one
        # above — is what actually runs almost always. Without this merge
        # a EU ticker's date from the published `eu` book never reached
        # /check at all: earn was never empty, so the fallback that reads
        # it never fired, and every EU pre-trade check answered "could not
        # be verified" even when the scheduled runner had a Yahoo date.
        pub_earn, _pub_complete, single_source = _published_earnings()
        for t, d in pub_earn.items():
            if t not in earn:
                earn[t] = d

    row = next((r for r in (_state.get("results") or [])
                if (r.get("ticker") or "").upper() == ticker), None)
    # A symbol nothing here has ever heard of must not collect green
    # ticks. "ZZZZ: no earnings due for at least 45 days" was a true
    # sentence about the calendar and a false impression about a company
    # that does not exist.
    series = pretrade._series_of(book)
    view = _credit_view() or {}
    known = (ticker in series or ticker in (earn or {})
             or ticker in view or ticker.replace(".", "-") in view)
    # "unknown" is only a fact when the books are loaded. On a freshly
    # restarted instance they are empty for a minute, and this gate told
    # a visitor "Nothing is known about AAPL — check the spelling". An
    # empty library is not evidence about a book.
    books_ready = bool(series) or bool(view)
    if books_ready and not known:
        return jsonify({
            "ok": True, "ticker": ticker, "held": [], "findings": [{
                "level": "warn", "headline": "Nothing is known about this symbol",
                "detail": f"{ticker} is not in the price book, the earnings "
                          f"calendar or the credit book here. Check the "
                          f"spelling; if it is a real listing, it is outside "
                          f"what this tool measures."}],
            "verdict": "warn", "n_findings": 1,
            "bottom_line": f"Nothing is known about {ticker} here — check the spelling.",
            "book_size": len(pretrade._series_of(book)),
            "ms": round((time.time() - _t_start) * 1000),
            "in_todays_scan": False})
    # `complete` is a claim about the US bulk calendar only — this
    # ticker's absence from it is the all-clear only when the calendar
    # actually covers listings like it, never for a non-US symbol just
    # because the (US) calendar happened to load complete.
    complete_for_ticker = complete and screener._region(ticker) == "US"
    held_by_investors = list(
        ((_inv13f_book() or {}).get("tickers") or {}).get(ticker, {})
        .get("holders") or [])
    res = pretrade.check(
        ticker, holdings, book, earn, complete_for_ticker,
        warming=warming or not book,
        single_source=ticker in single_source,
        held_by_investors=held_by_investors,
        risk_eur=(row or {}).get("risk_EUR"),
        reward_eur=((row["risk_EUR"] * row["RR"]) if row and row.get("risk_EUR")
                    and row.get("RR") else None),
        friction_pct=(row or {}).get("friction_pct"))
    # The analysis the reader actually came for: where the price stands,
    # what being wrong costs, how many shares that buys, whether the
    # company can pay its debts, and when it reports — each line carrying
    # the number it came from. The check used to answer four yes/no
    # questions and leave the synthesis to the reader; that is the work
    # they came here to have done.
    try:
        raw = (series.get(ticker) or [])
        rep = _credit_for(ticker) if ticker in view or raw else {}
        v = (_vol_book() or {}).get(ticker) or {}
        res["analysis"] = analysis.build(
            ticker, [c for c in raw if c],
            dates=[d for d, c in zip((book or {}).get("dates") or [], raw) if c],
            vol=v.get("vol") or (rep or {}).get("equity_vol"),
            credit_rep=rep, earnings_days=(earn or {}).get(ticker),
            cal_complete=complete_for_ticker,
            risk_budget=float((row or {}).get("risk_EUR") or 100.0))
    except Exception as e:                                  # noqa: BLE001
        print(f"[check] analysis for {ticker}: {type(e).__name__}: {e}",
              flush=True)

    res["ok"] = True
    res["book_size"] = len(pretrade._series_of(book))
    res["ms"] = round((time.time() - _t_start) * 1000)
    res["in_todays_scan"] = row is not None
    return jsonify(res)


# ---- credit standing, from filings the SEC gives away ----
CREDIT_TTL = float(os.environ.get("CREDIT_TTL", "86400"))   # filings are quarterly
_cik_map = {"ts": 0.0, "data": None}


# The whole-filer document is 3.7MB for Apple and larger for some filers.
# Bounding that download turned out to be harder than it looks:
#
#   requests' `timeout` is a gap between bytes, not a total, so a response
#   that drips steadily never trips it — Boeing held a request thread past
#   90 seconds while a 16-second budget was supposedly in force.
#
#   Reading with iter_content does not fix it either. A fixed chunk size
#   blocks until the buffer FILLS, and chunk_size=None reads to EOF; both
#   sail past any deadline check placed in the loop. Verified against a
#   server that dripped a byte every 20ms: neither form returned.
#
# So the fetch runs in a worker and the CALLER stops waiting. The worker
# is abandoned rather than killed — Python cannot interrupt a socket read
# — but the pool is small and capped, so a run of slow filers degrades
# into fast refusals instead of a queue of stuck request threads.
SEC_MAX_BYTES = 24 * 1024 * 1024
# Daemon threads rather than a ThreadPoolExecutor: the pool registers an
# atexit hook that JOINS its workers, so one abandoned fetch kept the
# whole process from exiting until the socket finally gave up. A
# semaphore caps how many can be in flight, so a run of slow filers
# becomes fast refusals instead of unbounded threads.
_sec_slots = threading.Semaphore(4)


def _sec_fetch(url: str, timeout: float, out: dict):
    import requests as rq
    try:
        with rq.get(url, headers={"User-Agent": screener.SEC_UA},
                    timeout=(min(5.0, timeout), timeout), stream=True) as r:
            if r.status_code != 200:
                raise RuntimeError(f"SEC {r.status_code}")
            buf, size = [], 0
            for chunk in r.iter_content(256 * 1024):
                size += len(chunk)
                if size > SEC_MAX_BYTES:
                    raise RuntimeError("SEC response too large")
                buf.append(chunk)
        out["value"] = json.loads(b"".join(buf).decode("utf-8", "replace"))
    except Exception as e:                      # noqa: BLE001 — reported below
        out["error"] = e
    finally:
        out["done"].set()
        _release(out)


def _release(out: dict) -> None:
    """Give the slot back exactly once, whoever gets there first.

    The slot counts fetches a CALLER is waiting on, so the caller returns
    it the moment it stops waiting rather than leaving it to the orphaned
    thread. That thread's own read timeout equals the caller's budget, so
    the slot was never held indefinitely — this shortens the window, it
    does not close a leak, and it is not the explanation for a burst of
    "the SEC did not answer in time" after a restart. That one is still
    open: the credit warmer and live requests compete for one worker and
    for the SEC's rate limit, and I have not proved which.
    """
    if not out.get("released"):
        out["released"] = True
        _sec_slots.release()


def _sec_json(url: str, timeout: float = 15):
    if not _sec_slots.acquire(timeout=min(2.0, timeout)):
        raise TimeoutError("too many SEC fetches already in flight")
    out = {"done": threading.Event(), "released": False}
    threading.Thread(target=_sec_fetch, args=(url, timeout, out),
                     daemon=True).start()
    if not out["done"].wait(timeout):
        _release(out)
        raise TimeoutError(f"SEC did not answer within {timeout:.0f}s")
    if out.get("error"):
        raise out["error"]
    return out["value"]


# When the SEC is refusing this address, every /credit call still spent
# its whole budget discovering that again — and the page fires one on
# every check, so a dead upstream turned into sixteen seconds of held
# worker thread per click and the whole site went slow. After a run of
# failures the answer is known; give it immediately and re-test rarely.
_sec_health = {"fails": 0, "until": 0.0}
SEC_BREAK_AFTER = 3
SEC_BREAK_S = float(os.environ.get("SEC_BREAK_S", "600"))


def _sec_is_open() -> bool:
    return time.time() >= _sec_health["until"]


def _sec_note(ok: bool) -> None:
    if ok:
        _sec_health.update(fails=0, until=0.0)
        return
    _sec_health["fails"] += 1
    if _sec_health["fails"] >= SEC_BREAK_AFTER:
        _sec_health["until"] = time.time() + SEC_BREAK_S
        _sec_health["fails"] = 0
        print(f"[sec] refusing this host — not asking again for "
              f"{SEC_BREAK_S:.0f}s", flush=True)


class Expired(Exception):
    """The report ran out of time before the filings answered."""


def _sec_within(budget_s: float):
    """A fetcher that answers within a total budget, not per request.

    One credit report can make eight sequential SEC calls — five balance
    sheet tags, the whole-filer fallback, and two share-count tags. A
    15-second timeout on each of them is a two-minute timeout on the
    report, which is what it turned out to be in production: Carnival
    held a worker thread for 120 seconds and returned nothing, twice.
    The caller needs a bound on the answer, not on each step towards it.

    Expiry is also recorded on the returned function, because both callers
    in credit.py catch per-tag failures and carry on — so a report that ran
    out of time would otherwise be indistinguishable from a company that
    files nothing, and would be reported as "missing balance sheet" when
    the balance sheet is right there.
    """
    end = time.time() + budget_s

    def get_json(url):
        left = end - time.time()
        if left <= 1:
            get_json.expired = True
            raise Expired(url)
        try:
            return _sec_json(url, timeout=min(8.0, left))
        except TimeoutError:
            # the fetch itself ran out of time, which is the same fact as
            # the budget running out and must be reported the same way —
            # not as "this company files no balance sheet"
            get_json.expired = True
            raise
    get_json.expired = False
    return get_json


def _cik_for(ticker: str, timeout: float = 8.0) -> int | None:
    """Ticker -> CIK, from the same SEC file the universe already uses.

    The timeout is an argument because this sits INSIDE a credit report's
    time budget: at a fixed 15 seconds it could spend longer than the
    whole report was allowed before the report's own clock started.
    """
    if _cik_map["data"] is None or time.time() - _cik_map["ts"] > 7 * 86400:
        try:
            d = _sec_json(screener.SEC_TICKERS_URL, timeout=timeout)
            fields = [f.lower() for f in d.get("fields", [])]
            ti, ci = fields.index("ticker"), fields.index("cik")
            _cik_map.update(
                data={row[ti].upper(): int(row[ci]) for row in d.get("data", [])},
                ts=time.time())
        except Exception:
            # Do NOT stamp ts on failure. Doing so cached an empty map for
            # the full seven days, and every credit report then answered
            # "not a US filer" — for Apple — until the process restarted.
            # A miss must be retried, not frozen.
            if _cik_map["data"]:
                _cik_map.update(ts=time.time())
    t = ticker.upper()
    m = _cik_map["data"] or {}
    # BRK.B / BRK-B are one company; the SEC file uses the dash form
    return m.get(t) or m.get(t.replace(".", "-"))


def _company_identity(cik: int, timeout: float = 10.0) -> dict:
    """SIC code, sector name and legal name, streamed from the head of the
    submissions file and abandoned after 64KB — same contract as the
    runner's copy. All three sit in the first few hundred bytes of any
    real filer's submissions JSON, so this costs no more than the SIC
    lookup already did. Any field not found is None; an unknown SIC is
    NOT treated as financial."""
    import requests as rq
    empty = {"sic": None, "sic_desc": None, "name": None}
    try:
        r = rq.get(f"https://data.sec.gov/submissions/CIK{int(cik):010d}.json",
                   headers={"User-Agent": screener.SEC_UA},
                   timeout=timeout, stream=True)
        if r.status_code != 200:
            r.close()
            return dict(empty)
        buf = b""
        for chunk in r.iter_content(8192):
            buf += chunk
            if b'"sicDescription"' in buf or len(buf) > 65536:
                break
        r.close()
    except Exception:
        return dict(empty)
    return credit.parse_submissions_identity(buf)


def _with_peers(rep: dict) -> dict:
    """Rank a distance against every other measured name on today's board
    — the whole book, and again within its own sector when enough of the
    board shares one (see credit.peer_standings).

    Computed on read rather than stored with the report, because the peer
    set grows as the board is measured: the first company measured has no
    peers at all, and a ranking cached at that moment would be served as
    "unavailable" for the next 24 hours to everyone who asked. It costs
    nothing to redo — these are cache reads, not network calls.
    """
    if rep.get("dd") is None:
        return rep
    me = (rep.get("ticker") or "").upper()
    published = _credit_view() or {}
    if len(published) >= 5:
        book = {t2.upper(): r for t2, r in published.items()}
        book[me] = rep
        stand = credit.peer_standings(book)
        return dict(rep, **stand.get(me, {}))
    book = {me: rep}
    for other in (_state.get("results") or []):
        t2 = (other.get("ticker") or "").upper()
        if not t2 or t2 == me:
            continue
        hit, c = cache_store.fetch(f"credit:{t2}", CREDIT_TTL)
        if hit and isinstance(c, dict) and c.get("dd") is not None:
            book[t2] = c
    stand = credit.peer_standings(book)
    return dict(rep, **stand.get(me, {}))


def _credit_for(ticker: str, budget_s: float = 16.0) -> dict:
    """One company's Distance to Default, cached, or an explicit refusal.

    Shared by the endpoint and the warmer below. It has to be shared: the
    peer ranking is computed from OTHER names' cached reports, so if only
    the endpoint could populate the cache, the ranking would require five
    strangers to have looked up five other companies first and would be
    absent almost every time it was asked for.
    """
    # Published first: computed on a runner the SEC will actually talk to,
    # so this path makes no outbound call and cannot time out.
    view = _credit_view() or {}
    # Brokers print BRK.B; the SEC's ticker file and the published book
    # say BRK-B. The dot form used to fall through every lookup and come
    # back "Not a US filer" — a false statement about the most famous
    # filer in the country — while the dash form got the honest sector
    # refusal. One company gets one answer.
    if ticker not in view and "." in ticker:
        alias = ticker.replace(".", "-")
        if alias in view or _cik_for(alias, timeout=0.1) is not None:
            ticker = alias
    pub = view.get(ticker)
    if pub and pub.get("dd") is not None:
        # ranked here rather than trusting the ranking the scan stored, so
        # a name added to the book later is ranked against the current set
        return _with_peers(dict(pub, ok=True, cached=True, from_scan=True))
    if pub and (pub.get("not_modelled") or pub.get("unmeasurable")):
        # A refusal published by the runner — a bank, an insurer, or a
        # listing whose quote does not trade. The refusal IS the answer;
        # falling through to a live fetch would replace "this cannot be
        # measured honestly, and here is why" with "the SEC is not
        # answering", which is about the wrong subject.
        return dict(pub, ok=True, cached=True, from_scan=True)

    key = f"credit:{ticker}"
    hit, cached = cache_store.fetch(key, CREDIT_TTL)
    if hit and isinstance(cached, dict):
        cached["cached"] = True
        return _with_peers(cached)

    cik = _cik_for(ticker, timeout=min(8.0, budget_s / 2))
    if cik is None and not (_cik_map["data"] or {}):
        _sec_note(False)
    if cik is None:
        # "the list does not contain this ticker" and "the list could not
        # be read" are different facts, and only one of them is about the
        # company. Coca-Cola was being reported as not a US filer because
        # the SEC was refusing this address that minute.
        if not (_cik_map["data"] or {}):
            return {"ok": True, "ticker": ticker, "dd": None,
                    "verdict": "The SEC's company list could not be read just "
                               "now, so this could not be looked up at all. "
                               "Nothing here is a statement about the company.",
                    "missing": ["the SEC company list"]}
        return {"ok": True, "ticker": ticker, "dd": None,
                "verdict": "Not a US filer — SEC XBRL covers US listings "
                           "only, so this company's filings are not "
                           "available here.",
                "missing": ["SEC filings"]}
    if not _sec_is_open():
        return {"ok": True, "ticker": ticker, "dd": None,
                "missing": ["SEC filings"],
                "verdict": "The SEC is not answering this server at the moment, "
                           "so companies outside today's published board cannot "
                           "be measured. Nothing here is about the company."}
    # The sector gate, on THIS path too. The runner refuses financials
    # before publishing, but a financial outside the published book fell
    # through to here and was fully modelled — Cigna's $55bn of claims
    # payable read as debt coming due, with a band and a percentile. The
    # exact defect the guard exists for, resurrected by the fallback.
    identity = _company_identity(cik)
    sic = identity["sic"]
    if credit.is_financial(sic):
        rep = {"ok": True, "ticker": ticker, "dd": None, "band": None,
               "sic": sic, "sic_desc": identity["sic_desc"],
               "name": identity["name"], "not_modelled": True,
               "verdict": credit.NOT_MODELLED}
        cache_store.put(key, rep)
        return rep
    sec = _sec_within(budget_s)
    try:
        bs = credit.fetch_balance_sheet(cik, sec)
    except Expired:
        return {"ok": True, "ticker": ticker, "dd": None,
                "verdict": "The SEC did not answer in time. Nothing is wrong "
                           "with the company — try again in a moment.",
                "missing": ["SEC filings"]}
    except Exception as e:
        return {"ok": True, "ticker": ticker, "dd": None,
                "verdict": f"Could not read the filings ({type(e).__name__}).",
                "missing": ["SEC filings"]}

    row = next((r for r in (_state.get("results") or [])
                if (r.get("ticker") or "").upper() == ticker), None)
    book = pretrade._series_of(_price_book())
    # the published series carry nulls on days the stock did not trade, so
    # the closes handed to the model must have them stripped
    closes = ((row or {}).get("spark")
              or [c for c in (book.get(ticker) or []) if c])
    mktcap = (row or {}).get("mktcap_b")
    equity = float(mktcap) * 1e9 if mktcap else None
    shares = {}
    if equity is None and closes:
        # shares x last close, both free — without this the report only
        # covers whatever is on today's board, which is not a product.
        # A refused share count leaves equity None and the report says so;
        # it does not fall back to a stale register.
        try:
            shares = credit.shares_outstanding(cik, sec)
            if shares.get("shares"):
                equity = shares["shares"] * float(closes[-1])
        except Exception:
            shares = {}          # including Expired: the report says what it lacks

    # a volatility measured over years beats one measured over the 60-day
    # window the price book can afford to carry; fall back to the window
    v = (_vol_book() or {}).get(ticker) or {}
    rep = credit.report(ticker, equity, closes, bs["current_liabilities"],
                        bs["total_liabilities"], as_of=bs.get("as_of"),
                        vol=v.get("vol"), vol_obs=v.get("obs"),
                        currency_refused=bs.get("currency_only"))
    rep["ok"] = True
    rep["source"] = bs.get("source")
    rep["endpoint"] = bs.get("source_endpoint")
    rep["shares_as_of"] = shares.get("as_of")
    rep["shares_tag"] = shares.get("tag")
    rep["sic"] = sic
    rep["sic_desc"] = identity["sic_desc"]
    rep["name"] = identity["name"]
    # credit.history() needs a share count to re-solve the model at each
    # past close, and this report's own equity already IS shares x price —
    # dividing it back out recovers the count exactly when equity came
    # from shares_outstanding(), and derives a reasonable implied count
    # (consistent with today's own headline dd) when it came from today's
    # market cap instead. Either way beats the gap this closes: every
    # ticker not already in the published book got a live report with no
    # chart at all, because history() silently returns None the instant
    # shares is None.
    rep["shares"] = (equity / float(closes[-1])) if (equity and closes) else None
    _sec_note(rep.get("dd") is not None or not sec.expired)
    # a report that ran out of time is not a report about a company that
    # files nothing, and must not be worded as one — or cached as one
    if rep.get("dd") is None and sec.expired:
        rep["timed_out"] = True
        rep["verdict"] = ("The SEC did not answer in time, so this is not "
                          "measured yet. Nothing is wrong with the company — "
                          "try again in a moment.")
    elif shares.get("stale_as_of") and equity is None:
        rep["verdict"] = (
            "This company last reported a share count on "
            f"{shares['stale_as_of']}, which is too old to price it today, "
            "so its market value cannot be established from filings alone.")

    if rep.get("dd") is not None:
        cache_store.put(key, rep)
    return _with_peers(rep)


@app.route("/credit/<ticker>")
def credit_page(ticker: str):
    """The full report for one company.

    The paid version of this is a document: the metric, how it moved, what
    is driving it, where the company sits against its peers, and what the
    figures were read from. A one-line verdict inside another feature is
    not that, and the ask was for the report.

    Everything here comes from what the scan already published, so the
    page costs no outbound call.
    """
    t = (ticker or "").strip().upper()
    rep = _credit_for(t) if t else {}
    # Dates and closes are paired BEFORE any filtering. The published
    # calendar is the union of US and EU sessions, so a US ticker carries
    # None on EU-only dates — and filtering the closes while slicing the
    # dates by length made the caption claim "56 trading days to
    # 2026-08-12" for a ticker whose last real close was the 11th, with
    # every intermediate date shifted by the market holidays in between.
    raw = (pretrade._series_of(_price_book()) or {}).get(t) or []
    all_dates = (_price_book() or {}).get("dates") or []
    if len(all_dates) < len(raw):
        all_dates = [None] * (len(raw) - len(all_dates)) + list(all_dates)
    pairs = [(d, c) for d, c in zip(all_dates, raw) if c]
    closes = [c for _, c in pairs]
    hist, hdates = None, []
    horizons = None
    if rep.get("dd") is not None:
        full = credit.history(closes, rep.get("shares"),
                              rep.get("default_point"), rep.get("equity_vol"))
        if full:
            pts = [(d, h) for (d, _), h in zip(pairs, full) if h is not None]
            hist = [h for _, h in pts] or None
            hdates = [d for d, _ in pts if d]
        # Different question from the chart above: that one holds the
        # balance sheet fixed and moves the price through past dates.
        # This holds TODAY's price and balance sheet fixed and moves the
        # horizon instead — one extra solve per horizon, no network call,
        # cheap enough to do for a single page view; not precomputed into
        # the published book, since restate() would then have to redo it
        # for the whole ~800-name universe on every read for a card
        # almost nobody scrolls to.
        if rep.get("equity") and rep.get("default_point") and rep.get("equity_vol"):
            horizons = credit.horizon_view(rep["equity"], rep["default_point"],
                                           rep["equity_vol"])
    # nearest neighbours, so "65th percentile" has faces attached to it —
    # from the SAME pool the percentile prose was computed against
    # (_with_peers already decided sector vs. whole-book for this exact
    # ticker), so the two parts of the page never describe different sets.
    # n_measured stays the WHOLE book's count regardless — it is quoted
    # against "the other companies" figure near the top of the page, and
    # narrowing it to a sector subset here would make that count lie.
    whole_book = _credit_view() or {}
    n_book = sum(1 for r2 in whole_book.values()
                 if isinstance(r2, dict) and r2.get("dd") is not None)
    book = whole_book
    if rep.get("sector_level") in ("sic", "sic_major"):
        book = credit.filter_sector(book, rep.get("sic"), level=rep["sector_level"])
    peers = sorted(((r.get("dd"), k) for k, r in book.items()
                    if r.get("dd") is not None and k != t))
    near = []
    if rep.get("dd") is not None:
        # the company itself belongs in its own comparison — a ranking with
        # no anchor is a list of strangers
        rows = sorted(peers + [(rep["dd"], t)])
        i = next(j for j, (_, k) in enumerate(rows) if k == t)
        near = [{"ticker": k, "dd": d, "band": credit.band(d)}
                for d, k in rows[max(0, i - 2):i + 3]]
    return render_template("credit.html", r=rep, t=t, hist=hist, near=near,
                           dates=hdates, horizons=horizons,
                           # the filing date and the price date are different
                           # facts and the report has to carry both — and the
                           # price date is THIS ticker's last real close, not
                           # the calendar's last entry, which can be a session
                           # on another exchange
                           priced_on=(pairs[-1][0] if pairs else None),
                           # the same set the front page counts — a
                           # live-measured extra made this page say 180
                           # while the directory said 179, in one minute
                           n_measured=n_book)


@app.route("/credit", methods=["POST"])
def credit_report():
    """How far a company is from not being able to pay its debts.

    The paid version of this report maps the model onto an empirical
    default frequency using a proprietary default database. That step is
    absent here and no probability is emitted in its place — see
    credit.py. What is returned is the distance itself, its drivers, and
    where it sits among the other names on today's board, which is the
    number a reader acts on and the one that needs no calibration.
    """
    body = request.get_json(silent=True) or {}
    ticker = str(body.get("ticker") or "").strip().upper()
    if not ticker:
        return jsonify({"ok": False, "error": "no ticker given"}), 400
    try:
        return jsonify(_credit_for(ticker))
    except Exception as e:                      # noqa: BLE001
        # a 500 here is a blank space on the card where a credit standing
        # should be, which reads as "nothing to worry about"
        print(f"[credit] {ticker}: {type(e).__name__}: {e}", flush=True)
        return jsonify({"ok": True, "ticker": ticker, "dd": None,
                        "missing": ["the credit model"],
                        "verdict": "This company's credit standing could not "
                                   "be worked out just now."})


@app.route("/favicon.ico")
def favicon():
    """A console error on every page load is a defect like any other."""
    svg = ('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 32 32">'
           '<rect width="32" height="32" rx="7" fill="#2a78d6"/>'
           '<path d="M7 21l6-7 4 4 8-9" stroke="#fff" stroke-width="3"'
           ' fill="none" stroke-linecap="round" stroke-linejoin="round"/></svg>')
    return Response(svg, mimetype="image/svg+xml",
                    headers={"Cache-Control": "public, max-age=604800"})


_today_memo = {"key": None, "res": None}


def _today_candidates(horizon: int) -> list:
    """Every liquid name the instance knows about, ready to be ranked.

    Built from the books already in memory — prices, volatility, credit,
    the earnings calendar — so this costs no outbound call. Deliberately
    NOT built from the pattern screen's output: that screen's entry rule
    is the one this project falsified, and ranking its survivors would
    smuggle a dead signal back in through the candidate list.
    """
    series = pretrade._series_of(_price_book()) or {}
    vols = _vol_book() or {}
    creds = _credit_view() or {}
    liq = _liq_book() or {}
    earn, cal_complete, earn_single_source = _published_earnings()
    inv13f_tickers = (_inv13f_book() or {}).get("tickers") or {}
    out = []
    for t, closes in series.items():
        c = [x for x in (closes or []) if x]
        if len(c) < 20:
            continue
        rep = creds.get(t) or {}
        px = c[-1]
        # `equity` in a credit report is shares x price — the company's
        # market value, restated against the latest close. Kept as the
        # fallback liquidity proxy for names liquidity.json has no figure
        # for (FX could not be established, or the name never traded in
        # the window) — ranking.filters() flags that fallback, never
        # treats it as a measurement of the same thing.
        mv = rep.get("equity")
        # vol.json holds {vol, obs, as_of} per name, not a bare float —
        # and a thin estimate is worse than none, because it sets both the
        # stop and the share count
        v = vols.get(t) or {}
        av = v.get("vol") if isinstance(v, dict) else v
        if isinstance(v, dict) and (v.get("obs") or 0) < 60:
            av = None
        adv = (liq.get(t) or {}).get("adv_usd")
        out.append({
            "ticker": t,
            "name": rep.get("name") or t,
            "price": px,
            "market_value": mv if (mv and mv > 0) else None,
            "adv_usd": adv if (adv and adv > 0) else None,
            "annual_vol": av,
            "dd": rep.get("dd"),
            "is_financial": bool(rep.get("missing") == "financial"
                                 or rep.get("not_modelled") == "financial"),
            "days_to_earnings": (earn or {}).get(t),
            # absence-from-calendar is only the all-clear for a name the
            # (complete) US bulk calendar actually covers — an EU name's
            # absence proves nothing, however complete that calendar is
            "cal_covered": bool(cal_complete) and screener._region(t) == "US",
            "earnings_single_source": t in earn_single_source,
            "sector": rep.get("sector"),
            # Purely informational — never read by ranking.py's filters()
            # or score(). A ticker only lands here via the `tickers` dict's
            # own ticker key (never its "(unmapped) issuer" fallback), so a
            # hit here is always a confirmed CUSIP-to-symbol match, never a
            # guess. See superinvestors.py / KNOWN_ISSUES.md for what this
            # is and is not (long-only, up to 135 days stale, no signal).
            "held_by_investors": list((inv13f_tickers.get(t) or {}).get("holders") or []),
        })
    return out


@app.route("/")
@app.route("/today")
def today_page():
    """The five names, with the plan for each — the point of the whole site.

    Everything else here measures. This decides. It is the page a reader
    opens in the morning, acts on in two minutes, and closes.

    What it will not do is imply a forecast. There is no price target and
    no buy rating, because the one entry signal this project tested
    against a null lost to a coin flip twice. What is on offer is a
    shortlist that will not blow up, sized so that being wrong costs a
    known amount — which is the part that actually compounds.
    """
    horizon = ranking.DEFAULT_HORIZON
    try:
        budget = float(request.args.get("risk") or 100.0)
    except (TypeError, ValueError):
        budget = 100.0
    budget = min(max(budget, 1.0), 1e7)

    # Scoring reads every name in the book and ranks it. That is cheap
    # once and wasteful on every request, and this is the front page on a
    # free instance that also has to serve everything else. The inputs
    # only change when a book is refreshed, so key on exactly that.
    earn, cal_complete, _earn_single_source = _published_earnings()
    regime_notes = _regime_notes(_regime_book())
    key = (_book["ts"], _vols["ts"], _creds["ts"], _earn_pub["ts"],
           _patterns_pub["ts"], round(budget, 2), horizon, cal_complete)
    if _today_memo["key"] == key:
        res = _today_memo["res"]
    else:
        res = ranking.score(_today_candidates(horizon), holdings=[],
                            patterns_report=_patterns_book(),
                            risk_budget=budget, horizon=horizon,
                            corr_by_ticker=None)
        _today_memo.update(key=key, res=res)

    # A stop does not protect you across a report — the price gaps
    # straight through it. That is the whole reason the earnings filter
    # exists, and when the calendar cannot be read it excludes nothing:
    # every name comes back "date unknown", gets a flag, and sails
    # through. Five confident-looking plans with the one check that
    # matters silently switched off is precisely the failure this project
    # keeps having, so the list is withheld instead.
    credit_index = brief.credit_directory(_credit_view())
    if not earn:
        return render_template("today.html", picks=[], res=res, budget=budget,
                               horizon=horizon, cost=ranking.ROUND_TRIP_COST_PCT,
                               blocked="the earnings calendar could not be read",
                               credit_index=credit_index, regime_notes=regime_notes)

    picks = []
    for row in res["ranked"][:5]:
        # row already carries its own cal_covered/earnings_single_source
        # from _today_candidates() — per-candidate, not the global flag
        p = plan.trade_plan(row, risk_budget=budget, horizon=horizon)
        if not p.get("usable"):
            continue
        p["thesis"] = plan.thesis(row, horizon, rank=len(picks))
        p["score"] = row["score"]
        p["components"] = row["components"]
        p["component_max"] = row.get("component_max") or {}
        # informational only — never read by ranking.py, see the field's
        # own comment in _today_candidates()
        p["held_by_investors"] = row.get("held_by_investors") or []
        picks.append(p)
    return render_template("today.html", picks=picks, res=res,
                           budget=budget, horizon=horizon,
                           cost=ranking.ROUND_TRIP_COST_PCT,
                           credit_index=credit_index, regime_notes=regime_notes)


@app.route("/patterns")
def patterns_page():
    """Which price shapes were tested, and which of them survived.

    This exists because the honest answer to "does this shape work" is
    almost always no, and nowhere on the internet shows you the no. Every
    site that tests patterns publishes the winners; the losers are what
    tell you whether the winners mean anything. If eleven shapes were
    tried and one came out at p = 0.04, that is not a discovery, it is
    arithmetic — and a reader can only see that if the other ten are on
    the page.

    The measurement runs weekly on a runner, because it needs a year of
    daily bars for hundreds of names. This page only reads the result.
    """
    d = _patterns_book() or {}
    rows = []
    for h, res in sorted((d.get("horizons") or {}).items(),
                         key=lambda kv: int(kv[0])):
        for name, r in (res or {}).items():
            if not r:
                rows.append({"pattern": name, "horizon": int(h),
                             "measured": False})
                continue
            rows.append({"pattern": name, "horizon": int(h), "measured": True,
                         "n": r.get("n"), "days": r.get("days"),
                         "edge_pct": r.get("edge_pct"),
                         "after_costs_pct": r.get("after_costs_pct"),
                         "hit_rate_pct": r.get("hit_rate_pct"),
                         "p": r.get("p"),
                         "p_text": patterns.p_words(r.get("p")),
                         "family_size": r.get("family_size"),
                         "survives": bool(r.get("survives")),
                         # confirmed = it also held up on the half of the
                         # history it was NOT chosen on. Surviving the
                         # search alone never earns the badge.
                         "confirmed": bool(r.get("confirmed")),
                         "holdout": r.get("holdout"),
                         "holdout_note": r.get("holdout_note"),
                         "tradeable": bool(r.get("survives")
                                           and r.get("confirmed")
                                           and (r.get("after_costs_pct") or 0) > 0),
                         "verdict": patterns.verdict(r)})
    # strongest first, so the page is read top-down and the reader does
    # not have to hunt for whether anything worked
    rows.sort(key=lambda r: (not r.get("tradeable"), not r.get("survives"),
                             r.get("p") if r.get("p") is not None else 2))
    measured = [r for r in rows if r.get("measured")]
    return render_template("patterns.html", d=d, rows=rows,
                           measured=measured,
                           unmeasured=len(rows) - len(measured),
                           # the family every result was corrected against —
                           # taken from the results rather than counted here,
                           # so the page cannot claim a correction the
                           # measurement did not actually apply
                           family=(measured[0].get("family_size")
                                   if measured else None),
                           tradeable=[r for r in rows if r.get("tradeable")],
                           cost=patterns.ROUND_TRIP_COST_PCT,
                           min_days=patterns.MIN_DAYS,
                           source=_published_reads.get("patterns.json"))


@app.route("/investors")
def investors_page():
    """Who among a curated set of managers holds what, read straight from
    SEC 13F filings — a fact with a date on it, never a signal.

    The idea of tracking a small roster of well-known managers is
    borrowed, with attribution, from Dataroma; every filing and every
    number on this page comes from SEC EDGAR directly, published weekly
    by superinvestors.py/thirteenf_scan.py (see docs/superinvestors-plan.md).

    This adds zero score points anywhere on the site. A 13F is long-only,
    US-equity-only, and up to 135 days stale by the time the next
    quarter's filings replace it — none of which a ranking should ever
    treat as current, directional information. See KNOWN_ISSUES.md.
    """
    d = _inv13f_book() or {}
    if not d.get("managers"):
        return render_template("investors.html", loaded=False, d=d)
    managers = sorted(d.get("managers") or [], key=lambda m: m["name"])
    rows = []
    for key, row in (d.get("tickers") or {}).items():
        ticker = row.get("ticker")
        rows.append({
            "ticker": ticker, "display": ticker or row.get("issuer") or key,
            "issuer": row.get("issuer"),
            "holders": len(row.get("holders") or []),
            "holder_names": sorted(row.get("holders") or []),
            "added": len(row.get("added") or []),
            "increased": len(row.get("increased") or []),
            "trimmed": len(row.get("trimmed") or []),
            "exited": len(row.get("exited") or []),
            "mapped": bool(ticker)})
    rows.sort(key=lambda r: (-r["holders"], r["issuer"] or ""))
    # A ticker one manager holds is not consensus among anything — the
    # roll-up table only earns its place showing names at least two
    # tracked managers independently hold; the total count above it says
    # plainly how many names that leaves out.
    multi = [r for r in rows if r["holders"] >= 2]
    return render_template(
        "investors.html", loaded=True, d=d, managers=managers,
        rows=multi[:200], n_shown=min(len(multi), 200),
        n_multi=len(multi), n_total_tickers=len(rows),
        n_unmapped=sum(1 for r in rows if not r["mapped"]),
        source=_published_reads.get("investors13f.json"))


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
    return Response(text, mimetype="text/markdown")


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
    # Render's shallow clone strips parent links, so --no-merges cannot
    # recognise a merge commit — the page opened with "Merge branch
    # 'claude/…'", an internal label recording nothing a visitor can
    # use. Filtering by subject fixed that and then emptied the page
    # entirely, because a shallow clone can hold nothing BUT merges. So:
    # filter, and fall back to the raw history rather than to nothing.
    rows = []
    for row in out.stdout.splitlines():
        date, _, subject = row.partition("\t")
        rows.append((date, subject))
    useful = [(d, s2) for d, s2 in rows
              if not s2.startswith(("Merge ", "Published scan ",
                                    "Scheduled scan "))]
    for date, subject in (useful or rows):
        lines.append(f"- **{date}** — {subject}")
    if not useful:
        lines.append("")
        lines.append("_This build carries only a shallow slice of history, so "
                     "the entries above are merge points rather than changes; "
                     "the repository holds the full record._")
    return Response("\n".join(lines) + "\n", mimetype="text/markdown")


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


def _calendar_refresher():
    """Rebuild the earnings calendar before its cache expires.

    The warmer below ran ONCE at boot while the cached calendar expires
    after six hours, and nothing else rebuilds it — scans run in CI, not
    here. So any instance alive longer than six hours answered every
    pre-trade check with "the calendar is still loading, try again in a
    minute", permanently, and the message told the reader to wait for
    something that was never coming.
    """
    while True:
        time.sleep(max(600.0, screener.EARN_CAL_TTL * 0.75))
        try:
            cal, ok = screener._earnings_calendar()
            print(f"[warm] earnings calendar refreshed: {len(cal)} companies, "
                  f"{'complete' if ok else 'INCOMPLETE'}", flush=True)
        except Exception as e:
            print(f"[warm] earnings calendar refresh failed: {e}", flush=True)


def _warm_books():
    """The three published files the page cannot render without.

    Deliberately NOT in the same thread as the earnings calendar. The
    calendar is ~32 sequential Nasdaq requests at 15 seconds each, so when
    Nasdaq is slow it can hold that thread for eight minutes — and it ran
    FIRST, so the price book, the volatility book and the credit
    standings all waited behind it. A reader in that window got a board
    with no credit column, no overlap measurement and no explanation,
    while all three files sat one HTTP GET away.

    Each of these is a single fetch. They have no business queueing behind
    anything.
    """
    for label, fn in (("price book", _price_book), ("volatility book", _vol_book),
                      ("credit book", _credit_book),
                      ("earnings calendar (published)", _earnings_book),
                      ("pattern sweep", _patterns_book),
                      ("market regime", _regime_book),
                      ("liquidity book", _liq_book),
                      ("13F book", _inv13f_book)):
        try:
            fn(fetch=True)
        except Exception as e:
            print(f"[warm] {label} failed: {e}", flush=True)


def _warm_calendar():
    """The earnings calendar, on its own thread because it is the slow one."""
    try:
        cal, ok = screener._earnings_calendar()
        print(f"[warm] earnings calendar: {len(cal)} companies, "
              f"{'complete' if ok else 'INCOMPLETE'}", flush=True)
    except Exception as e:
        print(f"[warm] earnings calendar failed: {e}", flush=True)


def _warm_credit():
    """Compute today's board's credit reports so the ranking has peers.

    The peer percentile is the one number here that a proprietary model
    has no advantage over — a ranking is invariant to whatever mapping
    turns distances into probabilities — and it needs at least five other
    measured names to exist. Left to the endpoint alone it would almost
    never appear, because it would depend on five other people having
    looked up five other companies first.

    So the board is measured up front, in the background, once a day. It
    is ~25 companies at a couple of seconds each against filings that
    change quarterly, and no request ever waits for it.
    """
    # the board arrives from stored results or a published scan, either of
    # which can land after this thread starts, so wait for it rather than
    # giving up on an empty list and leaving the ranking permanently absent
    board = []
    for _ in range(60):
        board = [(r.get("ticker") or "").upper()
                 for r in (_state.get("results") or [])]
        if board:
            break
        time.sleep(5)
    if not board:
        print("[warm] credit: no board to measure", flush=True)
        return
    t0, done, misses = time.time(), 0, 0
    for t in board:
        try:
            if _credit_for(t, budget_s=30).get("dd") is not None:
                done += 1
                misses = 0
            else:
                misses += 1
        except Exception:
            misses += 1
        # The SEC rate-limits by IP and this instance shares one with
        # everything else the box does. Walking the whole board while it is
        # refusing turns a temporary block into a sustained one, and every
        # live /credit call competes with it — a reader gets "the SEC did
        # not answer in time" for every company while the warmer is busy
        # earning that refusal. Stop and let the next boot try.
        if misses >= 3:
            print(f"[warm] credit: the SEC refused {misses} in a row after "
                  f"{done} — stopping rather than pressing", flush=True)
            return
        time.sleep(2.0)          # SEC asks for 10/second; this is one per two
    print(f"[warm] credit: {done}/{len(board)} of the board measured in "
          f"{time.time() - t0:.0f}s", flush=True)


_WARMERS = (("warm-books", lambda: _warm_books()),
            ("warm-calendar", lambda: _warm_calendar()),
            ("books-refresh", lambda: _book_refresher()),
            ("calendar-refresh", lambda: _calendar_refresher()),
            ("results-poll", lambda: _results_refresher()),
            ("crumb-hunter", lambda: _crumb_hunter()))
_warm_lock = threading.Lock()
_warm_done: set = set()          # returned normally; nothing left to do
_warm_failed: dict = {}          # name -> when it last raised
WARM_RETRY_S = 60.0


def _ensure_warm() -> list:
    """Start any warmer that is neither running nor finished, and say which.

    Starting these once at import is not enough, and the live instance
    proved it: /published reported MainThread and a gunicorn worker
    thread and nothing else, while the books those threads fill sat
    empty and every credit report on the site read "not measured".

    Two ways that happens and neither is hypothetical. A warmer that
    raises above its own try block exits, and takes its book with it for
    the life of the process — silently, because a dead thread makes no
    noise. And a server started with --preload imports this module in the
    master, starts these threads there, then forks: the worker inherits
    the module's data and none of its threads. Which of the two it was
    does not change the fix.

    Finished is not the same as dead. Two of these warm once and return;
    the rest loop forever. Rather than label each one, a warmer that
    returns normally is recorded as done and never restarted, and one
    that raises is not — which gets both cases right without the list and
    the labels having to agree.
    """
    if os.environ.get("SKIP_WARM"):
        return []
    started, now = [], time.time()
    with _warm_lock:
        alive = {t.name for t in threading.enumerate() if t.is_alive()}
        for name, fn in _WARMERS:
            if name in alive or name in _warm_done:
                continue
            # one that fails instantly must not be restarted by every
            # request in a loop for as long as the failure lasts. Gated on
            # having FAILED, not on having been tried, so a thread that
            # was never started here — the fork case — starts at once.
            if now - _warm_failed.get(name, 0.0) < WARM_RETRY_S:
                continue

            def _guarded(fn=fn, name=name):
                try:
                    fn()
                except BaseException as e:                  # noqa: BLE001
                    # A thread dying quietly is how a book stays empty
                    # forever. Say so; a later request restarts it.
                    _warm_failed[name] = time.time()
                    print(f"[warm] {name} died: {type(e).__name__}: {e}",
                          flush=True)
                else:
                    _warm_done.add(name)

            threading.Thread(target=_guarded, name=name, daemon=True).start()
            started.append(name)
    if started:
        print(f"[warm] started {', '.join(started)}", flush=True)
    return started


def _boot_books() -> None:
    """Fill the books from the copies shipped with this build, instantly.

    The books were populated only by the background warmers, which fetch
    over the network — so for the first minute after every deploy, and
    after every free-tier spin-down, /published reported
    {credit: 0, prices: 0, vol: 0} and the site answered real questions
    with "the SEC's company list could not be read". The data was on the
    instance's own disk the whole time.

    This is a local file read of a few hundred kilobytes: fast enough for
    import, and it makes a cold instance answer immediately with data
    that is at worst a few hours old. The warmers still run and still
    replace it with the live branch; this only removes the window where
    the site knows nothing.
    """
    if os.environ.get("SKIP_WARM"):
        return
    for name, store in (("prices.json", _book), ("vol.json", _vols),
                        ("credit.json", _creds),
                        ("patterns.json", _patterns_pub)):
        try:
            path = os.path.join(PUBLISHED_DIR, name)
            if not os.path.exists(path):
                continue
            with open(path) as f:
                data = json.load(f)
            if data:
                store.update(data=data, ts=0.0)   # ts=0: due for refresh
                _published_reads[name] = "shipped copy (boot)"
        except Exception as e:                                # noqa: BLE001
            print(f"[boot] {name}: {type(e).__name__}: {e}", flush=True)
    print(f"[boot] books from shipped copies: "
          f"{len(pretrade._series_of(_book['data'] or {}))} prices, "
          f"{len(_vols['data'] or {})} vol, {len(_creds['data'] or {})} credit",
          flush=True)


_boot_books()

# The test suite is hermetic — it imports this module and must not reach
# Nasdaq, the SEC or GitHub to do it.
_ensure_warm()


@app.before_request
def _warm_guard():
    """Every request checks the warmers are alive. This is the only thing
    standing between one dead thread and a page with no data on it."""
    try:
        _ensure_warm()
    except Exception as e:                                  # noqa: BLE001
        print(f"[warm] guard failed: {type(e).__name__}: {e}", flush=True)


# A scan that has said nothing for this long is not working, whatever its
# status field claims. Reporting that beats spinning silently forever.
STALL_AFTER_S = float(os.environ.get("STALL_AFTER_S", "300"))


# The scan publishes every two hours while US markets are open, on
# weekdays. When it stops — an exhausted Actions quota, a broken job — the
# site keeps serving the last thing it published, which is correct. What
# was NOT correct was saying nothing about why, or telling the reader to
# "press Run" for a button that was removed months ago.
SCAN_EVERY_H = float(os.environ.get("SCAN_EVERY_H", "2"))
SCAN_WINDOW = "13:00-21:00 UTC on weekdays"


def _publishing_state(results_ts: float, fresh: dict | None) -> dict:
    """Is the scheduled scan still publishing, and if not, say so.

    Overdue means BOTH that more time has passed than the schedule allows
    AND that a trading session has happened since — otherwise a weekend
    would read as a fault, which it is not.
    """
    age_h = max(0.0, (time.time() - results_ts) / 3600.0)
    sessions = (fresh or {}).get("sessions")
    traded_since = bool(sessions) and sessions >= 1
    overdue = age_h > SCAN_EVERY_H * 1.5 and traded_since
    return {"every_h": SCAN_EVERY_H, "window": SCAN_WINDOW,
            "age_h": round(age_h, 1), "overdue": overdue,
            "note": (f"The scheduled scan publishes every {SCAN_EVERY_H:g} hours "
                     f"({SCAN_WINDOW}) and has not published for "
                     f"{age_h:.1f} hours. The figures below are the last it "
                     f"produced — they are not today's."
                     if overdue else None)}


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
            st["publishing"] = _publishing_state(float(st["results_ts"]),
                                                 st["freshness"])
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
        # The abandoned worker is still alive and still writing to _state.
        # Retiring its generation makes every later write from it a no-op,
        # so a scan started right after cancelling cannot interleave its
        # partial rows with the old scan's filters — which is what /status
        # was reporting as a finished result.
        _state["generation"] = _state.get("generation", 0) + 1
    # Reloading the published board here means a reader trying to escape a
    # hang waits on two network fetches to do it. The poller picks it up.
    #
    # The restore below reports its own status, which would overwrite the
    # explanation the reader cancelled to get. Stored results ARE worth
    # showing, but not at the cost of the page silently claiming the scan
    # finished — so the verdict is put back after.
    why = _state["error"]
    restored = _load_snapshot()
    with _lock:
        _state.update(status="error", error=why)
    return jsonify({"ok": True, "restored_previous": bool(restored)})


@app.route("/published")
def published_route():
    """What the scheduled scan has published, and how old it is."""
    idx = _published_index()
    return jsonify({"base": PUBLISHED_BASE, "index": idx,
                    # which commit this process is actually running.
                    # Render sets RENDER_GIT_COMMIT on every deploy; the
                    # release gate compares it against the commit it just
                    # pushed, so "deployed" is an observation, not a claim
                    # about what should have happened by now.
                    "build": os.environ.get("RENDER_GIT_COMMIT"),
                    "using": _state.get("published_preset"),
                    "dir": PUBLISHED_DIR,
                    "dir_listing": sorted(os.listdir(PUBLISHED_DIR))
                                   if os.path.isdir(PUBLISHED_DIR) else None,
                    "reads": dict(_published_reads),
                    # which background threads are actually alive. Twice now
                    # I have inferred "the warmer did not run" from an absence
                    # and been wrong about why; this is the fact itself.
                    "threads": sorted(t.name for t in threading.enumerate()),
                    "warmers": {"done": sorted(_warm_done),
                                "failed": sorted(_warm_failed),
                                # if this is set in the deployment
                                # environment the warmers are disabled and
                                # every book stays empty by design — worth
                                # one line here rather than another round
                                # of inferring it from an absence
                                "suppressed": bool(os.environ.get("SKIP_WARM"))},
                    "loaded": {"prices": len(pretrade._series_of(_price_book())),
                               "vol": len(_vol_book()),
                               "credit": len(_credit_book()),
                               "liquidity": len(_liq_book()),
                               "investors13f": len((_inv13f_book() or {}).get("tickers") or {})}})


@app.route("/published/refresh", methods=["POST"])
def published_refresh():
    """Pull the newest published scan now instead of waiting for the poll."""
    with _lock:
        if _state["status"] == "running":
            return jsonify({"ok": False, "message": "A scan is running."}), 409
        adopted = _load_published(force=True, override_newer=True)
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
    # This used to run a full scan inline, in the request thread, with
    # universe_max=1000 whenever the saved profile was missing — which is
    # after every deploy, because /tmp is wiped. That is four times the
    # cap the page scan enforces to avoid being reaped on a 512MB
    # instance, on an unauthenticated GET any crawler can hit, holding one
    # of eight threads for minutes.
    #
    # Scans have not run here since they moved to CI. The alert's job is
    # to push what was published, so it reads the published board and
    # never scans.
    if _state["status"] == "running":
        return jsonify({"ok": False, "message": "busy"}), 409
    try:
        _load_published()
    except Exception:
        pass
    res = _state["results"] or []
    pend = _state["pending"] or []
    lines = [f"📈 Dip Finder — {time.strftime('%Y-%m-%d %H:%M UTC', time.gmtime())}"]
    br = _state.get("breadth") or {}
    if br.get("risk_factor", 1) < 1:
        lines.append(f"⚠ defensive market (breadth {br.get('pct')}%) — "
                     f"sizes throttled to {br['risk_factor']:g}x")
    # A pushed message that says "buy ≈X, stop Y" is a recommendation,
    # whatever the website says — and the entry signal behind it was
    # falsified against random entry on both rule sets. The alert now
    # sends what is factually true (the watchlist and its levels) framed
    # as what it is, with the refutation attached to every message.
    if res:
        lines.append(f"{len(res)} name(s) on the pattern watchlist:")
        for r in res[:TOP_N]:
            lines.append(f"  {r['ticker']}: {r['price']} vs defended level "
                         f"{r['support']}, earnings {r.get('earnings_in') or 'unverified'}")
        lines.append("Not a recommendation — this pattern tested no better "
                     "than random entry (p=0.41-0.50). Run any name through "
                     "the pre-trade check before acting.")
    else:
        lines.append("nothing on the watchlist today"
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
    # score_parts is a dict for the /full click-to-expand breakdown, not a
    # flat CSV cell — drop it here rather than let pandas stringify it into
    # an unreadable blob for anyone opening this in a spreadsheet.
    rows = [{k: v for k, v in dict(r, row_type="pick").items() if k != "score_parts"}
            for r in _state["results"]]
    rows += [{k: v for k, v in dict(r, row_type="BLOCKED", score=None, shares=None,
                                     risk_EUR=None, cum_risk_EUR=None).items()
               if k != "score_parts"}
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
