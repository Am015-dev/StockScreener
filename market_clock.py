"""What the US market is doing right now, and when it last closed.

The page had no idea markets close. On a Saturday it showed Friday's
close with a wall-clock age of "10 hours" and an amber "rerun before
acting" warning, which reads as a broken or neglected site. It is not:
Friday's close is the freshest data that exists, and will be until
Monday. An age counter that keeps ticking through a weekend measures
the wrong thing — what matters is how many *sessions* have passed, and
the answer over a weekend is zero.

Backed by exchange_calendars (github.com/gerrymanoim/exchange_calendars)
when it is importable, with every function falling back to the original
hand-typed HOLIDAYS table below if it is not — a broken wheel on Render
must degrade to the old behaviour, never a 500. The calendar is built
once, bounded to a roughly two-year window around "now" (never the
library's full multi-decade history, and never its minute-level APIs),
so the memory cost on a 512MB instance is a few hundred rows, not a
astronomical almanac. This replaced an earlier "adding a calendar
library for a fact this small is a poor trade" judgement in this same
docstring — see docs/review-log.md for why that call was reversed.
"""
from __future__ import annotations

import datetime as _dt

# NYSE/Nasdaq regular session, US Eastern.
OPEN_H, OPEN_M = 9, 30
CLOSE_H, CLOSE_M = 16, 0

_CAL = None
_CAL_TRIED = False


def _calendar():
    """The bounded XNYS calendar, or None if the library is unavailable.

    Built once per process. The window is deliberately short — this
    module only ever gets asked about "now" and recent scan timestamps,
    never historical dates — so a few hundred sessions is plenty and
    keeps the memory cost trivial on a 512MB instance.
    """
    global _CAL, _CAL_TRIED
    if _CAL_TRIED:
        return _CAL
    _CAL_TRIED = True
    try:
        import exchange_calendars as _xcals
        today = _dt.date.today()
        _CAL = _xcals.get_calendar(
            "XNYS",
            start=(today - _dt.timedelta(days=400)).isoformat(),
            end=(today + _dt.timedelta(days=550)).isoformat(),
        )
    except Exception:
        _CAL = None
    return _CAL


def _in_cal_range(cal, d: _dt.date) -> bool:
    return cal is not None and cal.first_session.date() <= d <= cal.last_session.date()


# Full-day closures. This is the fallback table, used whenever the
# calendar library above did not load or a date falls outside its
# bounded window — the calendar answers everything within its range,
# including half-days, which this table still cannot model on its own
# (treating a half day as a full session is the correct fallback for
# "has a session happened since?", the only question the pure-Python
# path is ever asked to answer without the library).
HOLIDAYS = {
    # 2026
    _dt.date(2026, 1, 1): "New Year's Day",
    _dt.date(2026, 1, 19): "Martin Luther King Jr. Day",
    _dt.date(2026, 2, 16): "Presidents' Day",
    _dt.date(2026, 4, 3): "Good Friday",
    _dt.date(2026, 5, 25): "Memorial Day",
    _dt.date(2026, 6, 19): "Juneteenth",
    _dt.date(2026, 7, 3): "Independence Day (observed)",
    _dt.date(2026, 9, 7): "Labor Day",
    _dt.date(2026, 11, 26): "Thanksgiving",
    _dt.date(2026, 12, 25): "Christmas Day",
    # 2027
    _dt.date(2027, 1, 1): "New Year's Day",
    _dt.date(2027, 1, 18): "Martin Luther King Jr. Day",
    _dt.date(2027, 2, 15): "Presidents' Day",
    _dt.date(2027, 3, 26): "Good Friday",
    _dt.date(2027, 5, 31): "Memorial Day",
    _dt.date(2027, 6, 18): "Juneteenth (observed)",
    _dt.date(2027, 7, 5): "Independence Day (observed)",
    _dt.date(2027, 9, 6): "Labor Day",
    _dt.date(2027, 11, 25): "Thanksgiving",
    _dt.date(2027, 12, 24): "Christmas Day (observed)",
}

# The last date this module will answer about. Past this it reports that
# it does not know, rather than quietly assuming every weekday is a session
# — a screener that invents a trading calendar will eventually tell someone
# the market is open on Christmas. When the calendar library loaded, this is
# its actual last built session (which moves forward on every process
# restart); otherwise it falls back to the last entry in the hand-typed
# table above.
KNOWN_THROUGH = (_calendar().last_session.date() if _calendar() is not None
                  else max(HOLIDAYS))


def _eastern_offset(d: _dt.date) -> int:
    """Hours behind UTC for US Eastern on this date (-4 EDT, -5 EST).

    US DST: second Sunday in March to first Sunday in November.
    """
    def nth_sunday(year, month, n):
        d1 = _dt.date(year, month, 1)
        first = d1 + _dt.timedelta(days=(6 - d1.weekday()) % 7)
        return first + _dt.timedelta(days=7 * (n - 1))

    start = nth_sunday(d.year, 3, 2)
    end = nth_sunday(d.year, 11, 1)
    return 4 if start <= d < end else 5


