"""The home page must render whatever ends up in a results row.

Rows arrive from a scan, a CSV reload, a published payload and a snapshot
mirrored out of someone's browser. Any of them can be a schema older than
the running code. A 500 here is total unavailability: the whole product is
one page.
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
    if c.get("/").status_code != 200:
        bad.append((fld, repr(junk)))

# whole rows of junk, and missing keys
for _ in range(400):
    row = {f: random.choice(JUNK) for f in random.sample(FIELDS, random.randint(0, 8))}
    A._state.update(results=[row], top_picks=[row], pending=[row],
                    results_ts=random.choice([None, time.time(), 0]),
                    status="done")
    n += 1
    if c.get("/").status_code != 200:
        bad.append(("random", repr(row)[:120]))

print(f"{n} corrupted-row renders")
if bad:
    print(f"FAILED {len(bad)}:")
    for b in bad[:12]: print("   ", b)
    sys.exit(1)
print("the home page rendered every one of them")

# ---- the table's columns must line up with its header ----
# Adding the credit column replaced the RSI cell instead of sitting beside
# it, so every row was one short and every value after RSI displayed under
# the wrong heading. Nothing else in the suite would have noticed.
import re as _re

A._state.update(results=[dict(GOOD, ticker=f"T{i}") for i in range(3)],
                top_picks=[GOOD], pending=[], results_ts=time.time(),
                status="done")
_html = c.get("/").get_data(as_text=True)
_tbl = _re.search(r'<table class="wl".*?</table>', _html, _re.S)
assert _tbl, "the watchlist table did not render"
_rows = _re.findall(r"<tr[^>]*>(.*?)</tr>", _tbl.group(0), _re.S)
_head = len(_re.findall(r"<th", _rows[0]))
assert _head >= 7, _head
for _r in _rows[1:]:
    _n = len(_re.findall(r"<td", _r))
    assert _n == _head, f"{_n} cells under {_head} headings — the columns are shifted"
print(f"every row has {_head} cells under {_head} headings")

print("\nPAGE ROBUSTNESS PINNED")


# ---- the credit report must be reachable from the front page ----
# It was not. The report rendered, the route answered 200, and the only
# link to it on the home page was inside <details> — collapsed, so the
# reader never saw it and told me the report did not exist. This asserts
# on the page as served, above that collapsed section, because that is
# where the claim "a reader can find it" is either true or false.
_BOOK = {f"CR{i}": {"dd": 1.0 + i, "band": "comfortable"} for i in range(6)}
_saved_cb = A._credit_book
A._credit_book = lambda fetch=False: _BOOK
try:
    A._state.update(results=[dict(GOOD, ticker=f"T{i}") for i in range(3)],
                    top_picks=[GOOD], pending=[], results_ts=time.time(),
                    status="done")
    _html = c.get("/").get_data(as_text=True)
    _above = _html.split("<details")[0]
    _links = _re.findall(r'href="/credit/([A-Z0-9.\-]+)"', _above)
    assert _links, "no link to a credit report before the collapsed section"
    assert len(set(_links)) >= 4, sorted(set(_links))
    print(f"{len(set(_links))} credit reports are one click away, above the fold")

    # and the lookup box, which is the path for a company that is not on
    # today's board at all — which is most of them
    assert 'id="crTicker"' in _above and 'id="crGo"' in _above
    assert "/credit/" in _html.split("openCredit")[1][:300]
    print("any company can be looked up by name, not just today's picks")
finally:
    A._credit_book = _saved_cb

# with an empty book the card is still there, and still says something
# true — an absence of measurements must not read as an absence of the
# feature
A._credit_book = lambda fetch=False: {}
try:
    _html = c.get("/").get_data(as_text=True)
    assert 'id="crTicker"' in _html.split("<details")[0]
    assert "No company has been measured yet" in _html
    print("with nothing measured the way in is still there, and says why it is empty")
finally:
    A._credit_book = _saved_cb

print("\nCREDIT REPORT REACHABILITY PINNED")


# ---- the page must not hide itself behind a question ----
# This is what "I do not see any Moodys like report anywhere" actually
# was. #main shipped display:none and only saving holdings or clicking
# skip revealed it — and the skip lived in sessionStorage, so it was
# forgotten when the tab closed. A visitor arriving on a phone saw one
# question and a blank page under it: no brief, no pre-trade check, no
# credit reports. Rendered it in a real browser to find that out, which
# is the only way a display:none is visible at all.
_html = c.get("/").get_data(as_text=True)

assert '<div id="main">' in _html, \
    "the page's own content must not ship hidden"
assert 'id="main" style="display:none"' not in _html
print("the page does not ship with its content hidden")

# and nothing may hide it afterwards either
_js = _html.split("<script")[-1]
import re as _re2
for _m in _re2.finditer(r'main\.style\.display\s*=\s*([^;\n]+)', _js):
    assert _m.group(1).strip().strip('"') == "block", \
        f"something can still hide the page: {_m.group(0)}"
print("and no code path sets it back to hidden")

# the dismissal has to outlive the tab, or the question returns forever
assert 'localStorage.setItem("dipfinder_skipped"' in _js, \
    "skip must persist across visits, not just the session"
print("skipping the holdings question is remembered on the next visit")


# ---- the page must not recommend a trade its own test falsified ----
# The permutation test ran twice — balanced (p = 0.50, 25 seeds, 376
# stocks) and wide-net (p = 0.41, 40 seeds, 657 stocks) — and coin-flip
# entry did as well or better both times. The headline used to be
# "Closest to the setup today: <ticker>", with the refutation in small
# print underneath. A tool that headlines a number it has proven means
# nothing is a toy; the pick is gone, and this pins it gone.
A._state.update(results=[dict(GOOD, ticker=f"T{i}") for i in range(3)],
                top_picks=[GOOD], pending=[], results_ts=time.time(),
                status="done")
_html = c.get("/").get_data(as_text=True)
assert "Closest to the setup today" not in _html
assert "Nothing to do today" not in _html
print("no trade is recommended anywhere on the page")

# the falsification is stated WITH the list it applies to, in words
assert "no better than random entry" in _html
assert "Not a recommendation" in _html.replace("\n          ", " ")
print("the pattern list carries its own refutation, in full sentences")

# and the working tools lead: both check inputs sit above the collapsed
# pattern section
_above = _html.split("<details")[0]
for _id in ('id="ckTicker"', 'id="crTicker"'):
    assert _id in _above, f"{_id} is below the fold"
print("the pre-trade check and the credit report lead the page")


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
print("/full states the falsification before it shows a row")


# ---- the page opens with information, not with a question ----
# "What do you own?" as a full card was the first thing every visitor
# saw, and the review's biggest single deduction for value density. The
# ask is now one line above the day's news; the form appears only when
# the reader asks for it.
_html = c.get("/").get_data(as_text=True)
assert 'id="ownInvite"' in _html, "the slim invitation must exist"
_i_invite = _html.find('id="ownInvite"')
_i_gate = _html.find('id="ownGate"')
assert _i_invite < _i_gate, "the invitation comes before the full form"
assert 'id="ownGate" style="display:none"' in _html, \
    "the full form must not ship visible"
# and the ask itself is short: the old card ran to four paragraphs
_invite = _html[_i_invite:_html.find("</p>", _i_invite)]
import re as _re3
_words = len(_re3.sub(r"<[^>]+>", " ", _invite).split())
assert _words < 40, f"the invitation is {_words} words — it is meant to be a line"
print(f"the holdings ask is a {_words}-word line, and the form waits to be asked for")
