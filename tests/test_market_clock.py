"""Freshness must be measured in trading sessions, not wall-clock hours.

A visitor opened the site on a Saturday and reported it as broken. It was
not: Friday's close is the newest data that exists until Monday. But the
page counted hours, so it labelled the freshest possible view "10 hours
old", coloured it amber, and told the reader to press Run — which would
have re-scanned a closed market. An age counter that keeps ticking
through a weekend is measuring the wrong thing.

These tests pin the distinction, and pin the guard that stops the module
inventing a trading calendar it does not have.
"""
import datetime as dt
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TMP = tempfile.mkdtemp(prefix="clock_")
os.environ.setdefault("MARKET_DB", os.path.join(TMP, "m.db"))
os.environ.setdefault("SCREENER_CACHE_DB", os.path.join(TMP, "c.db"))
sys.path.insert(0, str(ROOT))

import market_clock as mc

U = dt.timezone.utc

# ---- the state machine over a full week ----
cases = [
    # (utc datetime, expected state)
    (dt.datetime(2026, 8, 7, 12, 0, tzinfo=U), "premarket"),   # Fri 08:00 ET
    (dt.datetime(2026, 8, 7, 18, 0, tzinfo=U), "open"),        # Fri 14:00 ET
    (dt.datetime(2026, 8, 7, 21, 30, tzinfo=U), "afterhours"), # Fri 17:30 ET
    (dt.datetime(2026, 8, 8, 8, 0, tzinfo=U), "weekend"),      # Sat
    (dt.datetime(2026, 8, 9, 23, 0, tzinfo=U), "weekend"),     # Sun
    (dt.datetime(2026, 8, 10, 14, 0, tzinfo=U), "open"),       # Mon 10:00 ET
    (dt.datetime(2026, 9, 7, 15, 0, tzinfo=U), "holiday"),     # Labor Day
    (dt.datetime(2026, 11, 26, 15, 0, tzinfo=U), "holiday"),   # Thanksgiving
]
for when, expect in cases:
    got = mc.state(when)
    assert got["state"] == expect, f"{when}: expected {expect}, got {got['state']}"
    assert got["label"], f"{when}: every state must carry a readable sentence"
assert mc.state(dt.datetime(2026, 9, 7, 15, 0, tzinfo=U))["holiday"] == "Labor Day"
print(f"market state correct across {len(cases)} points spanning a week, "
      f"two holidays and both sides of the bell")

# ---- DST: the same wall-clock hour is a different market state ----
# 14:00 UTC is 10:00 ET in summer (open) but 09:00 ET in winter (premarket).
summer = mc.state(dt.datetime(2026, 8, 10, 14, 0, tzinfo=U))
winter = mc.state(dt.datetime(2026, 12, 10, 14, 0, tzinfo=U))
assert summer["state"] == "open", summer
assert winter["state"] == "premarket", winter
print("daylight saving handled: 14:00 UTC is open in August, premarket in December")

# ---- the weekend must add zero sessions ----
fri_close = dt.datetime(2026, 8, 7, 20, 0, tzinfo=U).timestamp()   # Fri 16:00 ET
for when, sessions, stale in [
    (dt.datetime(2026, 8, 8, 8, 0, tzinfo=U), 0, False),    # Sat
    (dt.datetime(2026, 8, 9, 23, 0, tzinfo=U), 0, False),   # Sun
    (dt.datetime(2026, 8, 10, 14, 0, tzinfo=U), 0, False),  # Mon, session live
    (dt.datetime(2026, 8, 11, 14, 0, tzinfo=U), 1, False),  # Tue
    (dt.datetime(2026, 8, 13, 14, 0, tzinfo=U), 3, True),   # Thu
]:
    f = mc.staleness(fri_close, when)
    assert f["sessions"] == sessions, (when, f)
    assert f["stale"] is stale, (when, f)
assert "Friday" in mc.staleness(fri_close, dt.datetime(2026, 8, 8, 8, 0, tzinfo=U))["phrase"]
print("a Friday scan is 0 sessions old all weekend, 1 on Tuesday, stale by Thursday")

