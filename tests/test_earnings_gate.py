"""The earnings gate has to VERIFY, not just refuse.

Failing closed on the earnings date was the right fix for the audit's
worst finding, and on its own it made the tool useless: every per-ticker
earnings source is a Yahoo or Finnhub call that fails from a datacenter
IP, so a live scan blocked all 28 candidates and published an empty
page. Honest, and worth nothing to a reader.

The bulk calendar is what makes fail-closed affordable. Its safety rests
entirely on one claim: a COMPLETE window makes absence informative. A
company missing from every day of the next 45 is not reporting in the
next 45 — a verified pass. The moment the window has a hole, absence
proves nothing and everything unlisted must go back to being blocked.

These tests exist to keep that distinction from eroding into "we
couldn't find a date, so it's probably fine".
"""
import os
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TMP = tempfile.mkdtemp(prefix="earngate_")
os.environ["SCREENER_CACHE_DB"] = os.path.join(TMP, "cache.db")
os.environ["MARKET_DB"] = os.path.join(TMP, "market.db")
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).parent))

import hashlib

import numpy as np
import pandas as pd

import cache_store
import screener
from _synth import pullback_hist

screener.RETRY_DELAYS = ()
screener._yahoo_auth_session = lambda: (None, None)
screener._get_quote_v7 = lambda t: None
screener._get_days_to_earnings = lambda t: None      # Yahoo unreachable
screener._finnhub_days_to_earnings = lambda t: None  # no key configured
screener._fx_rates = lambda: {"USD": 1.1, "GBP": 0.85, "CHF": 0.95,
                              "SEK": 11.0, "NOK": 11.0, "DKK": 7.5}
# fundamentals verify, so the only gate under test is the earnings one
screener._get_info = lambda t: {"marketCap": 50e9, "forwardEps": 5.0,
                                "sector": "Technology", "shortName": t}


# ---- a controllable stand-in for the published calendar ----
class FakeResponse:
    def __init__(self, status, rows):
        self.status_code, self._rows = status, rows

    def json(self):
        return {"data": {"rows": self._rows}}


def calendar_stub(reporting: dict, fail_days: set = frozenset()):
    """reporting: {symbol: day-offset}. fail_days: offsets that 500."""
    def get(url, params=None, headers=None, timeout=None):
        day = pd.Timestamp(params["date"])
        off = int((day - pd.Timestamp.now().normalize()).days)
        if off in fail_days:
            return FakeResponse(503, [])
        rows = [{"symbol": s} for s, d in reporting.items() if d == off]
        return FakeResponse(200, rows)
    return get


def weekday_offset(at_least: int) -> int:
    """First calendar-day offset >= at_least that is a weekday. The
    calendar only fetches trading sessions, so a fixture that lands on a
    Saturday is simply never published and the test measures nothing."""
    d = pd.Timestamp.now().normalize()
    off = at_least
    while (d + pd.Timedelta(days=off)).dayofweek >= 5:
        off += 1
    return off


SOON_D, LATER_D = weekday_offset(3), weekday_offset(28)
HOLE_D = weekday_offset(7)

TICKERS = ["SOON", "LATER", "ABSENT", "FOREIGN.L"]
# same seed for every ticker: a differently-seeded series can lack a pivot
# low entirely, and the scan then rejects it before the earnings gate ever
# runs — a test that passes for the wrong reason
ohlc = pd.concat({t: pullback_hist(0) for t in TICKERS}, axis=1)
bench = pd.Series(np.linspace(300, 400, 260),
                  index=pd.bdate_range(end=pd.Timestamp.today(), periods=260))

BASE = {
    "min_dollar_vol_m": 10, "min_rr": 0.5, "rsi_low": 0, "rsi_high": 100,
    "min_stop_atr": 0, "max_friction_pct": 0, "max_support_dist_pct": 0,
    "min_price": 0, "min_share_vol": 0, "require_market_uptrend": False,
    "require_profitable": False, "min_rs_3m": -100, "min_mkt_cap_b": 0,
    "strict_gates": True, "earnings_drop_days": 10, "exclude": "",
}


