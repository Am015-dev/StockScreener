"""Routes that used to fail, and the conditions that made them fail.

Every case here was reproduced against the running app before it was
fixed. They are grouped by what the failure cost a reader: a scan that
never ends, a check that refuses forever, an endpoint that crashes on
input the shipped page does not send but an API client does.

Hermetic: SKIP_WARM stops the background threads, and the only network
callers are stubbed.
"""
import os
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TMP = tempfile.mkdtemp(prefix="server_")
os.environ.update(MARKET_DB=os.path.join(TMP, "m.db"),
                  JOURNAL_DB=os.path.join(TMP, "j.db"),
                  SCREENER_CACHE_DB=os.path.join(TMP, "c.db"),
                  RESULTS_CSV=os.path.join(TMP, "r.csv"), SKIP_WARM="1")
sys.path.insert(0, str(ROOT))

import app as A
import screener

client = A.app.test_client()


# ---- /check accepts holdings in every shape a client can send ----
# The page posts objects. A hand-edited localStorage, an API client or an
# older schema sends bare tickers or a mapping, and pretrade did
# h.get("ticker") on a string: a 500 rather than an answer.
for name, held in (("objects", [{"ticker": "MSFT", "shares": 3}]),
                   ("bare strings", ["MSFT", "NVDA"]),
                   ("a mapping", {"MSFT": 10}),
                   ("mixed junk", ["MSFT", None, 7, {"ticker": "KO"}, ""]),
                   ("empty", []),
                   ("a string", "MSFT NVDA")):
    r = client.post("/check", json={"ticker": "AAPL", "holdings": held})
    assert r.status_code < 500, f"{name} -> {r.status_code}"
print("/check answers for holdings sent as objects, strings, a mapping or junk")

r = client.post("/check", json={})
assert r.status_code == 400, r.status_code
print("/check with no ticker is a 400, not a crash")


# ---- an abandoned scan must stop writing to the page ----
# Cancelling left the worker alive and still writing, so a scan started
# straight after interleaved its rows with the cancelled scan's filters
# and /status served the pair as a finished result.
A._state.update(status="running", generation=0, results=[], params_used={"a": 1},
                started_at=time.time(), last_progress_ts=time.time() - 999)
old_gen = A._state["generation"]
assert client.post("/cancel").status_code == 200
assert A._state["generation"] == old_gen + 1, "cancel must retire the worker"

A._on_partial([{"ticker": "GHOST", "score": 9}], 10, 10, gen=old_gen)
A._progress("a message from the abandoned scan", gen=old_gen)
assert not any(r.get("ticker") == "GHOST" for r in A._state["results"]), \
    "the cancelled scan is still writing rows into the live page"
assert not any("abandoned scan" in line for line in A._state["log"])

A._on_partial([{"ticker": "LIVE", "score": 9}], 10, 10,
              gen=A._state["generation"])
assert A._state["results"][0]["ticker"] == "LIVE", "the current scan still writes"
print("a cancelled scan's rows and log lines are dropped; the new one's are kept")


# ---- /alert must not run a scan on the web instance ----
# It ran a full 1,000-stock scan inline in the request thread, on an
# unauthenticated GET, at four times the cap the page enforces to avoid
# being reaped on a 512MB instance.
scanned = {"n": 0}
_real = screener.run_screener
screener.run_screener = lambda *a, **k: scanned.__setitem__("n", scanned["n"] + 1)
A._state.update(status="done", results=[], pending=[])
r = client.get("/alert")
screener.run_screener = _real
assert r.status_code == 200, r.status_code
assert scanned["n"] == 0, "/alert started a scan on the web instance"
print("/alert reports the published board and never scans")


# ---- request bodies are bounded on a 512MB instance ----
assert A.app.config.get("MAX_CONTENT_LENGTH"), "an unbounded body is a memory bomb"
big = "x" * (A.app.config["MAX_CONTENT_LENGTH"] + 1024)
r = client.post("/snapshot/restore", data=big,
                content_type="application/json")
assert r.status_code == 413, r.status_code
print(f"a body over {A.app.config['MAX_CONTENT_LENGTH'] // (1024*1024)}MB is "
      f"refused rather than parsed into memory")


# ---- the earnings calendar has something that rebuilds it ----
# The warmer ran once at boot; the cache expires after six hours; scans
# run in CI. So every check after six hours refused with "still loading,
# try again in a minute" — permanently.
import inspect
assert hasattr(A, "_calendar_refresher"), \
    "nothing rebuilds the earnings calendar after its cache expires"
src = inspect.getsource(A._calendar_refresher)
assert "while True" in src and "_earnings_calendar" in src
started = inspect.getsource(A).split("if not os.environ.get(\"SKIP_WARM\")")[-1]
assert "_calendar_refresher" in started, "the refresher is defined but never started"
print("the earnings calendar is rebuilt on a loop, not once at boot")

print("\nALL SERVER-ROBUSTNESS TESTS PASSED")
