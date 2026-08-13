"""Corrupted scan rows must never turn into a 500, on whatever still renders them.

Rows arrive from a scan, a CSV reload, a published payload and a snapshot
mirrored out of someone's browser. Any of them can be a schema older than
the running code.

This used to sweep /brief, which Jinja-rendered `_state["results"]`
directly into HTML — the one place a stray None or NaN could raise inside
a template filter. /brief is retired now (folded into "/", which reads
the published books, not `_state`, and never touches these rows at all),
so nothing server-side Jinja-renders this data any more. The place a
corrupted row can still blow up a request is /results.csv, which builds a
pandas DataFrame straight out of it — a different failure mode (column
alignment, dtype coercion) but the same shape of bug, and pandas is
considerably more likely than jsonify to choke on a mixed-type column.
"""
import os, sys, tempfile, time, itertools, random
T = tempfile.mkdtemp()
os.environ.update(MARKET_DB=T+"/m.db", JOURNAL_DB=T+"/j.db",
                  SCREENER_CACHE_DB=T+"/c.db", RESULTS_CSV=T+"/r.csv", SKIP_WARM="1")
from pathlib import Path
# The deploy branch SHIPS published/*.json, so a CI checkout has real scan
# results on disk that the app adopts at import — overriding whatever this
# test set up. Point the lookup somewhere empty.
os.environ["PUBLISHED_DIR"] = os.path.join(T, "no_published")
os.makedirs(os.environ["PUBLISHED_DIR"], exist_ok=True)
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import app as A

FIELDS = ["ticker", "name", "price", "stop", "resistance", "RSI", "support",
          "shares", "risk_EUR", "RR", "mpc_r", "edge_r", "friction_pct",
          "earnings_in", "sector", "analyst", "vol_ratio", "score"]
JUNK = [None, "", "n/a", float("nan"), float("inf"), 0, -1, "12.5", [], {},
        True, 1e308, "  ", "None"]

c = A.app.test_client()
random.seed(7)
bad = []
GOOD = {"ticker": "ZZA", "name": "Test", "price": 100.0, "stop": 94.0,
        "resistance": 118.0, "RSI": 42.0, "support": 96.0, "shares": 12.0,
        "risk_EUR": 100.0, "RR": 3.2, "mpc_r": 0.2, "edge_r": 0.1,
        "friction_pct": 5.0, "earnings_in": ">45d", "sector": "Tech",
        "analyst": "buy", "vol_ratio": 0.9, "score": 71}

# one field corrupted at a time, every field x every junk value
n = 0
for fld, junk in itertools.product(FIELDS, JUNK):
    row = dict(GOOD); row[fld] = junk
    A._state.update(results=[row], top_picks=[row], pending=[],
                    results_ts=time.time(), status="done")
    n += 1
    if c.get("/results.csv").status_code != 200:
        bad.append((fld, repr(junk)))

# whole rows of junk, and missing keys
for _ in range(400):
    row = {f: random.choice(JUNK) for f in random.sample(FIELDS, random.randint(0, 8))}
    A._state.update(results=[row], top_picks=[row], pending=[row],
                    results_ts=random.choice([None, time.time(), 0]),
                    status="done")
    n += 1
    if c.get("/results.csv").status_code != 200:
        bad.append(("random", repr(row)[:120]))

print(f"{n} corrupted-row exports")
if bad:
    print(f"FAILED {len(bad)}:")
    for b in bad[:12]: print("   ", b)
    sys.exit(1)
print("/results.csv built a file from every one of them")

print("\nPAGE ROBUSTNESS PINNED")


# ---- the front page must not depend on _state at all ----
# The whole point of moving off /brief: a corrupted scan row must not be
# able to take down the page a reader actually lands on. Today reads the
# published books, not the live scan state — prove that by corrupting
# _state as badly as the sweep above and confirming the root is
# unaffected.
A._state.update(results=[{"ticker": None, "price": float("nan")}],
                top_picks=[{}], pending=[{"ticker": []}],
                results_ts="not a number", status="running")
_r = c.get("/")
assert _r.status_code == 200, _r.status_code
print("the front page is unaffected by garbage in the live scan state")


# ---- the credit report must be reachable from the front page ----
# It was not, once: the report rendered, the route answered 200, and the
# only link to it was inside a <details> nested inside another collapsed
# table — nothing on the page hinted it existed. That is a different bug
# from what's here now: the credit lookup sits inside its own top-level,
# clearly-labelled "check something before you trade" toggle, which is
# the same progressive-disclosure pattern already used for "why it ranks
# where it does" and the excluded-names list elsewhere on this page. The
# property worth pinning is narrower — the toggle exists, says plainly
# what is behind it, and the tools it reveals are actually there in the
# HTML, not synthesised by JS after the fact.
import re as _re

