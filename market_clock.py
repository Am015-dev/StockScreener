"""What the US market is doing right now, and when it last closed.

The page had no idea markets close. On a Saturday it showed Friday's
close with a wall-clock age of "10 hours" and an amber "rerun before
acting" warning, which reads as a broken or neglected site. It is not:
Friday's close is the freshest data that exists, and will be until
Monday. An age counter that keeps ticking through a weekend measures
the wrong thing — what matters is how many *sessions* have passed, and
the answer over a weekend is zero.

This module is deliberately dependency-free and self-contained. Adding
pandas_market_calendars for a fact this small would be a poor trade on
a 512MB instance.
"""
from __future__ import annotations

import datetime as _dt

# NYSE/Nasdaq regular session, US Eastern.
OPEN_H, OPEN_M = 9, 30
CLOSE_H, CLOSE_M = 16, 0

# Full-day closures. Half-days (1pm close) are deliberately not modelled:
# treating a half day as a full session is correct for every question this
# module is asked ("has a session happened since?"), and a wrong half-day
# rule would be worse than none.
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

# The last date the holiday table covers. Past this the module reports that
# it does not know, rather than quietly assuming every weekday is a session
# — a screener that invents a trading calendar will eventually tell someone
# the market is open on Christmas.
KNOWN_THROUGH = max(HOLIDAYS)


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
    """Is this date a regular full trading session?"""
    return d.weekday() < 5 and d not in HOLIDAYS


def previous_session(d: _dt.date) -> _dt.date:
    """The most recent trading session on or before this date."""
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

    def close_ts(d: _dt.date) -> float:
        o = _eastern_offset(d)
        return _dt.datetime(d.year, d.month, d.day, CLOSE_H, CLOSE_M,
                            tzinfo=_dt.timezone.utc).timestamp() + o * 3600

    def open_ts(d: _dt.date) -> float:
        o = _eastern_offset(d)
        return _dt.datetime(d.year, d.month, d.day, OPEN_H, OPEN_M,
                            tzinfo=_dt.timezone.utc).timestamp() + o * 3600

    # beyond the holiday table, say so rather than guess
    if today > KNOWN_THROUGH:
        return {"state": "unknown", "is_open": False, "holiday": None,
                "last_close_ts": None, "next_open_ts": None,
                "label": "Trading calendar not known past "
                         f"{KNOWN_THROUGH.isoformat()} — treating prices as "
                         f"undated rather than guessing."}

    minutes = et.hour * 60 + et.minute
    open_min, close_min = OPEN_H * 60 + OPEN_M, CLOSE_H * 60 + CLOSE_M

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
                    "label": f"US markets open at 9:30am ET — these are "
                             f"{prev.strftime('%A')}'s closing prices."}
        if minutes < close_min:
            return {"state": "open", "is_open": True, "holiday": None,
                    "last_close_ts": close_ts(previous_session(today - _dt.timedelta(days=1))),
                    "next_open_ts": None,
                    "label": "US markets are open — prices are moving now."}
        nxt = next_session_after(today)
        return {"state": "afterhours", "is_open": False, "holiday": None,
                "last_close_ts": close_ts(today),
                "next_open_ts": open_ts(nxt) if nxt <= KNOWN_THROUGH else None,
                "label": "US markets have closed for the day — these are "
                         "today's closing prices."}

    prev = previous_session(today)
    nxt = next_session_after(today - _dt.timedelta(days=1)) if today.weekday() >= 5 \
        else next_session_after(today)
    while nxt <= KNOWN_THROUGH and not is_session(nxt):
        nxt = next_session_after(nxt)
    holiday = HOLIDAYS.get(today)
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
    scan_session = previous_session(scan_et.date())

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
        phrase = ("from the current session" if m["is_open"]
                  else f"from the last close ({scan_session.strftime('%A')})")
    elif sessions == 1:
        phrase = "one session old"
    else:
        phrase = f"{sessions} sessions old"
    # one session of drift is tolerable; two means the levels have moved
    return {"sessions": sessions, "stale": sessions >= 2, "phrase": phrase,
            "market": m}
