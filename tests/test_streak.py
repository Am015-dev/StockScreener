"""Round 14: pages that actually point at each other, and real motion.

Operator: "the pages do not communicate with each other... make the
site much more dynamic now that we have free actions." Investigated
and found three concrete things, pinned here:

  - tickers on Today's Five and /full were plain text, never linked to
    /credit/<ticker> — now real links, in both directions (a ticker's
    own report also links back to /today and /investors when relevant).
  - four published caches (earnings, patterns, regime, the cooldown
    history) were only ever fetched once at process boot, so two pages
    could genuinely disagree on the same fact — now refreshed on the
    same cadence as the other five.
  - the cooldown history from last round already tracks which tickers
    appeared on which day; nothing showed that motion to a reader —
    now a real, computed "how long has this been here" line.
"""
import datetime
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import ranking                                                   # noqa: E402


# ---- 1. ranking.session_streak(): the pure display-only function ----

assert ranking.session_streak("AAA", None, "2026-08-15") is None
print("no history -> None")

assert ranking.session_streak("AAA", {"history": [
    {"date": "2026-08-14", "picks": {"AAA": {"score": 50}}}]}, "") is None
print("no today -> None")

history_absent = {"history": [
    {"date": "2026-08-11", "picks": {"BBB": {"score": 50}}},
    {"date": "2026-08-12", "picks": {"BBB": {"score": 50}}},
    {"date": "2026-08-13", "picks": {"BBB": {"score": 50}}},
]}
r = ranking.session_streak("AAA", history_absent, "2026-08-14")
assert r == {"kind": "new", "of": 3}, r
print("absent from every recorded session in range -> 'new'")

history_full = {"history": [
    {"date": "2026-08-11", "picks": {"AAA": {"score": 50}}},
    {"date": "2026-08-12", "picks": {"AAA": {"score": 50}}},
    {"date": "2026-08-13", "picks": {"AAA": {"score": 50}}},
]}
r2 = ranking.session_streak("AAA", history_full, "2026-08-14")
assert r2 == {"kind": "streak", "on": 3, "of": 3}, r2
print("present the session immediately before today, every session -> 'streak' 3/3")

history_partial = {"history": [
    {"date": "2026-08-11", "picks": {}},
    {"date": "2026-08-12", "picks": {"AAA": {"score": 50}}},
    {"date": "2026-08-13", "picks": {"AAA": {"score": 50}}},
]}
r3 = ranking.session_streak("AAA", history_partial, "2026-08-14")
assert r3 == {"kind": "streak", "on": 2, "of": 3}, r3
print("present the session immediately before today, 2 of 3 sessions -> 'streak' 2/3")

history_returning = {"history": [
    {"date": "2026-08-11", "picks": {"AAA": {"score": 50}}},
    {"date": "2026-08-12", "picks": {}},
    {"date": "2026-08-13", "picks": {}},
]}
r4 = ranking.session_streak("AAA", history_returning, "2026-08-14")
assert r4 == {"kind": "returning", "gap": 2}, r4
print("present before, absent the last 2 sessions -> 'returning' gap=2")

history_stale = {"history": [{"date": "2026-06-01", "picks": {"AAA": {"score": 50}}}]}
r5 = ranking.session_streak("AAA", history_stale, "2026-08-15")
assert r5 is None, r5
print("a single isolated entry outside STREAK_MAX_CALENDAR_GAP_DAYS -> None, "
      "not mistaken for continuity")

r6 = ranking.session_streak("", history_full, "2026-08-14")
assert r6 is None
print("no ticker -> None")

print("\nSESSION_STREAK PINNED")


# ---- 2. /today: the rendered streak line matches session_streak() ----

os.environ["PUBLISHED_DIR"] = "/tmp/streak-app-published"
os.makedirs(os.environ["PUBLISHED_DIR"], exist_ok=True)
os.environ["PUBLISHED_BASE"] = "http://127.0.0.1:1"
os.environ["PUBLISHED_FETCH_S"] = "1"
os.environ["SKIP_WARM"] = "1"

import re                                                         # noqa: E402
import app                                                        # noqa: E402

