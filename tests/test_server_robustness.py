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
# fetch=True is a warmer asking for the CURRENT file. Gating it behind the
# read TTL meant a book fetched a minute before the scan published could
# not be replaced for the best part of an hour, with the new file already
# sitting on the instance's own disk.

fetched = {"n": 0}
_real_pub = A._published_get


def _pub(path):
    if path == "prices.json":
        fetched["n"] += 1
        return {"dates": ["2026-02-02"], "series": {"NEW": [2.0]}}
    return _real_pub(path)


A._published_get = _pub
assert A._price_book(fetch=True)["series"].get("NEW"), \
    "a warmer's fetch must replace the book, however recently it was read"
assert fetched["n"] == 1, fetched
A._published_get = _real_pub
print("a warmer's refetch replaces the book immediately, rather than waiting "
      "out a read-path TTL")


# ---- a slow SEC response must be abandoned, not waited out ----
# Boeing held a request thread past 90 seconds while a 16-second budget
# was supposedly in force, because requests' `timeout` is a gap BETWEEN
# BYTES, not a total: a body that drips steadily never trips it. Reading
# with iter_content does not fix it either — a fixed chunk size blocks
# until the buffer fills and chunk_size=None reads to EOF, so a deadline
# checked inside the loop is never reached. This drips a byte every 20ms,
# which is exactly the shape that defeated both.
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer


