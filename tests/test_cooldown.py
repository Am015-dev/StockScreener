"""Round 13: Today's Five must not show the same names every session.

Operator: "The same companies appear everyday." Investigated and found
the real mechanism: two of the four score components
(`adds to what you own`, `confirmed pattern`) are structurally zero for
an anonymous visitor — no holdings to compare against, and nothing has
yet survived this project's own pattern holdout (/patterns says so
outright: "None of the 33 patterns tested is worth trading"). Ranking
on the other two alone, both slow-moving fundamentals, mechanically
floats the same handful of ultra-stable blue chips to the top for
weeks. This file pins the fix at all three layers: the pure selection
function, the scheduled scan that records what was actually shown, and
the live page that reads that history back.
"""
import datetime
import json
import os
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

import ranking                                                   # noqa: E402


# ---- 1. ranking.select_daily_five(): the pure cooldown logic ----

def _row(t, score):
    return {"ticker": t, "score": score}


ranked = [_row(t, s) for t, s in
         (("A", 90), ("B", 80), ("C", 70), ("D", 60), ("E", 50),
          ("F", 40), ("G", 30))]

out = ranking.select_daily_five(ranked, None, "2026-08-15", 60.0)
assert [r["ticker"] for r in out] == ["A", "B", "C", "D", "E"]
print("no history: identical to ranked[:5]")

history = {"history": [
    {"date": "2026-08-13", "picks": {t: {"score": s} for t, s in
                                     (("A", 90), ("B", 80), ("C", 70),
                                      ("D", 60), ("E", 50))}},
    {"date": "2026-08-14", "picks": {t: {"score": s} for t, s in
                                     (("A", 90), ("B", 80), ("C", 70),
                                      ("D", 60), ("E", 50))}},
]}
out = ranking.select_daily_five(ranked, history, "2026-08-15", 60.0)
tickers = [r["ticker"] for r in out]
assert "F" in tickers and "G" in tickers
assert not next(r for r in out if r["ticker"] == "F").get("cooldown_backfill")
assert next(r for r in out if r["ticker"] == "A").get("cooldown_backfill") is True
print("A-E cooling (shown unchanged the last 2 sessions): F and G (fresh) "
      "fill the list first, cooling names backfill only what's left over")

ranked_moved = [_row(t, s) for t, s in
               (("A", 40), ("B", 80), ("C", 70), ("D", 60), ("E", 50),
                ("F", 40), ("G", 30))]
out2 = ranking.select_daily_five(ranked_moved, history, "2026-08-15", 60.0)
a = next(r for r in out2 if r["ticker"] == "A")
assert a.get("cooldown_waived") is True
print("A's score moved by a material amount since it was last shown "
      "(90 -> 40): the cooldown is waived, not enforced blindly")

stale_history = {"history": [{"date": "2026-07-01", "picks": {"A": {"score": 90}}}]}
out3 = ranking.select_daily_five(ranked, stale_history, "2026-08-15", 60.0)
assert [r["ticker"] for r in out3] == ["A", "B", "C", "D", "E"]
print("a history entry outside the calendar window (45 days old, an "
      "isolated backfilled entry) has no effect — it is not mistaken for "
      "'the most recent session' just because nothing newer exists")

out4 = ranking.select_daily_five(ranked, history, "", 60.0)
assert [r["ticker"] for r in out4] == ["A", "B", "C", "D", "E"]
print("no price date available: falls back to plain ranked[:5], never errors")

print("\nSELECT_DAILY_FIVE PINNED")


# ---- 2. ranking.build_candidates(): the refactored candidate builder ----
# app.py's _today_candidates() used to build this inline; it is now a thin
# wrapper. Pinned here so a future edit to either side cannot silently
# drift the two apart (which would surface first in whichever caller is
# tested less — exactly the failure mode this refactor exists to avoid).

series = {"AAA": [100.0] * 25, "THIN": [50.0] * 10}   # THIN: under 20 closes
vols = {"AAA": {"vol": 0.3, "obs": 200}}
creds = {"AAA": {"dd": 7.0, "equity": 5e10, "name": "Triple A Inc",
                 "sector": "Industrials"}}
liq = {"AAA": {"adv_usd": 100e6}}
earn = {"AAA": 30}
cands = ranking.build_candidates(series, vols, creds, liq, earn, True,
                                 earn_single_source={"AAA"},
                                 region_of=lambda t: "US")
assert len(cands) == 1, "THIN (under 20 closes) must be excluded"
c = cands[0]
assert c["ticker"] == "AAA" and c["dd"] == 7.0 and c["market_value"] == 5e10
assert c["days_to_earnings"] == 30 and c["earnings_single_source"] is True
assert c["cal_covered"] is True
print("build_candidates(): a short-history name is excluded, a real one "
      "carries its credit/vol/earnings fields through correctly")

print("\nBUILD_CANDIDATES PINNED")


# ---- 3. scheduled_scan._record_todays_five(): the publisher ----

import scheduled_scan as ss                                      # noqa: E402

tmp_prev = Path(tempfile.mkdtemp(prefix="cooldown-prev-")) / "prev_recent_picks.json"
_ORIG_PREV_PATH = "/tmp/prev_recent_picks.json"


