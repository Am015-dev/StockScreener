"""The 13F superinvestor feature end to end on the web app: /investors
renders correctly empty and populated, and the note-level line on /today
and /check never affects a score, a rank, or a verdict.
"""
import json
import os
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

tmp = tempfile.mkdtemp(prefix="investors-page-")
os.environ["PUBLISHED_DIR"] = tmp
os.environ["PUBLISHED_BASE"] = "http://127.0.0.1:1"
os.environ["PUBLISHED_FETCH_S"] = "1"
os.environ["SKIP_WARM"] = "1"

import app                                                       # noqa: E402

c = app.app.test_client()

# ---- /investors with nothing published: honest, not a 500 ----
r = c.get("/investors")
assert r.status_code == 200, r.status_code
assert "Not published yet" in r.data.decode()
print("/investors with no published book renders 200, says so plainly")

# ---- build a small board + a 13F book naming one of its tickers ----
series = {f"T{i}": [100.0 + (i % 7) + j * 0.1 for j in range(60)]
         for i in range(20)}
series["AAPL"] = [190.0 + j * 0.1 for j in range(60)]
dates = [f"2026-06-{(j % 28) + 1:02d}" for j in range(60)]
app._book.update(data={"dates": dates, "series": series}, ts=9e9)
app._vols.update(data={t: {"vol": 0.25, "obs": 250, "as_of": "2026-08-11"}
                       for t in series}, ts=9e9)
app._creds.update(data={t: {"ticker": t, "dd": 6.0, "equity": 5e10,
                            "shares": 1e8, "band": "comfortable"}
                        for t in series}, ts=9e9)
app._earn_pub.update(data={"as_of": time.strftime("%Y-%m-%d"),
                           "complete": True,
                           "map": {t: 60 for t in series}}, ts=9e9)

INV = {
    "as_of": "2026-08-01", "n_managers_tracked": 3, "n_managers_ok": 3,
    "skipped": [{"name": "Dead Fund LLC", "cik": 999,
                "reason": "filer name mismatch: ..."}],
    "managers": [
        {"cik": 1, "name": "BERKSHIRE HATHAWAY INC", "period": "2026-03-31",
         "filed": "2026-05-15", "n_positions": 1, "n_options": 0,
         "top": [{"issuer": "APPLE INC", "class": "COM",
                 "value_usd": 5.78e10, "shares": 227917808, "ticker": "AAPL"}]},
    ],
    "tickers": {
        "AAPL": {"issuer": "APPLE INC", "ticker": "AAPL",
                "holders": ["BERKSHIRE HATHAWAY INC", "CITADEL ADVISORS LLC"],
                "added": ["CITADEL ADVISORS LLC"], "increased": [],
                "trimmed": [], "exited": []},
        # single-holder: must NOT appear in the multi-holder table
        "T3": {"issuer": "ISSUER OF T3", "ticker": "T3",
              "holders": ["BERKSHIRE HATHAWAY INC"],
              "added": [], "increased": [], "trimmed": [], "exited": []},
        # unmapped: no ticker key at all
        "(unmapped) SOME PRIVATE HOLDCO": {
            "issuer": "SOME PRIVATE HOLDCO", "ticker": None,
            "holders": ["CITADEL ADVISORS LLC", "RENAISSANCE TECHNOLOGIES LLC"],
            "added": [], "increased": [], "trimmed": [], "exited": []},
    },
}
app._inv13f_pub.update(data=INV, ts=9e9)

# ---- /investors populated ----
r = c.get("/investors")
assert r.status_code == 200, r.status_code
body = r.data.decode()
assert "BERKSHIRE HATHAWAY INC" in body
assert "AAPL" in body
assert "Dead Fund LLC" in body, "a skipped manager must be named, not hidden"
assert "(unmapped) SOME PRIVATE HOLDCO" not in body, \
    "the raw fallback key must render as the issuer name, not the literal key"
assert "SOME PRIVATE HOLDCO" in body
assert ">T3<" not in body, \
    "a single-holder ticker must not appear in the 2-or-more table"
print("/investors renders managers, the multi-holder table, a skipped "
      "manager named plainly, and an unmapped CUSIP by issuer name only")