def scan(reporting, fail_days=frozenset(), **over):
    """Run a full scan against a stubbed calendar and stubbed prices."""
    cache_store.reset() if hasattr(cache_store, "reset") else None
    for f in Path(TMP).glob("cache.db*"):
        f.unlink(missing_ok=True)
    screener.requests.get = calendar_stub(reporting, fail_days)
    p = screener.clean_params(dict(BASE, **over))
    key = (p["include_us"], p["include_eu"], tuple(p["sectors"]),
           round(p["min_mkt_cap_b"] * 1e9), p["universe_max"])
    okey = hashlib.md5(",".join(TICKERS).encode()).hexdigest()
    screener._cache.update(universe_key=key, universe=TICKERS,
                           universe_ts=time.time(), ohlc_key=okey, ohlc=ohlc,
                           ohlc_ts=time.time(), bench={"US": bench, "EU": bench},
                           bench_ts=time.time(), info={}, earnings={}, finnhub={})
    log = []
    res = screener.run_screener(dict(BASE, **over), progress=lambda m: log.append(str(m)))
    rows = {r["ticker"]: r for r in res["df"].to_dict("records")}
    pend = {r["ticker"]: r for r in res.get("pending") or []}
    return rows, pend, log


# ---- a complete window: near reports block, far ones pass, absentees pass ----
rows, pend, log = scan({"SOON": SOON_D, "LATER": LATER_D})
assert "SOON" not in rows, f"a report {SOON_D} days out must not become a pick"
assert "LATER" in rows, f"a report {LATER_D} days out is outside the 10-day gate: {rows.keys()}"
assert rows["LATER"]["days_to_earnings"] == LATER_D
assert rows["LATER"]["earnings_in"] == f"{LATER_D}d"
assert "earnings_from_calendar" in rows["LATER"]["flags"]
print(f"complete window: {SOON_D}d blocked, {LATER_D}d published with its real date")

# the point of the whole exercise — a company on no page of a complete
# calendar is verified clear, and must reach the reader as a pick
assert "ABSENT" in rows, \
    "a name absent from a COMPLETE window is verified clear, not unknown"
assert pd.isna(rows["ABSENT"]["days_to_earnings"])   # no date, by design
assert rows["ABSENT"]["earnings_in"] == f">{screener.EARN_CAL_DAYS}d", \
    rows["ABSENT"]["earnings_in"]
assert "earnings_unverified" not in rows["ABSENT"]["flags"], \
    "verified-absent is not the same as unverified, and must not be flagged so"
print(f"complete window: an unlisted US name publishes as "
      f">{screener.EARN_CAL_DAYS}d — verified, not guessed")

# a US-only calendar says nothing about a London listing
assert "FOREIGN.L" not in rows, \
    "a US calendar cannot verify a non-US listing — it must stay blocked"
assert "FOREIGN.L" in pend, pend.keys()
assert "no earnings calendar covers this listing" in pend["FOREIGN.L"]["why_not"], \
    pend["FOREIGN.L"]["why_not"]
print("non-US listing blocked, and told why")

# ---- one missing day and the argument collapses ----
rows, pend, log = scan({"SOON": SOON_D, "LATER": LATER_D}, fail_days={HOLE_D})
assert "ABSENT" not in rows, \
    "with a hole in the window, absence proves nothing and must block"
assert "ABSENT" in pend, pend.keys()
assert any("INCOMPLETE" in l for l in log), \
    "an incomplete calendar must be stated in the log, not swallowed"
# a name the calendar DID list is still verified — a hole elsewhere does
# not invalidate a positive sighting
assert "LATER" in rows, "a listed date stays valid even in a partial window"
print("incomplete window: absentees blocked, positive sightings still trusted")

# ---- the score must not treat "verified clear" as "unknown" ----
def row(**kw):
    base = {"RR": 3.0, "RSI": 45.0, "price": 100.0, "support": 96.0,
            "rs_3m": 2.0, "vol_ratio": 0.8, "days_to_earnings": None,
            "analyst_mean": None, "flags": ""}
    base.update(kw)
    return base


P = screener.clean_params({})
unknown, _, _ = screener.score_row(row(), P)
clear, _, _ = screener.score_row(row(earnings_in=f">{screener.EARN_CAL_DAYS}d"), P)
soon, _, _ = screener.score_row(row(days_to_earnings=4, earnings_in="4d"), P)
assert clear > unknown > soon, (clear, unknown, soon)
print(f"scoring: verified-clear {clear} > unknown {unknown} > reports-soon {soon}")

# ---- the calendar is cached, so three presets cost one fetch ----
calls = {"n": 0}
inner = calendar_stub({"X": 2})


def counting(*a, **k):
    calls["n"] += 1
    return inner(*a, **k)


screener.requests.get = counting
cache_store.put("earncal:5", {"as_of": str(pd.Timestamp.now().normalize().date()),
                              "map": {"X": 2}, "complete": True})
cal, ok = screener._earnings_calendar(days_ahead=5)
assert ok and cal == {"X": 2}, (cal, ok)
assert calls["n"] == 0, f"a cached calendar must not re-fetch ({calls['n']} calls)"
print("calendar cached: repeat presets in the same run cost no extra requests")

print("\nALL EARNINGS-GATE TESTS PASSED")