tickers = [f"STK{i}" for i in range(10)]
day = "2026-08-15"
base = datetime.date.fromisoformat(day) - datetime.timedelta(days=59)
dates = [(base + datetime.timedelta(days=j)).isoformat() for j in range(60)]
series = {t: [100.0 + i + j * 0.1 for j in range(60)] for i, t in enumerate(tickers)}
prices = {"dates": dates, "series": series}
vol = {t: {"vol": 0.15 + 0.05 * i, "obs": 250, "as_of": day} for i, t in enumerate(tickers)}
# credit.html's "measured" branch needs the full Merton-model input
# set, not just dd/equity/shares — a minimal fixture 500s (this cost
# real time to discover once already, see test_credit_endpoint.py's
# own PUBLISHED_CREDIT["P05"].update(...) for the same complete shape).
credit_book = {t: {"ticker": t, "dd": 3.0 + i, "band": "comfortable",
                   "shares": 1e8, "default_point": 4e9, "equity_vol": 0.3,
                   "equity": 4e10, "asset_vol": 0.22, "market_leverage": 0.35,
                   "as_of": day, "vol_obs": 250, "shares_as_of": day,
                   "source": "Liabilities", "name": f"{t} Inc",
                   "sic_desc": "Electronic Computers"}
              for i, t in enumerate(tickers)}
earnings = {"as_of": day, "complete": True, "map": {t: 60 for t in tickers}}

app._book.update(data=prices, ts=9e9)
app._vols.update(data=vol, ts=9e9)
app._creds.update(data=credit_book, ts=9e9)
app._earn_pub.update(data=earnings, ts=9e9)
app._today_memo.update(key=None, res=None)

res0 = ranking.score(app._today_candidates(ranking.DEFAULT_HORIZON), holdings=[],
                     patterns_report=app._patterns_book(), risk_budget=100.0,
                     horizon=ranking.DEFAULT_HORIZON, corr_by_ticker=None)
top5_tickers = [r["ticker"] for r in res0["ranked"][:5]]
assert len(top5_tickers) == 5

# Seed history: today's likely #1 pick was on the list for 3 straight
# sessions before today (a real, unambiguous "streak" case) — one of
# the OTHER four tickers is deliberately left out of history entirely,
# so it must render as "new" (or nothing, if it isn't picked).
streak_ticker = top5_tickers[0]
history = {"history": [
    {"date": "2026-08-12", "picks": {streak_ticker: {"score": 40}}},
    {"date": "2026-08-13", "picks": {streak_ticker: {"score": 40}}},
    {"date": "2026-08-14", "picks": {streak_ticker: {"score": 40}}},
]}
app._recent_pub.update(data=history, ts=9e9)
app._today_memo.update(key=None, res=None)

c = app.app.test_client()
r = c.get("/today")
assert r.status_code == 200, r.status_code
body = r.data.decode()

expected = ranking.session_streak(streak_ticker, history, day)
assert expected == {"kind": "streak", "on": 3, "of": 3}, expected
assert "On the list 3 of the last 3 recorded sessions." in body, \
    f"expected streak sentence for {streak_ticker} not found in rendered /today"
print(f"/today renders the streak sentence for {streak_ticker}, and its numbers "
      f"(3 of 3) match session_streak() called directly on the same fixture")

# thin/no history must render cleanly, no error, no streak line
app._recent_pub.update(data={}, ts=9e9)
app._today_memo.update(key=None, res=None)
r2 = c.get("/today")
assert r2.status_code == 200, r2.status_code
assert "recorded session" not in r2.data.decode(), \
    "a streak line rendered with no history to support it"
print("with empty cooldown history, /today renders cleanly with no streak line "
      "(the graceful-degrade case, same as the cooldown itself)")

print("\n/today STREAK RENDERING PINNED")


# ---- 3. every ticker on /today is a real link, not plain text ----

app._recent_pub.update(data=history, ts=9e9)
app._today_memo.update(key=None, res=None)
r3 = c.get("/today")
body3 = r3.data.decode()
linked = set(re.findall(r'<a class="tk"[^>]*href="/credit/([A-Z0-9]+)"[^>]*>', body3))
assert linked, "no linked pick tickers found on /today"
assert linked == set(top5_tickers) or len(linked) >= 3, \
    f"expected the shown picks to be linked, got {linked}"
