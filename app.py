"""Web front-end for the pullback screener, built for Render's free plan.

The free plan sleeps when idle and wakes on request ("on demand"), so the app
runs the scan only when you press Run: a background thread does the work while
the page polls /status for live progress. The latest results are kept in
memory and mirrored to a CSV on the instance's ephemeral disk, so they survive
page reloads (but not a full spin-down — just press Run again).
"""

import io
import os
import threading
import time
from collections import Counter

import pandas as pd
from flask import Flask, Response, jsonify, render_template

import screener

app = Flask(__name__)

RESULTS_CSV = os.environ.get("RESULTS_CSV", "/tmp/screener_results.csv")

_lock = threading.Lock()
_state = {
    "status": "idle",          # idle | running | done | error
    "log": [],
    "started_at": None,
    "finished_at": None,
    "elapsed_s": None,
    "universe_size": None,
    "results": [],             # list of row dicts
    "rejection_summary": [],   # [{"reason": ..., "count": ...}]
    "near_misses": [],         # [{"ticker": ..., "reason": ...}]
    "error": None,
}


def _load_cached_csv():
    if os.path.exists(RESULTS_CSV):
        try:
            df = pd.read_csv(RESULTS_CSV)
            _state["results"] = df.to_dict("records")
            _state["status"] = "done"
            _state["finished_at"] = os.path.getmtime(RESULTS_CSV)
            _state["log"] = ["Loaded cached results from previous run."]
        except Exception:
            pass


_load_cached_csv()


def _progress(msg):
    for line in str(msg).splitlines():
        if line.strip():
            _state["log"].append(line)
    # keep the log bounded
    if len(_state["log"]) > 500:
        _state["log"][:] = _state["log"][-500:]


def _run_scan():
    try:
        result = screener.run_screener(progress=_progress)
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
        _state["results"] = df.to_dict("records") if len(df) else []
        _state["universe_size"] = result["universe_size"]
        _state["elapsed_s"] = round(result["elapsed_s"])
        if len(df):
            df.to_csv(RESULTS_CSV, index=False)
        _state["status"] = "done"
    except Exception as e:
        _state["error"] = f"{type(e).__name__}: {e}"
        _state["status"] = "error"
        _progress(f"Scan failed: {_state['error']}")
    finally:
        _state["finished_at"] = time.time()


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/run", methods=["POST"])
def run():
    with _lock:
        if _state["status"] == "running":
            return jsonify({"ok": False, "message": "A scan is already running."}), 409
        _state.update(status="running", log=[], error=None,
                      started_at=time.time(), finished_at=None,
                      rejection_summary=[], near_misses=[])
        threading.Thread(target=_run_scan, daemon=True).start()
    return jsonify({"ok": True})


@app.route("/status")
def status():
    return jsonify(_state)


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