# and the specific regression: the exact moment the visitor saw
sat = mc.staleness(fri_close, dt.datetime(2026, 8, 8, 8, 8, tzinfo=U))
assert not sat["stale"], "Friday's close on a Saturday morning is not stale"
assert sat["market"]["state"] == "weekend"
assert "closed for the weekend" in sat["market"]["label"]
print(f"the reported case now reads: {sat['market']['label']!r} ({sat['phrase']})")

# ---- a holiday weekend is three days and still zero sessions ----
# Friday 4 Sep 2026 close, read on Labor Day Monday 7 Sep.
sep_fri = dt.datetime(2026, 9, 4, 20, 0, tzinfo=U).timestamp()
f = mc.staleness(sep_fri, dt.datetime(2026, 9, 7, 15, 0, tzinfo=U))
assert f["sessions"] == 0 and not f["stale"], f
print("Labor Day weekend: 3 calendar days, 0 sessions, not stale")

# ---- session arithmetic ----
assert mc.is_session(dt.date(2026, 8, 7))          # Friday
assert not mc.is_session(dt.date(2026, 8, 8))      # Saturday
assert not mc.is_session(dt.date(2026, 9, 7))      # Labor Day
assert mc.previous_session(dt.date(2026, 8, 9)) == dt.date(2026, 8, 7)
assert mc.sessions_between(dt.date(2026, 8, 7), dt.date(2026, 8, 10)) == 1
assert mc.sessions_between(dt.date(2026, 8, 7), dt.date(2026, 8, 7)) == 0
print("session arithmetic: weekends and holidays skipped, same-day is zero")

# ---- past the known calendar the module must refuse to guess ----
# dynamic, not a fixed date — the calendar's actual bound moves forward
# on every process start (or falls back to the hand-typed table's fixed
# end), so "beyond" must always be computed relative to it, not pinned to
# a date that could eventually fall INSIDE a future bound
beyond_date = mc.KNOWN_THROUGH + dt.timedelta(days=60)
beyond = dt.datetime(beyond_date.year, beyond_date.month, beyond_date.day, 15, 0, tzinfo=U)
st = mc.state(beyond)
assert st["state"] == "unknown", st
assert st["last_close_ts"] is None
assert "not known past" in st["label"]
f = mc.staleness(fri_close, beyond)
assert f["sessions"] is None and f["stale"], \
    "an unknown calendar must fail closed — stale, not silently fresh"
print(f"past {mc.KNOWN_THROUGH}: reports 'unknown' and fails closed rather than "
      f"assuming every weekday trades")

# ---- the holiday table must not be quietly empty ----
assert len(mc.HOLIDAYS) >= 18, len(mc.HOLIDAYS)
assert mc.KNOWN_THROUGH >= dt.date(2027, 12, 1), mc.KNOWN_THROUGH
print(f"{len(mc.HOLIDAYS)} holidays known through {mc.KNOWN_THROUGH}")

# ---- half-days: the reason exchange_calendars replaced the fixed table ----
# Only meaningful when the library actually loaded — the hand-typed
# HOLIDAYS table has never modelled half-days and is not expected to.
if mc._calendar() is not None:
    # the day after Thanksgiving, 27 Nov 2026, closes at 1pm ET (13:00),
    # not the regular 4pm — 13:30 ET must already read as afterhours
    black_friday_1330et = dt.datetime(2026, 11, 27, 18, 30, tzinfo=U)
    st = mc.state(black_friday_1330et)
    assert st["state"] == "afterhours", st
    assert "early close" in st["label"], st["label"]
    print(f"day-after-Thanksgiving half day: {st['label']!r}")

    # Christmas Eve 2026, also a half day
    xmas_eve_1330et = dt.datetime(2026, 12, 24, 18, 30, tzinfo=U)
    st = mc.state(xmas_eve_1330et)
    assert st["state"] == "afterhours", st
    print("Christmas Eve half day: also reads afterhours by 1:30pm ET")

    # a half day still counts as exactly one session — staleness must not
    # double-count it or skip it
    thu_before = dt.datetime(2026, 11, 26, 21, 0, tzinfo=U).timestamp()  # Thanksgiving close (n/a, holiday)
    wed_close = dt.datetime(2026, 11, 25, 21, 0, tzinfo=U).timestamp()   # Wed 25 Nov close
    f = mc.staleness(wed_close, dt.datetime(2026, 11, 30, 15, 0, tzinfo=U))  # Monday after
    assert f["sessions"] == 1, f   # only Fri 27 Nov traded between them (Thu was Thanksgiving)
    print(f"the half day counts as exactly one session: {f['sessions']}")