def _write_fixture(out: Path, tickers, base_date, dd_by_ticker=None):
    series = {t: [100.0 + i + j * 0.1 for j in range(60)]
             for i, t in enumerate(tickers)}
    dates = [(base_date + datetime.timedelta(days=j)).isoformat()
            for j in range(60)]
    (out / "prices.json").write_text(json.dumps(
        {"dates": dates, "series": series}))
    (out / "vol.json").write_text(json.dumps(
        {t: {"vol": 0.15 + 0.05 * i, "obs": 250, "as_of": dates[-1]}
         for i, t in enumerate(tickers)}))
    dd_by_ticker = dd_by_ticker or {t: 3.0 + i for i, t in enumerate(tickers)}
    (out / "credit.json").write_text(json.dumps(
        {t: {"ticker": t, "dd": dd_by_ticker[t], "equity": 4e10, "shares": 1e8,
             "band": "comfortable"} for t in tickers}))
    (out / "earnings.json").write_text(json.dumps(
        {"as_of": dates[-1], "complete": True, "map": {t: 60 for t in tickers}}))
    return dates[-1]


tickers8 = [f"T{i}" for i in range(8)]

out1 = Path(tempfile.mkdtemp(prefix="cooldown-run1-"))
day1 = _write_fixture(out1, tickers8, datetime.date(2026, 6, 1))
if Path(_ORIG_PREV_PATH).exists():
    Path(_ORIG_PREV_PATH).unlink()
ss._record_todays_five(out1)
rec1 = json.loads((out1 / "recent_picks.json").read_text())
assert rec1["history"][-1]["date"] == day1
assert len(rec1["history"][-1]["picks"]) == 5
print(f"first run (no prior history): recorded 5 picks for {day1}")

out2 = Path(tempfile.mkdtemp(prefix="cooldown-run2-"))
day2 = _write_fixture(out2, tickers8, datetime.date(2026, 6, 2))
Path(_ORIG_PREV_PATH).write_text(json.dumps(rec1))
ss._record_todays_five(out2)
rec2 = json.loads((out2 / "recent_picks.json").read_text())
today_picks = set(rec2["history"][-1]["picks"])
yesterday_picks = set(rec1["history"][-1]["picks"])
assert today_picks != yesterday_picks, \
    "identical scores day over day must not repeat the identical five names"
assert len(rec2["history"]) == 2
print(f"second run, same fixture (unchanged scores): {sorted(today_picks)} "
      f"differs from day 1's {sorted(yesterday_picks)} — real names, "
      f"never the identical five back to back with nothing having changed")

if Path(_ORIG_PREV_PATH).exists():
    Path(_ORIG_PREV_PATH).unlink()

print("\nSCHEDULED-SCAN RECORDING PINNED")


# ---- 4. app.py's today_page() actually applies the cooldown ----

os.environ["PUBLISHED_DIR"] = str(Path(tempfile.mkdtemp(prefix="cooldown-app-")))
os.makedirs(os.environ["PUBLISHED_DIR"], exist_ok=True)
os.environ["PUBLISHED_BASE"] = "http://127.0.0.1:1"
os.environ["PUBLISHED_FETCH_S"] = "1"
os.environ["SKIP_WARM"] = "1"

import app                                                        # noqa: E402

tickers12 = [f"APP{i}" for i in range(12)]
day = "2026-08-15"
base = datetime.date.fromisoformat(day) - datetime.timedelta(days=59)
dates = [(base + datetime.timedelta(days=j)).isoformat() for j in range(60)]
series = {t: [100.0 + i + j * 0.1 for j in range(60)] for i, t in enumerate(tickers12)}
prices = {"dates": dates, "series": series}
vol = {t: {"vol": 0.15 + 0.05 * i, "obs": 250, "as_of": day} for i, t in enumerate(tickers12)}
credit = {t: {"ticker": t, "dd": 3.0 + i, "equity": 4e10, "shares": 1e8,
             "band": "comfortable"} for i, t in enumerate(tickers12)}
earnings = {"as_of": day, "complete": True, "map": {t: 60 for t in tickers12}}

app._book.update(data=prices, ts=9e9)
app._vols.update(data=vol, ts=9e9)
app._creds.update(data=credit, ts=9e9)
app._earn_pub.update(data=earnings, ts=9e9)

# figure out what the unfiltered top 5 would be, then seed cooldown history
# claiming those exact 5 (with unchanged scores) were shown yesterday
app._today_memo.update(key=None, res=None)
res0 = ranking.score(app._today_candidates(ranking.DEFAULT_HORIZON),
                     holdings=[], patterns_report=app._patterns_book(),
                     risk_budget=100.0, horizon=ranking.DEFAULT_HORIZON,
                     corr_by_ticker=None)
baseline_top5 = {r["ticker"]: r["score"] for r in res0["ranked"][:5]}
assert len(baseline_top5) == 5

yesterday = (datetime.date.fromisoformat(day) - datetime.timedelta(days=1)).isoformat()
app._recent_pub.update(data={"history": [
    {"date": yesterday, "picks": {t: {"score": s} for t, s in baseline_top5.items()}},
]}, ts=9e9)
app._today_memo.update(key=None, res=None)

c = app.app.test_client()
r = c.get("/today")
assert r.status_code == 200, r.status_code
body = r.data.decode()
# <a class="tk" href="...">TICKER</a> is the main pick header; the
# collapsed "credit_index" section further down the page also carries
# class="tk" (on a <b>, for the exact-match list of every measured
# company) — anchored on the tag so this counts only the five picks.
shown = set(__import__("re").findall(r'<a class="tk"[^>]*>([A-Z0-9]+)</a>', body))
assert shown, "no picks rendered"
assert shown != set(baseline_top5), \
    ("/today showed the identical five names the day after they were "
     f"already shown with unchanged scores: {shown}")
print(f"/today with yesterday's unfiltered top 5 seeded as cooldown history: "
      f"today shows {sorted(shown)}, not the identical {sorted(baseline_top5)}")


print("\nAPP.PY COOLDOWN WIRING PINNED")

print("\nALL COOLDOWN TESTS PASSED")