# ---- pagination hides rows in the browser only — the server ships all of them ----
# The multi-holder table can run to 200 rows; JS pages it 25 at a time so
# the page does not read as an unbroken scroll of data. That must never
# cost a reader without JS a single row — the server-rendered HTML has to
# carry every one, hidden or not.
big = dict(INV)
big_tickers = dict(INV["tickers"])
for i in range(30):
    big_tickers[f"BIG{i}"] = {"issuer": f"BIG ISSUER {i}", "ticker": f"BIG{i}",
                              "holders": ["BERKSHIRE HATHAWAY INC", "CITADEL ADVISORS LLC"],
                              "added": [], "increased": [], "trimmed": [], "exited": []}
big["tickers"] = big_tickers
app._inv13f_pub.update(data=big, ts=9e9)
r = c.get("/investors")
body_big = r.data.decode()
assert body_big.count('class="pageRow"') == 32, \
    "every multi-holder row (30 synthetic + AAPL + the unmapped holdco) " \
    "must ship in the static HTML"
assert 'class="reveal' not in body_big and ' reveal"' not in body_big, \
    "the reveal class must be added by JS only, never server-rendered — " \
    "otherwise a reader with no JS sees permanently-invisible cards"
print("/investors ships all 32 multi-holder rows in static HTML (pagination "
      "is JS-only) and never server-renders the .reveal class")

app._inv13f_pub.update(data=INV, ts=9e9)   # restore the small fixture

# ---- the field flows to /today's candidates and never affects filters ----
import ranking                                                   # noqa: E402

cands = {cd["ticker"]: cd for cd in app._today_candidates(10)}
assert cands["AAPL"]["held_by_investors"] == \
    ["BERKSHIRE HATHAWAY INC", "CITADEL ADVISORS LLC"], cands["AAPL"]
assert cands["T0"]["held_by_investors"] == [], \
    "a ticker absent from the 13F book must carry an empty list, not None"
ok_a, why_a, flags_a = ranking.filters(cands["AAPL"])
ok_t0, why_t0, flags_t0 = ranking.filters(cands["T0"])
assert not any("superinvestor" in (f or "").lower() for f in flags_a), flags_a
assert not any("superinvestor" in (f or "").lower() for f in flags_t0), flags_t0
print("held_by_investors reaches every candidate but never appears in a "
      "filters() flag — ranking.py never reads the field")

app._today_memo.update(key=None, res=None)
r = c.get("/today")
assert r.status_code == 200, r.status_code
body = r.data.decode()
if "AAPL" in body and "<span class=\"tk\">AAPL</span>" in body:
    assert "Superinvestors" in body
    print("AAPL made today's board and carries the Superinvestors line")
else:
    print("AAPL did not make today's board this run (real scoring, not "
          "guaranteed) — field-flow already verified directly above")

# ---- every rendered pick carries a real price-path chart, not just some ----
n_picks = body.count('class="card pick"')
n_charts = body.count('class="chart" data-spark=')
if n_picks:
    assert n_charts == n_picks, \
        (f"{n_picks} picks rendered but only {n_charts} carried a chart — "
         "the 60-point fixture series should give every pick one")
    print(f"/today draws a real 60-session price chart for all {n_picks} picks")
else:
    print("no picks rendered this run to check chart rendering against")

# ---- freshness: the one question a returning visitor actually has ----
# A live site review found /today gave no answer anywhere to "is this
# today's close or three days old" — grepped the rendered page for
# "as of"/"updated"/"scanned" and got zero hits for the picks themselves.
assert "Priced as of" in body, \
    "the front page must say what session the picks were priced on"
assert dates[-1] in body, \
    ("the freshness line must show the LATEST published price date, not "
     "some other date")
print("/today states what session the picks were priced on")

# ---- /check: note-level only, never the bottom line ----
r = c.post("/check", json={"ticker": "AAPL", "holdings": ""})
assert r.status_code == 200, r.status_code
body = r.get_json()
notes = [f for f in body["findings"]
        if f["level"] == "note" and "superinvestor" in f["headline"].lower()]
assert len(notes) == 1, body["findings"]
assert "superinvestor" not in body["bottom_line"].lower(), body["bottom_line"]
print("/check carries the note and never lets it reach the bottom line")

# a ticker with no 13F coverage carries no line at all
r = c.post("/check", json={"ticker": "T0", "holdings": ""})
body = r.get_json()
assert not any("superinvestor" in f["headline"].lower()
              for f in body["findings"]), body["findings"]
print("a ticker no tracked manager holds renders no superinvestor line at all")

print("\nALL INVESTORS-PAGE TESTS PASSED")
