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
# The deploy branch SHIPS published/*.json, so a CI checkout has real
# scan results on disk that the app adopts at import — overriding
# whatever this test set up. Point the lookup somewhere empty.
os.environ["PUBLISHED_DIR"] = os.path.join(TMP, "no_published")
os.makedirs(os.environ["PUBLISHED_DIR"], exist_ok=True)
sys.path.insert(0, str(ROOT))

import app as A
import screener

REAL_PRICE_BOOK = A._price_book   # later sections stub this out

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

# and the explanation the reader cancelled to get must survive the restore
# that follows it. Restoring stored results reports its own status, which
# overwrote the abandoned-scan verdict with "done" — so the page claimed
# the scan had finished, which is the opposite of what happened.
A._state.update(status="running", generation=A._state["generation"],
                started_at=time.time(), last_progress_ts=time.time() - 999,
                error=None)
_restored = {"n": 0}
_real_snap = A._load_snapshot


def _snap_that_reports_done():
    _restored["n"] += 1
    A._state.update(status="done", error=None, results=[{"ticker": "OLD"}])
    return True


A._load_snapshot = _snap_that_reports_done
assert client.post("/cancel").status_code == 200
A._load_snapshot = _real_snap
assert _restored["n"] == 1, "the restore did not run"
st = client.get("/status").get_json()
assert st["status"] == "error", st["status"]
assert "abandoned" in (st["error"] or "").lower(), st["error"]
assert any(r.get("ticker") == "OLD" for r in st.get("results") or []), \
    "stored results are still worth showing alongside the explanation"
print("cancelling keeps its explanation even when stored results are restored")


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


# ---- the /check ROUTE, not just the helper underneath it ----
# pretrade.check() is covered thoroughly in isolation. The route that
# assembles its arguments was covered only by "did not return 500", and
# two mutations survived that: forcing the calendar's `complete` flag to
# True (so a company merely ABSENT from a broken calendar reads as
# verified instead of blocked — the exact fail-open the product exists to
# remove), and dropping `holdings` (so every check silently answers "no
# holdings given" and never measures overlap).
N_DAYS = 70
DATES2 = [f"2026-{1 + i // 28:02d}-{1 + i % 28:02d}" for i in range(N_DAYS)]


def _walk(seed, drift=0.0):
    x, out, v = seed, [], 100.0
    for _ in range(N_DAYS):
        x = (1103515245 * x + 12345) % (2 ** 31)
        v *= 1 + drift + ((x / 2 ** 31) - 0.5) * 0.03
        out.append(round(v, 2))
    return out


_twin = _walk(5)
BOOK2 = {"dates": DATES2,
         "series": {"AAA": _twin, "BBB": list(_twin), "ZZZ": _walk(99)}}
A._price_book = lambda fetch=False: BOOK2
A._state.update(results=[], status="done")


def _check(ticker, holdings, cal, complete):
    screener._earnings_calendar = lambda build=False, **k: (cal, complete)
    A.cache_store.put(f"earncal:{screener.EARN_CAL_DAYS}", {"x": 1})
    return client.post("/check", json={"ticker": ticker,
                                       "holdings": holdings}).get_json()


_real_cal = screener._earnings_calendar

# a company ABSENT from a COMPLETE calendar has no earnings due: pass
r = _check("ZZZ", [{"ticker": "AAA"}], {"AAA": 90}, True)
_heads = " ".join(f["headline"] for f in r["findings"])
assert "still loading" not in _heads, _heads
assert not any(f["level"] == "block" for f in r["findings"]), _heads

# the SAME input against a HOLED calendar must block instead
r = _check("ZZZ", [{"ticker": "AAA"}], {"AAA": 90}, False)
assert any(f["level"] == "block" for f in r["findings"]), \
    "absence from an incomplete calendar must block, not read as verified"
print("the route passes the calendar's completeness through: absent+complete "
      "passes, absent+holed blocks")

# earnings inside the gate window block whatever the completeness flag says
r = _check("ZZZ", [{"ticker": "AAA"}], {"ZZZ": 3}, True)
assert any(f["level"] == "block" for f in r["findings"]), r["findings"]
print("an earnings date inside the gate window blocks the trade")

# and the holdings actually reach the comparison
r = _check("AAA", [{"ticker": "BBB"}], {"AAA": 90, "BBB": 90}, True)
_text = " ".join(f["headline"] + " " + f["detail"] for f in r["findings"])
assert "No holdings given" not in _text, _text
assert "already own" in _text or "overlap" in _text.lower(), _text
assert r.get("book_size") == 3, r.get("book_size")
print("holdings posted to the route reach the overlap comparison")

screener._earnings_calendar = _real_cal


# ---- the price book must actually expire ----
# Dropping the TTL check left the suite green. The refresher calls through
# the same function, so a book that never expires is a book that is never
# refreshed: the correlation half of every check would run on frozen
# prices indefinitely, with nothing on the page saying so.
A._price_book = REAL_PRICE_BOOK
A._book.update(data={"dates": ["2026-01-01"], "series": {"OLD": [1.0]}},
               ts=time.time())
assert A._price_book().get("series", {}).get("OLD"), "a fresh book is served"

A._book["ts"] = time.time() - (A.BOOK_TTL + 60)
fetched = {"n": 0}
_real_pub = A._published_get


def _pub(path):
    if path == "prices.json":
        fetched["n"] += 1
        return {"dates": ["2026-02-02"], "series": {"NEW": [2.0]}}
    return _real_pub(path)


A._published_get = _pub
assert A._price_book(fetch=True)["series"].get("NEW"), "an expired book refetches"
assert fetched["n"] == 1, fetched
A._published_get = _real_pub
print(f"the price book expires after {A.BOOK_TTL:.0f}s and is refetched, rather "
      f"than being frozen for the life of the process")

print("\nALL SERVER-ROBUSTNESS TESTS PASSED")
