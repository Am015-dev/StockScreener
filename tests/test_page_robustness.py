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