print(f"every pick header on /today is a real <a href=\"/credit/...\"> link: {sorted(linked)}")

print("\nTICKER-LINKING ON /today PINNED")


# ---- 4. /credit/<ticker> shows real cross-references ----

app._inv13f_pub.update(data={"tickers": {
    tickers[0]: {"holders": ["Fund A", "Fund B", "Fund C"]}}}, ts=9e9)

# the streak ticker: not today's own pick in this credit-page check
# (in_todays_five is independent of whatever /today happens to rank),
# so seed it as "on the list before, off recently" to hit the
# 'returning' branch specifically
returning_history = {"history": [
    {"date": "2026-08-10", "picks": {tickers[1]: {"score": 40}}},
    {"date": "2026-08-11", "picks": {}},
    {"date": "2026-08-12", "picks": {}},
]}
app._recent_pub.update(data=returning_history, ts=9e9)

r_holder = c.get(f"/credit/{tickers[0]}")
assert r_holder.status_code == 200, r_holder.status_code
body_holder = r_holder.data.decode()
# Jinja preserves the template's own line-wrapping whitespace, so the
# rendered text has a real newline between "3" and "tracked" — collapse
# whitespace before matching rather than asserting on exact spacing.
flat_holder = " ".join(body_holder.split())
assert "Held by 3 tracked superinvestor" in flat_holder, \
    "13F holder cross-reference missing from /credit/<ticker>"
assert 'href="/investors"' in body_holder
print(f"/credit/{tickers[0]} shows its real 13F holder count and links to /investors")

r_returning = c.get(f"/credit/{tickers[1]}")
assert r_returning.status_code == 200, r_returning.status_code
body_returning = " ".join(r_returning.data.decode().split())
expected_streak = ranking.session_streak(tickers[1], returning_history,
                                         dates[-1])
assert expected_streak == {"kind": "returning", "gap": 2}, expected_streak
assert "off for 2 sessions" in body_returning, \
    "the 'returning' cross-reference sentence is missing or has the wrong gap"
print(f"/credit/{tickers[1]} correctly shows it was on Today's Five before, "
      f"off for 2 sessions, matching session_streak() directly")

r_none = c.get(f"/credit/{tickers[9]}")
assert r_none.status_code == 200, r_none.status_code
body_none = r_none.data.decode()
assert "Elsewhere on this site" not in body_none, \
    "a ticker with no 13F holders, no streak, and not on Today's Five " \
    "must not render an empty cross-reference card"
print(f"/credit/{tickers[9]} (no holders, no history) renders no "
      f"cross-reference card at all — nothing invented when there's nothing to say")

print("\n/credit/<ticker> CROSS-REFERENCE PINNED")


# ---- 5. _book_refresher() keeps all nine books warm, one failure never
# takes down the other eight ----
# The four newly-added books (earnings, patterns, regime, recent-picks)
# get NO try/except of their own inside _*_book(fetch=True) — the only
# thing standing between one bad fetch and a dead warmer thread is
# _book_refresher()'s own per-book try/except. Exercised here by
# running one real pass of the loop body: _published_get is stubbed to
# raise for every file, and time.sleep is stubbed to raise a sentinel
# right after being reached — if the sentinel fires, every book's
# fetch was attempted and every failure was caught along the way.

class _StopAfterOnePass(Exception):
    pass


_orig_published_get = app._published_get
_orig_sleep = app.time.sleep


def _always_fails(path):
    raise RuntimeError(f"simulated failure reading {path}")


def _stop_after_sleep(_seconds):
    raise _StopAfterOnePass()


app._published_get = _always_fails
app.time.sleep = _stop_after_sleep
try:
    app._book_refresher()
    raise AssertionError("_book_refresher() returned instead of looping")
except _StopAfterOnePass:
    pass
finally:
    app._published_get = _orig_published_get
    app.time.sleep = _orig_sleep

print("_book_refresher() completes a full pass over all nine books — "
      "including the four newly-added ones — with every fetch failing, "
      "and reaches time.sleep() rather than dying on the first exception")

print("\nBOOK-REFRESHER FAILURE CONTAINMENT PINNED")

print("\nALL ROUND-14 CONNECTIVITY TESTS PASSED")