_BOOK = {f"CR{i}": {"dd": 1.0 + i, "band": "comfortable"} for i in range(6)}
_saved_cb = A._credit_book
A._credit_book = lambda fetch=False: _BOOK
try:
    _html = c.get("/").get_data(as_text=True)
    _m = _re.search(r'<details class="card" id="ownGate">\s*<summary[^>]*>(.*?)</summary>',
                    _html, _re.S)
    assert _m, "the check-something-before-you-trade toggle is missing"
    _summary = _re.sub(r"\s+", " ", _m.group(1)).strip()
    assert "credit" in _summary.lower() or "check" in _summary.lower(), _summary
    print(f"the check toggle says what it opens: {_summary[:70]}...")

    _links = _re.findall(r'href="/credit/([A-Z0-9.\-]+)"', _html)
    assert len(set(_links)) >= 4, sorted(set(_links))
    print(f"{len(set(_links))} credit reports are reachable from the front page")

    assert 'id="crTicker"' in _html and 'id="crGo"' in _html
    assert 'id="ckTicker"' in _html and 'id="ckGo"' in _html
    print("any company can be looked up by name, and the pre-trade check is there too")
finally:
    A._credit_book = _saved_cb

# with an empty book the toggle is still there, and still says something
# true — an absence of measurements must not read as an absence of the
# feature
A._credit_book = lambda fetch=False: {}
A._credit_view_memo["key"] = None   # bust the restated-book cache — its key is
                                    # (_creds.ts, _book.ts), neither of which
                                    # this swap touches, so a stale memo would
                                    # otherwise still show the old six rows
try:
    _html = c.get("/").get_data(as_text=True)
    _flat = _re.sub(r"\s+", " ", _html)
    assert 'id="crTicker"' in _html
    assert "No company has been measured yet" in _flat
    print("with nothing measured the way in is still there, and says why it is empty")
finally:
    A._credit_book = _saved_cb

print("\nCREDIT REPORT REACHABILITY PINNED")


# ---- the page must not hide its content behind a question ----
# "I do not see any Moodys like report anywhere" was a display:none gate
# that only an answer or a skip revealed — and the skip lived in
# sessionStorage, so it came back on every visit. Today has no such gate:
# the picks (or the "nothing cleared" explanation) render unconditionally,
# and the one optional question — what do you own — is a closed toggle a
# reader opens by choice, not a wall in front of everything else.
A._state.update(results=[], top_picks=[], pending=[], results_ts=None, status="idle")
_html = c.get("/").get_data(as_text=True)
assert "<h1>" in _html, "no headline rendered server-side"
assert 'style="display:none"' not in _html.split("<h1>")[0][-400:], \
    "something upstream of the headline can hide the page"
_gate_m = _re.search(r'<details class="card" id="ownGate">', _html)
assert _gate_m, "the holdings/check toggle is missing"
assert "open" not in _html[_gate_m.start():_gate_m.start() + 60].split(">")[0], \
    "the check toggle ships open — it is meant to stay closed until asked for"
print("the page does not ship with its content hidden, and the one question waits to be asked")


# ---- the page must not recommend a trade its own test falsified ----
# The permutation test ran twice — balanced (p = 0.50, 25 seeds, 376
# stocks) and wide-net (p = 0.41, 40 seeds, 657 stocks) — and coin-flip
# entry did as well or better both times. The old headline was "Closest
# to the setup today: <ticker>", with the refutation in small print
# underneath. A tool that headlines a number it has proven means nothing
# is a toy.
assert "Closest to the setup today" not in _html
assert "Nothing to do today" not in _html
print("no trade is recommended anywhere on the front page")

# and the page states plainly, in its own words, that nothing here is a
# forecast — not just by omission
assert "no price target" in _html.lower() or "coin flip" in _html.lower()
print("the absence of a recommendation is stated, not just implied by omission")


# ---- /full carries the verdict too ----
# The full board is where the stops, targets and sizes live, which makes
# it the page MOST likely to be read as advice — it must state the
# falsification before it shows a single row.
_full = c.get("/full").get_data(as_text=True)
assert _full and "tested against random entry and failed" in _full
_i_verdict = _full.find("tested against random entry")
_i_table = _full.find("<table")
assert _i_verdict != -1 and (_i_table == -1 or _i_verdict < _i_table)
assert "Finds strong, rising stocks" not in _full, \
    "the old sales pitch is back"
assert "copyTicket(" not in _full, \
    "a ready-to-send bracket order is back beneath the falsification banner"
print("/full states the falsification before it shows a row, "
      "and does not hand out an order ticket")


# ---- old links must still land somewhere, not 404 ----
for old in ("/brief", "/today"):
    r = c.get(old)
    assert r.status_code in (301, 302, 200), (old, r.status_code)
    print(f"{old} still answers ({r.status_code})")

print("\nNO-RECOMMENDATION AND REACHABILITY PINNED")