else:
    print("exchange_calendars not importable in this environment — "
          "half-day pins skipped, fallback path exercised below instead")

# ---- the fallback path, forced, must reproduce the pre-library behaviour ----
_saved_cal, _saved_tried = mc._CAL, mc._CAL_TRIED
try:
    mc._CAL, mc._CAL_TRIED = None, True   # pretend the import failed
    assert mc.is_session(dt.date(2026, 8, 7))
    assert not mc.is_session(dt.date(2026, 8, 8))
    assert not mc.is_session(dt.date(2026, 9, 7))          # Labor Day
    assert mc.previous_session(dt.date(2026, 8, 9)) == dt.date(2026, 8, 7)
    st = mc.state(dt.datetime(2026, 8, 7, 18, 0, tzinfo=U))
    assert st["state"] == "open", st
    # the fallback cannot see half-days — this is the documented gap
    st = mc.state(dt.datetime(2026, 11, 27, 18, 30, tzinfo=U))
    assert st["state"] == "open", \
        "the fallback path treats a half day as a full session — expected"
finally:
    mc._CAL, mc._CAL_TRIED = _saved_cal, _saved_tried
print("fallback path (library unavailable) reproduces the original "
      "weekday/holiday logic, including its known half-day blind spot")

print("\nALL MARKET-CLOCK TESTS PASSED")


# ---- "the current session" must mean the scan's prices are from it ----
# The phrase branched on whether a market happened to be open while the
# reader looked, not on whether the scan came from that session. Friday's
# closes read at 10:00 Monday were described as live, and a scan run
# premarket (which can only hold the previous close) was dated to the day
# it ran, so it still claimed to be zero sessions old after a full session
# had traded.
import datetime as _d

FRI_CLOSE = _d.datetime(2026, 8, 7, 20, 30, tzinfo=_d.timezone.utc)   # 16:30 ET
MON_OPEN = _d.datetime(2026, 8, 10, 14, 0, tzinfo=_d.timezone.utc)    # 10:00 ET

st = mc.staleness(FRI_CLOSE.timestamp(), now_utc=MON_OPEN)
assert st["market"]["is_open"] is True, st["market"]
assert "current session" not in st["phrase"], st["phrase"]
assert "Friday" in st["phrase"], st["phrase"]
print(f"Friday's closes read on Monday morning: {st['phrase']!r}, not 'current'")

# a scan taken during today's session, read during that same session, IS live
MON_MID = _d.datetime(2026, 8, 10, 17, 0, tzinfo=_d.timezone.utc)     # 13:00 ET
MON_LATE = _d.datetime(2026, 8, 10, 19, 0, tzinfo=_d.timezone.utc)    # 15:00 ET
live = mc.staleness(MON_MID.timestamp(), now_utc=MON_LATE)
assert live["phrase"] == "from the current session", live["phrase"]
print("a scan taken during the session being traded still reads as current")

# a premarket scan holds the PREVIOUS close, and must age from it
MON_PRE = _d.datetime(2026, 8, 10, 12, 30, tzinfo=_d.timezone.utc)    # 08:30 ET
TUE_MID = _d.datetime(2026, 8, 11, 17, 0, tzinfo=_d.timezone.utc)
pre = mc.staleness(MON_PRE.timestamp(), now_utc=TUE_MID)
# the phrase for an aged scan counts sessions; the point of the fix is
# that a premarket scan is dated to the PREVIOUS close, so by Tuesday
# midday a Monday-premarket scan is a full session behind
assert pre["sessions"] >= 1, pre
assert pre["phrase"] in ("one session old", "2 sessions old"), pre["phrase"]
print(f"a premarket scan is dated to the last close, not the day it ran "
      f"({pre['sessions']} session(s) old on Tuesday)")

print("\nSESSION-PROVENANCE PINNED")