class _Drip(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        try:
            self.wfile.write(b'{"units": {"USD": [')
            for _ in range(100000):
                self.wfile.write(b" ")
                self.wfile.flush()
                time.sleep(0.02)
        except Exception:
            pass

    def log_message(self, *a):
        pass


_srv = HTTPServer(("127.0.0.1", 0), _Drip)
_port = _srv.server_address[1]
threading.Thread(target=_srv.serve_forever, daemon=True).start()

_t0 = time.time()
try:
    A._sec_json(f"http://127.0.0.1:{_port}/slow.json", timeout=4)
    raise AssertionError("a response that never ends must not return a value")
except TimeoutError:
    pass
_took = time.time() - _t0
assert _took < 10, f"a 4-second budget took {_took:.1f}s — the body is unbounded"
print(f"a response that never stops arriving is abandoned after {_took:.1f}s")

# and the caller turns that into a refusal, not a crash or a hang
_real_cik_for = A._cik_for
A._cik_for = lambda t, timeout=8.0: 1
A._sec_json = lambda url, timeout=15: (_ for _ in ()).throw(
    TimeoutError("SEC did not answer"))
A._price_book = lambda fetch=False: BOOK2          # SLOW has prices, not filings
BOOK2["series"]["SLOW"] = list(_twin)
A.cache_store.put("credit:SLOW", None)
_rep = A._credit_for("SLOW", budget_s=4)
assert _rep["ok"] and _rep["dd"] is None
assert "did not answer in time" in _rep["verdict"], _rep["verdict"]
assert not _rep.get("cached"), "a timed-out report must not be cached"
print("the credit report turns a slow SEC into a refusal that names the reason")
# no shutdown(): the handler is still mid-drip and shutdown() waits for it.
# The server thread is a daemon, so the process exits regardless.


# ---- abandoned fetches must not permanently consume the slots ----
# The concurrency cap counts fetches a caller is waiting on. This checks
# that a run of abandoned ones leaves the pool full afterwards, so a burst
# of slow filers cannot wedge every later reader on "too many SEC fetches
# already in flight". It does NOT prove the caller-side release matters:
# the orphaned thread's read timeout equals the caller's budget, so the
# slot came back either way — the release only shortens the window.
_slots = A._sec_slots._value
assert _slots >= 2, _slots
for _ in range(_slots + 2):
    try:
        A._sec_json(f"http://127.0.0.1:{_port}/slow.json", timeout=2)
        raise AssertionError("the drip server must never answer")
    except TimeoutError as e:
        assert "in flight" not in str(e), \
            "a previous abandoned fetch is still holding its slot"
assert A._sec_slots._value == _slots, \
    f"{_slots - A._sec_slots._value} slot(s) never came back"
print(f"after {_slots + 2} abandoned fetches all {_slots} slots are free again")


# ---- the warmer must stop when the SEC is refusing ----
# It walks the whole board at boot. If the SEC is rate-limiting this IP,
# pressing on turns a temporary block into a sustained one AND competes
# with every live request, so a reader gets "the SEC did not answer in
# time" for every company while the warmer is busy earning that refusal.
_calls = {"n": 0}
_real_credit_for = A._credit_for


def _always_refuses(t, budget_s=16.0):
    _calls["n"] += 1
    return {"ok": True, "ticker": t, "dd": None, "verdict": "no"}


A._credit_for = _always_refuses
A._state["results"] = [{"ticker": f"T{i}"} for i in range(25)]
_t0 = time.time()
A._warm_credit()
A._credit_for = _real_credit_for
assert _calls["n"] <= 4, \
    f"the warmer made {_calls['n']} calls against a refusing SEC"
print(f"a refusing SEC stops the warmer after {_calls['n']} attempts, "
      f"not {len(A._state['results'])}")


# ---- a failed refresh must not wipe a good book ----
# Each book did `data = _published_get(...) or {}` and stored the result
# unconditionally, so the first transient miss replaced a copy holding
# 1,453 entries with nothing — and the site then served no volatility and
# no credit standings while the files sat on its own disk.
A._price_book = REAL_PRICE_BOOK
A._book.update(data={"dates": ["d"], "series": {"KEEP": [1.0]}}, ts=time.time())
A._vols.update(data={"KEEP": {"vol": 0.2, "obs": 900}}, ts=time.time())
A._creds.update(data={"KEEP": {"dd": 4.0, "band": "comfortable"}}, ts=time.time())

_real_pg = A._published_get
A._published_get = lambda path: None          # every refresh fails
for _fn in (A._price_book, A._vol_book, A._credit_book):
    _fn(fetch=True)
assert A._price_book().get("series", {}).get("KEEP"), "the price book was wiped"
assert A._vol_book().get("KEEP"), "the volatility book was wiped"
assert A._credit_book().get("KEEP"), "the credit book was wiped"

A._published_get = lambda path: {"NEW": {"dd": 9.9}} if path == "credit.json" else None
A._credit_book(fetch=True)
assert A._credit_book().get("NEW"), "a refresh that DOES return data must replace"
assert not A._credit_book().get("KEEP")
A._published_get = _real_pg
print("a refresh that returns nothing keeps the book it had; one that returns "
      "data replaces it")


# ---- overdue means the schedule was missed AND a session traded ----
import time as _t
_now = _t.time()
_late = A._publishing_state(_now - 19.3 * 3600, {"sessions": 2, "stale": True})
assert _late["overdue"] is True and "has not published" in _late["note"]
assert "19.3 hours" in _late["note"], _late["note"]

# a weekend: hours have passed, but no session traded — not a fault
_weekend = A._publishing_state(_now - 60 * 3600, {"sessions": 0, "stale": False})
assert _weekend["overdue"] is False and _weekend["note"] is None

# and a scan from an hour ago is simply current
_fresh = A._publishing_state(_now - 3600, {"sessions": 0, "stale": False})
assert _fresh["overdue"] is False
print("overdue needs both a missed schedule and a traded session, so a weekend "
      "never reads as a fault")

# the removed Run button must not be recommended anywhere
_idx = (ROOT / "templates/index.html").read_text()
assert "press Run" not in _idx, \
    "the page still tells the reader to press a button that was removed"
print("no page tells the reader to press the button that no longer exists")


# ---- the data branch is preferred, and which source answered is visible ----
# A shipped copy is frozen at build time; preferring it meant the site
# would serve one board forever the moment nothing was committing fresh
# results. And the two were indistinguishable from outside, which is how I
# deleted the shipped files on the strength of an index that had come from
# those very files.
import requests as _rq2
_real_get2 = _rq2.get
_pubdir2 = os.path.join(TMP, "shipped2")
os.makedirs(_pubdir2, exist_ok=True)
with open(os.path.join(_pubdir2, "index.json"), "w") as f:
    f.write('{"presets": [{"preset": "SHIPPED"}]}')
_real_dir2 = A.PUBLISHED_DIR
A.PUBLISHED_DIR = _pubdir2


class _Resp:
    status_code = 200

    def json(self):
        return {"presets": [{"preset": "NETWORK"}]}


_rq2.get = lambda *a, **k: _Resp()
A._published_reads.clear()
got = A._published_get("index.json")
assert got["presets"][0]["preset"] == "NETWORK", \
    "the live data branch must win over a copy frozen into the build"
assert A._published_reads["index.json"] == "data branch", A._published_reads


class _Gone:
    status_code = 404

    def json(self):
        raise ValueError


_rq2.get = lambda *a, **k: _Gone()
A._published_reads.clear()
got = A._published_get("index.json")
assert got["presets"][0]["preset"] == "SHIPPED", \
    "with the branch unreachable the shipped copy must still answer"
assert "shipped copy" in A._published_reads["index.json"]
assert "404" in A._published_reads["index.json"], A._published_reads
print("the data branch wins; a shipped copy answers when it cannot, and "
      "/published names which")

_rq2.get = _real_get2
A.PUBLISHED_DIR = _real_dir2

# and a warm instance picks up a new scan without being restarted
import inspect as _i3
assert hasattr(A, "_results_refresher")
assert "_results_refresher" in _i3.getsource(A).split(
    'if not os.environ.get("SKIP_WARM")')[-1], "the poller is never started"
print("a new scan reaches a warm instance by polling, not by restarting it")


# ---- the books must not queue behind the earnings calendar ----
# The calendar is ~32 sequential Nasdaq requests at 15 seconds each, and
# it ran first in the same thread. When Nasdaq was slow it held that
# thread for minutes, and the price book, the volatility book and the
# credit standings all waited behind it — so a reader got a board with no
# credit column and no overlap measurement while all three files sat one
# HTTP GET away.
import inspect as _i4

assert hasattr(A, "_warm_books") and hasattr(A, "_warm_calendar"), \
    "the slow warm and the fast warm must be separable"
_books_src = _i4.getsource(A._warm_books)
assert "_earnings_calendar" not in _books_src, \
    "the books must not wait on the calendar"
# each is registered as its own warmer, so each gets its own thread —
# chaining them is what put the books behind the calendar
_names = [n for n, _ in A._WARMERS]
for _fn in ("warm-books", "warm-calendar"):
    assert _names.count(_fn) == 1, f"{_fn} is not registered exactly once: {_names}"
_calls = {n: set(f.__code__.co_names) for n, f in A._WARMERS}
assert _calls["warm-books"] == {"_warm_books"}, _calls["warm-books"]
assert _calls["warm-calendar"] == {"_warm_calendar"}, _calls["warm-calendar"]

# a calendar that never returns must not stop the books loading
_slow = {"n": 0}


def _never(*a, **k):
    _slow["n"] += 1
    _t.sleep(30)
    return ({}, False)


import time as _t
_real_cal2 = screener._earnings_calendar
screener._earnings_calendar = _never
A._book.update(data=None, ts=0.0)
_saved_pg = A._published_get
A._published_get = lambda path: ({"dates": ["d"], "series": {"X": [1.0]}}
                                 if path == "prices.json" else {})
_t0 = _t.time()
A._warm_books()
assert _t.time() - _t0 < 5, "the books waited on something"
assert A._price_book().get("series", {}).get("X"), "the price book did not load"
assert _slow["n"] == 0, "_warm_books called the calendar"
screener._earnings_calendar = _real_cal2
A._published_get = _saved_pg          # later sections test the real one
print("the books load without touching the calendar, so a slow Nasdaq cannot "
      "empty the page")


# ---- a stale token must not make a public file look deleted ----
# Measured against the real host: the same URL returns 200 with no header
# and 404 with a token attached. raw.githubusercontent answers a token it
# does not accept with 404 rather than 401, so an expired or wrongly
# scoped credential makes a PUBLIC file indistinguishable from a missing
# one — and the site sat empty with every file one anonymous GET away.
import requests as _rq3
_real3 = _rq3.get
_seen = []


class _R:
    def __init__(self, code, body=None):
        self.status_code, self._b = code, body

    def json(self):
        return self._b


def _token_hostile(url, headers=None, timeout=None):
    _seen.append(bool((headers or {}).get("Authorization")))
    # 404 whenever a token is presented, 200 when it is not
    if (headers or {}).get("Authorization"):
        return _R(404)
    return _R(200, {"presets": [{"preset": "ANON"}]})


os.environ["PUBLISHED_TOKEN"] = "stale-token"
_rq3.get = _token_hostile
A.PUBLISHED_DIR = os.path.join(TMP, "empty_pub")
os.makedirs(A.PUBLISHED_DIR, exist_ok=True)
A._published_reads.clear()
got = A._published_get("index.json")
assert got and got["presets"][0]["preset"] == "ANON", got
assert _seen and _seen[0] is False, "the anonymous attempt must come first"
assert A._published_reads["index.json"] == "data branch"
print("a public file is read anonymously, so a stale token cannot hide it")

# and where the token IS required, it is still tried
_seen.clear()


def _token_required(url, headers=None, timeout=None):
    _seen.append(bool((headers or {}).get("Authorization")))
    if (headers or {}).get("Authorization"):
        return _R(200, {"presets": [{"preset": "AUTHED"}]})
    return _R(404)


_rq3.get = _token_required
A._published_reads.clear()
got = A._published_get("index.json")
assert got["presets"][0]["preset"] == "AUTHED", got
assert _seen == [False, True], _seen
assert "with token" in A._published_reads["index.json"]
print("where the token is genuinely needed it is still used, as a retry")

_rq3.get = _real3
os.environ.pop("PUBLISHED_TOKEN", None)

print("\nALL SERVER-ROBUSTNESS TESTS PASSED")


# ---- a warmer that is not running must be restarted ----
# Measured on the live instance: /published reported MainThread and one
# gunicorn worker thread and NOTHING else, while the price, volatility
# and credit books were all empty and every credit report on the site
# said "not measured". Whether the threads died or were started in a
# master that then forked, starting them once at import was not enough.
_saved_warmers = A._WARMERS
_saved_done, _saved_failed = set(A._warm_done), dict(A._warm_failed)
_saved_skip = os.environ.get("SKIP_WARM")
try:
    os.environ.pop("SKIP_WARM", None)
    A._warm_done.clear(); A._warm_failed.clear()

    _ran = []
    _stop = threading.Event()
    A._WARMERS = (("t-forever", lambda: (_ran.append("forever"), _stop.wait())),
                  ("t-once", lambda: _ran.append("once")),
                  ("t-broken", lambda: (_ran.append("broken"),
                                        (_ for _ in ()).throw(RuntimeError("nope")))))

    assert sorted(A._ensure_warm()) == ["t-broken", "t-forever", "t-once"]
    for _ in range(50):
        if len(_ran) >= 3: break
        time.sleep(0.02)
    assert sorted(_ran) == ["broken", "forever", "once"], _ran
    print("every warmer that was not running got started")

    # the one still looping is not restarted, the one that finished is not
    # re-run, and the one that raised is held off rather than restarted by
    # every request for as long as the failure lasts
    for _ in range(50):
        if "t-once" in A._warm_done and "t-broken" in A._warm_failed: break
        time.sleep(0.02)
    assert A._ensure_warm() == [], A._ensure_warm()
    assert "t-once" in A._warm_done and "t-broken" not in A._warm_done
    assert "t-broken" in A._warm_failed
    print("a running warmer is left alone, a finished one is not re-run, and a "
          "failed one is not restarted on every single request")

    # but once the hold-off expires the failed one IS tried again — a
    # transient failure must not disable a book for the life of the process
    _retried = []
    for _ in range(100):
        # the failed thread may not have exited yet, and a warmer that is
        # still running must not be started twice — so poll rather than
        # racing it
        A._warm_failed["t-broken"] = time.time() - A.WARM_RETRY_S - 1
        _retried = A._ensure_warm()
        if _retried:
            break
        time.sleep(0.02)
    assert _retried == ["t-broken"], _retried
    print("a failure is retried later, not treated as permanent")

    # and the guard is wired to every request, which is the only thing
    # standing between one dead thread and a page with no data on it
    A._warm_done.clear(); A._warm_failed.clear(); _ran.clear()
    _stop.set()
    c = A.app.test_client()
    c.get("/published")
    for _ in range(50):
        if "once" in _ran: break
        time.sleep(0.02)
    assert "once" in _ran, _ran
    print("a request restarts them, so the site repairs itself without a deploy")
finally:
    _stop.set()
    A._WARMERS = _saved_warmers
    A._warm_done.clear(); A._warm_done.update(_saved_done)
    A._warm_failed.clear(); A._warm_failed.update(_saved_failed)
    if _saved_skip is not None:
        os.environ["SKIP_WARM"] = _saved_skip

print("\nWARMER SUPERVISION PINNED")


# ---- a published file that never arrives must not empty the site ----
# Watched on the live instance: the warm-books thread alive and running,
# three minutes into a single GET for the 650KB price book, with no read
# recorded either way — so the price book, the volatility book and the
# credit standings were all empty and every credit report on the site
# said "not measured". requests' timeout is the gap between bytes, not a
# total, so a body that trickles never trips it.
import requests as _rq_mod

_saved_get = _rq_mod.get
_saved_reads = dict(A._published_reads)
_saved_fetch_s = A.PUBLISHED_FETCH_S
_hung = threading.Event()
try:
    def _never_returns(url, **kw):
        _hung.wait(30)          # far longer than the bound below
        raise AssertionError("this response must never be used")

    _rq_mod.get = _never_returns
    A.PUBLISHED_FETCH_S = 0.5
    A._published_reads.clear()

    _t0 = time.time()
    _got = A._published_get("prices.json")
    _spent = time.time() - _t0
    assert _spent < 5, f"the fetch was not bounded ({_spent:.1f}s)"
    print(f"a fetch that never answers is abandoned in {_spent:.1f}s, not waited on")

    # and it says so, rather than looking like an empty file
    _why = A._published_reads.get("prices.json") or ""
    assert "no answer within" in _why, _why
    print(f"and it reports why: {_why!r}")

    # the shipped copy answers in its place, so the page keeps its data
    import json as _json
    os.makedirs(A.PUBLISHED_DIR, exist_ok=True)
    with open(os.path.join(A.PUBLISHED_DIR, "prices.json"), "w") as _f:
        _json.dump({"dates": ["d1"], "series": {"SHIP": [1.0]}}, _f)
    A._published_reads.clear()
    _got = A._published_get("prices.json")
    assert _got and "SHIP" in (_got.get("series") or {}), _got
    assert "shipped copy" in A._published_reads.get("prices.json", "")
    print("the copy shipped with the build answers instead, and is labelled as such")
finally:
    _hung.set()
    _rq_mod.get = _saved_get
    A.PUBLISHED_FETCH_S = _saved_fetch_s
    A._published_reads.clear(); A._published_reads.update(_saved_reads)
    try:
        os.remove(os.path.join(A.PUBLISHED_DIR, "prices.json"))
    except OSError:
        pass

print("\nBOUNDED PUBLISHED FETCH PINNED")


# ---- the alert channel must not recommend trades either ----
# The website's pick was removed after the permutation test; the daily
# Telegram/Discord push still said "buy ≈X, stop Y, target Z" — the same
# falsified recommendation, by another door, into a channel with no
# /limits link in sight. The message now sends the watchlist as a
# watchlist, with the refutation attached.
_saved_send = A._send_alert
_saved_lp = A._load_published
_sent_texts = []
try:
    A._send_alert = lambda text: (_sent_texts.append(text) or ["test"])
    A._load_published = lambda *a, **k: None
    A._state.update(results=[{"ticker": "ZZA", "price": 100.0, "stop": 94.0,
                              "resistance": 118.0, "support": 96.0,
                              "shares": 12, "risk_EUR": 100, "RR": 3.2,
                              "score": 71, "earnings_in": ">45d"}],
                    pending=[], status="done", results_ts=time.time())
    c = A.app.test_client()
    r = c.post("/alert")
    assert r.status_code == 200, r.status_code
    assert _sent_texts, "no alert text produced"
    text = _sent_texts[-1]
    assert "buy" not in text.lower(), text
    assert "target" not in text.lower(), text
    assert "Not a recommendation" in text and "random entry" in text, text
    assert "ZZA" in text and "defended level" in text
    print("the pushed alert is a watchlist with its refutation, not a buy signal")
finally:
    A._send_alert = _saved_send
    A._load_published = _saved_lp

print("\nALERT WORDING PINNED")


# ---- /check parses the holdings format the page itself teaches ----
# The textarea says "One per line — TICKER, shares, cost". The endpoint
# split that on commas AND whitespace alike, so "MSFT, 10, 300" became
# three positions named MSFT, 10 and 300 — and the reply said "4 of your
# 6 positions could not be compared" about a two-position book, with the
# numbers rendered as tickers in user-facing text.
_c2 = A.app.test_client()
_saved_view_p = A._credit_view
A._credit_view = lambda: {"ZQX": {"dd": 5.0}}   # the symbol must be known,
# or the unknown-symbol refusal answers before the parser ever runs
for _payload, _want in (
        ("MSFT, 10, 300\nNVDA, 5, 120", ["MSFT", "NVDA"]),
        ("MSFT NVDA", ["MSFT", "NVDA"]),
        ("MSFT,NVDA", ["MSFT", "NVDA"]),
        ("BRK-B, 2, 410\nSHELL.L, 100, 2450", ["BRK-B", "SHELL.L"]),
        ("10, 20, 30", []),
        ("", [])):
    _r = _c2.post("/check", json={"ticker": "ZQX", "holdings": _payload}).get_json()
    assert _r.get("held") == _want, (_payload, _r.get("held"))
A._credit_view = _saved_view_p
print("every holdings shape the page teaches parses to tickers, never numbers")

print("\nCHECK PARSER PINNED")


# ---- the published earnings calendar replaces the warm-up block ----
# "Earnings calendar is still loading — try again in a minute" was the
# single most common sentence this tool actually said to a user: the
# instance rebuilt the calendar from ~32 Nasdaq calls after every
# deploy. The scan publishes the finished map now, and a fresh instance
# answers from it instead of asking the reader to wait out a warm-up.
import datetime as _dt2
_saved_earn = dict(A._earn_pub)
try:
    A._earn_pub.update(data={
        "as_of": _dt2.date.today().isoformat(), "complete": True,
        "map": {"ZQX": 30, "NEARER": 4}}, ts=time.time())
    c3 = A.app.test_client()
    r = c3.post("/check", json={"ticker": "ZQX", "holdings": ""}).get_json()
    heads = [f["headline"] for f in r["findings"]]
    assert not any("still loading" in h for h in heads), heads
    assert any("No earnings for 30 days" in h for h in heads), heads
    assert r["bottom_line"], r
    print("a fresh instance answers the earnings question from the published calendar")

    # a company reporting soon still blocks — the data is used, not bypassed
    r2 = c3.post("/check", json={"ticker": "NEARER", "holdings": ""}).get_json()
    assert any("Earnings in 4 days" in f["headline"] for f in r2["findings"])
    assert r2["bottom_line"].startswith("Do not buy this today")
    print("and the earnings gate still blocks what it should")

    # a stale published calendar is refused, not trusted about 'clear'
    A._earn_pub["data"]["as_of"] = (
        _dt2.date.today() - _dt2.timedelta(days=5)).isoformat()
    cal, ok, _single = A._published_earnings()
    assert cal == {} and ok is False
    print("a five-day-old published calendar is refused rather than believed")

    # and re-basing: yesterday's map moves one day closer today
    A._earn_pub["data"]["as_of"] = (
        _dt2.date.today() - _dt2.timedelta(days=1)).isoformat()
    cal, ok, _single = A._published_earnings()
    assert cal.get("NEARER") == 3, cal
    print("the published days-to-report are re-based onto today")
finally:
    A._earn_pub.clear(); A._earn_pub.update(_saved_earn)

print("\nPUBLISHED EARNINGS PINNED")


# ---- one company, one answer; an unknown symbol, no green ticks ----
# BRK.B (the form brokers print) answered "Not a US filer" — a false
# statement about the most famous filer in the country — while BRK-B got
# the honest sector refusal. And ZZZZ, a symbol that does not exist,
# collected a green "No earnings due for at least 45 days".
_saved_map = dict(A._cik_map)
_saved_view = A._credit_view
try:
    A._cik_for = _real_cik_for          # an earlier section stubbed it
    A._cik_map.update(data={"BRK-B": 1067983}, ts=time.time())
    assert A._cik_for("BRK-B") == 1067983
    assert A._cik_for("BRK.B") == 1067983, "the dot form must resolve too"
    print("BRK.B and BRK-B resolve to the same company")

    A._credit_view = lambda: {"BRK-B": {"dd": None, "not_modelled": True,
                                        "verdict": "Banks and insurers are "
                                                   "not modelled here."}}
    rep = A._credit_for("BRK.B")
    assert rep.get("not_modelled"), rep
    print("the dot form gets the same honest refusal as the dash form")

    _c4 = A.app.test_client()
    r = _c4.post("/check", json={"ticker": "ZZZZ", "holdings": ""}).get_json()
    assert r["verdict"] == "warn"
    assert "check the spelling" in r["bottom_line"], r["bottom_line"]
    assert not any(f["level"] == "ok" for f in r["findings"]), r["findings"]
    print("an unknown symbol gets 'nothing is known', never a green tick")
finally:
    A._cik_map.clear(); A._cik_map.update(_saved_map)
    A._credit_view = _saved_view

print("\nALIAS AND UNKNOWN-SYMBOL PINNED")


# ---- "unknown symbol" is only a fact when the books are loaded ----
# On a freshly restarted instance the books are empty for a minute, and
# the unknown-symbol gate answered "Nothing is known about AAPL — check
# the spelling" to the first visitor after every deploy. An empty
# library is not evidence about a book.
_saved_pb2, _saved_view2 = A._price_book, A._credit_view
try:
    A._price_book = lambda fetch=False: {}
    A._credit_view = lambda: {}
    _c5 = A.app.test_client()
    r = _c5.post("/check", json={"ticker": "AAPL", "holdings": ""}).get_json()
    assert "check the spelling" not in (r.get("bottom_line") or ""), r["bottom_line"]
    print("an empty instance never calls a real ticker a typo")

    # and once the books exist, a ghost is still a ghost
    A._credit_view = lambda: {"AAPL": {"dd": 5.0}}
    r2 = _c5.post("/check", json={"ticker": "ZZZZ", "holdings": ""}).get_json()
    assert "check the spelling" in (r2.get("bottom_line") or ""), r2
    print("a loaded instance still refuses ghosts")
finally:
    A._price_book, A._credit_view = _saved_pb2, _saved_view2

print("\nWARM-UP VS UNKNOWN PINNED")