def is_session(d: _dt.date) -> bool:
    """Is this date a regular trading session?"""
    cal = _calendar()
    if _in_cal_range(cal, d):
        return bool(cal.is_session(d.isoformat()))
    return d.weekday() < 5 and d not in HOLIDAYS


def previous_session(d: _dt.date) -> _dt.date:
    """The most recent trading session on or before this date."""
    cal = _calendar()
    if _in_cal_range(cal, d):
        return cal.date_to_session(d.isoformat(), direction="previous").date()
    while not is_session(d):
        d -= _dt.timedelta(days=1)
    return d


def sessions_between(start: _dt.date, end: _dt.date) -> int:
    """Trading sessions strictly after `start` and up to and including
    `end`. This is the honest unit for "how old are these prices" — a
    weekend adds hours but no sessions, and no prices moved."""
    if end <= start:
        return 0
    n, d = 0, start + _dt.timedelta(days=1)
    while d <= end:
        if is_session(d):
            n += 1
        d += _dt.timedelta(days=1)
    return n


def state(now_utc: _dt.datetime | None = None) -> dict:
    """Describe the market right now.

    Returns keys:
      state          one of open / premarket / afterhours / weekend /
                     holiday / unknown
      label          a sentence a reader can act on
      is_open        bool
      last_close_ts  UTC epoch of the most recent session's close
      next_open_ts   UTC epoch of the next session's open (None if unknown)
      holiday        holiday name when state == "holiday"
    """
    now = now_utc or _dt.datetime.now(_dt.timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=_dt.timezone.utc)
    off = _eastern_offset(now.date())
    et = now - _dt.timedelta(hours=off)
    today = et.date()
    cal = _calendar()

    def _cal_close_utc_ts(d: _dt.date) -> float | None:
        """This session's real close as a UTC epoch, from the calendar's
        schedule — this is what makes a half-day close at 1pm ET instead
        of 4pm. None when the calendar is unavailable or d is outside it,
        in which case every caller below falls back to the fixed hour."""
        if not _in_cal_range(cal, d):
            return None
        try:
            row = cal.schedule.loc[_dt.datetime(d.year, d.month, d.day)]
        except KeyError:
            return None
        return row["close"].timestamp()

    def close_ts(d: _dt.date) -> float:
        exact = _cal_close_utc_ts(d)
        if exact is not None:
            return exact
        o = _eastern_offset(d)
        return _dt.datetime(d.year, d.month, d.day, CLOSE_H, CLOSE_M,
                            tzinfo=_dt.timezone.utc).timestamp() + o * 3600

    def close_minutes_et(d: _dt.date) -> int:
        """ET minutes-since-midnight this session closes — 780 (1pm) on a
        half day, the regular 960 (4pm) otherwise or without the library."""
        exact = _cal_close_utc_ts(d)
        if exact is None:
            return CLOSE_H * 60 + CLOSE_M
        close_et = (_dt.datetime.fromtimestamp(exact, _dt.timezone.utc)
                    - _dt.timedelta(hours=_eastern_offset(d)))
        return close_et.hour * 60 + close_et.minute

    def open_ts(d: _dt.date) -> float:
        o = _eastern_offset(d)
        return _dt.datetime(d.year, d.month, d.day, OPEN_H, OPEN_M,
                            tzinfo=_dt.timezone.utc).timestamp() + o * 3600

    # beyond what the calendar (or the fallback table) covers, say so
    # rather than guess
    if today > KNOWN_THROUGH:
        return {"state": "unknown", "is_open": False, "holiday": None,
                "last_close_ts": None, "next_open_ts": None,
                "label": "Trading calendar not known past "
                         f"{KNOWN_THROUGH.isoformat()} — treating prices as "
                         f"undated rather than guessing."}

    minutes = et.hour * 60 + et.minute
    open_min = OPEN_H * 60 + OPEN_M
    close_min = close_minutes_et(today)
    early_close = close_min < CLOSE_H * 60 + CLOSE_M
    if early_close:
        h24, m = close_min // 60, close_min % 60
        h12 = h24 - 12 if h24 > 12 else (12 if h24 == 0 else h24)
        ampm = "pm" if h24 >= 12 else "am"
        early_suffix = f" (early close today, {h12}:{m:02d}{ampm} ET)"
    else:
        early_suffix = ""

    def next_session_after(d: _dt.date) -> _dt.date:
        d += _dt.timedelta(days=1)
        while not is_session(d) and d <= KNOWN_THROUGH:
            d += _dt.timedelta(days=1)
        return d

    if is_session(today):
        if minutes < open_min:
            prev = previous_session(today - _dt.timedelta(days=1))
            return {"state": "premarket", "is_open": False, "holiday": None,
                    "last_close_ts": close_ts(prev), "next_open_ts": open_ts(today),
                    "label": f"US markets open at 9:30am ET{early_suffix} — these are "
                             f"{prev.strftime('%A')}'s closing prices."}
        if minutes < close_min:
            return {"state": "open", "is_open": True, "holiday": None,
                    "last_close_ts": close_ts(previous_session(today - _dt.timedelta(days=1))),
                    "next_open_ts": None,
                    "label": f"US markets are open{early_suffix} — prices are moving now."}
        nxt = next_session_after(today)
        return {"state": "afterhours", "is_open": False, "holiday": None,
                "last_close_ts": close_ts(today),
                "next_open_ts": open_ts(nxt) if nxt <= KNOWN_THROUGH else None,
                "label": f"US markets have closed for the day{early_suffix} — these are "
                         "today's closing prices."}

    prev = previous_session(today)
    nxt = next_session_after(today - _dt.timedelta(days=1)) if today.weekday() >= 5 \
        else next_session_after(today)
    while nxt <= KNOWN_THROUGH and not is_session(nxt):
        nxt = next_session_after(nxt)
    holiday = HOLIDAYS.get(today)
    if not holiday and today.weekday() < 5 and _in_cal_range(cal, today):
        # a weekday closure past the hand-typed table's coverage — the
        # calendar library knows years the fixed table was never updated
        # for, and naming the holiday beats a bare "closed, unexplained"
        try:
            names = cal.regular_holidays.holidays(
                _dt.datetime(today.year, today.month, today.day),
                _dt.datetime(today.year, today.month, today.day),
                return_name=True)
            if len(names):
                holiday = str(names.iloc[0])
        except Exception:
            pass
    if holiday:
        label = (f"US markets are closed for {holiday} — these are "
                 f"{prev.strftime('%A')}'s closing prices.")
        st = "holiday"
    else:
        label = (f"US markets are closed for the weekend — these are "
                 f"{prev.strftime('%A')}'s closing prices.")
        st = "weekend"
    return {"state": st, "is_open": False, "holiday": holiday,
            "last_close_ts": close_ts(prev),
            "next_open_ts": open_ts(nxt) if nxt <= KNOWN_THROUGH else None,
            "label": label}


def staleness(results_ts: float, now_utc: _dt.datetime | None = None) -> dict:
    """How stale a scan is, measured in sessions rather than hours.

    A scan from Friday afternoon read on Saturday is 0 sessions old: it
    is the freshest view of the market that exists. The same scan read on
    Tuesday is 2 sessions old and genuinely needs rerunning. Wall-clock
    hours cannot tell those apart, and the page was warning about both.
    """
    now = now_utc or _dt.datetime.now(_dt.timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=_dt.timezone.utc)
    m = state(now)
    scan_dt = _dt.datetime.fromtimestamp(results_ts, _dt.timezone.utc)
    scan_et = scan_dt - _dt.timedelta(hours=_eastern_offset(scan_dt.date()))
    # A scan run BEFORE the opening bell can only hold the previous
    # close, but previous_session() maps a trading day to itself, so a
    # premarket Monday scan was dated Monday and still claimed to be zero
    # sessions old after Monday had traded and closed. A scan run during
    # the session is a different matter: those are that session's live
    # prices, so only the pre-open case is rolled back.
    scan_session = previous_session(scan_et.date())
    if (scan_session == scan_et.date()
            and (scan_et.hour * 60 + scan_et.minute) < OPEN_H * 60 + OPEN_M):
        scan_session = previous_session(scan_et.date() - _dt.timedelta(days=1))

    if m["state"] == "unknown":
        return {"sessions": None, "stale": True, "phrase": "age unknown",
                "market": m}

    now_et = now - _dt.timedelta(hours=_eastern_offset(now.date()))
    ref = previous_session(now_et.date())
    # a scan taken during today's session is current until today closes
    if is_session(now_et.date()) and \
            (now_et.hour * 60 + now_et.minute) < CLOSE_H * 60 + CLOSE_M:
        ref = previous_session(now_et.date() - _dt.timedelta(days=1)) \
            if scan_session < now_et.date() else now_et.date()

    sessions = sessions_between(scan_session, ref)
    if sessions <= 0:
        # "the current session" has to mean the scan's prices came from the
        # session trading right now — not merely that some session is open
        # while the reader looks. Friday's closes read on Monday morning
        # were being described as live.
        live = m["is_open"] and scan_session == previous_session(now_et.date()) \
            and is_session(now_et.date()) and scan_session == now_et.date()
        phrase = ("from the current session" if live
                  else f"from the last close ({scan_session.strftime('%A')})")
    elif sessions == 1:
        phrase = "one session old"
    else:
        phrase = f"{sessions} sessions old"
    # one session of drift is tolerable; two means the levels have moved
    return {"sessions": sessions, "stale": sessions >= 2, "phrase": phrase,
            "market": m}
