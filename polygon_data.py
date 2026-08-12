"""Market data from Polygon, which is an API rather than a scrape.

Everything this project reads from Yahoo comes through `yfinance`, which
is a scraper against an endpoint Yahoo does not document or support. The
consequences are written all over this repository: datacenter IPs get
throttled unpredictably, the universe had to be rebuilt from a bundled
fallback list whenever that happened, and the screener's per-sector
queries were the reason a truncation bug could silently delete every
Communication Services and Energy name from the board.

Polygon answers the same questions under a contract, and — decisively —
answers the two big ones in BULK:

    /v2/aggs/grouped/...     one call returns every US ticker for a day
    /v3/reference/tickers    1,000 tickers a page (names and types)

Measured on the free tier: the grouped call returned 12,410 tickers with
OHLC and volume in 0.8 seconds. The same data through yfinance is a
multi-minute batched download that fails whole sectors at a time.

What the free tier does NOT give, checked rather than assumed: market
cap, share counts and SIC codes are None on the paginated list — and on
the list filtered to one ticker. They live on the per-ticker detail
endpoint only. So fundamentals stay with the SEC, where they are
official and unmetered, and this module never claims otherwise.

THE CONSTRAINT THAT SHAPES EVERYTHING HERE: five calls per minute, hard.
Measured, not read from a page — a burst of twelve got two 200s and ten
429s. So this module NEVER makes a per-ticker call. Anything that would
need one ticker at a time is not implemented, on purpose; a design that
drifts into it would take six hours to scan a universe this size.

Free tier also means: end-of-day only, and exactly two years of history
(2024-08-12 answers, 2023-08-11 is 403).

What this module does NOT replace: SEC XBRL for filings (official, free,
unlimited) and Nasdaq for the earnings calendar.
"""
from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request

BASE = "https://api.polygon.io"

# Five per minute, hard. Twelve seconds plus a hair, so a clock that
# drifts the wrong way does not spend the run collecting 429s.
MIN_INTERVAL_S = float(os.environ.get("POLYGON_INTERVAL_S", "12.5"))
MAX_RETRIES = 3

_last_call = {"t": 0.0}


def have_key() -> bool:
    return bool(os.environ.get("POLYGON_API_KEY"))


def _get(path: str, params: dict | None = None, timeout: float = 45.0,
         progress=None) -> dict | None:
    """One rate-limited call. None on anything that is not a 200.

    Paced rather than retried-on-429 where possible: a 429 costs a whole
    call from a budget of five a minute, so waiting is cheaper than
    asking. The retry exists only for the case where something else
    shared the key.
    """
    key = os.environ.get("POLYGON_API_KEY")
    if not key:
        return None
    q = dict(params or {})
    q["apiKey"] = key
    url = f"{BASE}{path}"
    if q:
        from urllib.parse import urlencode
        url = f"{url}{'&' if '?' in path else '?'}{urlencode(q)}"

    for attempt in range(MAX_RETRIES):
        wait = MIN_INTERVAL_S - (time.time() - _last_call["t"])
        if wait > 0:
            time.sleep(wait)
        _last_call["t"] = time.time()
        try:
            req = urllib.request.Request(
                url, headers={"User-Agent": "StockScreener/1.0"})
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return json.load(r)
        except urllib.error.HTTPError as e:
            if e.code == 429 and attempt < MAX_RETRIES - 1:
                if progress:
                    progress(f"  polygon: rate limited, waiting {MIN_INTERVAL_S:.0f}s")
                time.sleep(MIN_INTERVAL_S)
                continue
            if progress:
                progress(f"  polygon {path}: HTTP {e.code}")
            return None
        except Exception as e:                                # noqa: BLE001
            if progress:
                progress(f"  polygon {path}: {type(e).__name__}")
            return None
    return None


def grouped_day(date: str, progress=None) -> dict:
    """{ticker: {"c","o","h","l","v"}} for every US ticker on `date`.

    One call for the entire market. An empty dict means a non-trading
    day or a refusal — the caller must not read it as "the market was
    flat", which is the mistake that put a 471%-volatility stale quote
    on the front page.
    """
    d = _get(f"/v2/aggs/grouped/locale/us/market/stocks/{date}",
             {"adjusted": "true"}, progress=progress)
    if not d or d.get("status") not in ("OK", "DELAYED"):
        return {}
    out = {}
    for row in (d.get("results") or []):
        t = row.get("T")
        if not t:
            continue
        out[t] = {"c": row.get("c"), "o": row.get("o"), "h": row.get("h"),
                  "l": row.get("l"), "v": row.get("v")}
    return out


def common_stocks(max_pages: int = 14, progress=None) -> dict:
    """{ticker: {"name","exchange"}} for active US common stock.

    WHAT THIS DOES NOT RETURN, measured rather than assumed: market cap,
    share counts and SIC code come back None from the paginated list —
    filtered to a single ticker as well. They exist only on the
    per-ticker detail endpoint, and five calls a minute makes twelve
    thousand of those a six-hour job. So the free tier gives no bulk
    fundamentals, and this module does not pretend otherwise: SIC still
    comes from the SEC, and share counts still come from XBRL.

    What it IS for: separating common stock from the ETFs, warrants and
    funds that share the grouped-bars feed. SPY and QQQ are the two
    largest lines by dollar volume in the whole market, and a universe
    ranked on that number without this filter would put them first.
    """
    out: dict = {}
    params = {"market": "stocks", "type": "CS", "active": "true",
              "limit": 1000, "sort": "ticker"}
    cursor = None
    for page in range(max_pages):
        if cursor:
            params = {"cursor": cursor, "limit": 1000}
        d = _get("/v3/reference/tickers", params, progress=progress)
        if not d:
            break
        rows = d.get("results") or []
        for r in rows:
            t = r.get("ticker")
            if t:
                out[t] = {"name": r.get("name"),
                          "exchange": r.get("primary_exchange")}
        nxt = d.get("next_url") or ""
        cursor = nxt.split("cursor=")[1].split("&")[0] if "cursor=" in nxt else None
        if progress:
            progress(f"  polygon: {len(out)} common stocks after page {page + 1}")
        if not cursor or not rows:
            break
    return out


def universe_by_liquidity(day: dict, common: dict | None = None,
                          cap: int = 1800, min_dollar_vol: float = 10e6) -> list:
    """The universe, ranked by money actually traded — from one call.

    The old universe came from ten per-sector Yahoo screens ordered by
    market cap, then truncated. That truncation ran across the
    concatenated sector blocks rather than across the sizes, so it kept
    every Technology name and deleted all of Communication Services and
    Energy — Alphabet dropped for ten-billion-dollar industrials, and
    every downstream feature inherited the hole.

    One grouped call cannot be truncated that way: there are no blocks.
    Dollar volume is also the more honest ranking for this tool, which
    exists to check trades a person can actually execute and already
    filters on liquidity — and on the measured day, "above $50M traded"
    picked out 1,795 names, within one of the universe the old path
    produced after all that machinery.
    """
    rows = []
    for t, bar in (day or {}).items():
        c, v = bar.get("c"), bar.get("v")
        if not c or not v or c <= 0:
            continue
        if common is not None and t not in common:
            continue          # ETFs, funds, warrants — SPY leads without this
        dv = c * v
        if dv >= min_dollar_vol:
            rows.append((dv, t))
    rows.sort(reverse=True)
    return [t for _, t in rows[:cap]]


def splits(since: str, progress=None) -> dict:
    """{ticker: [{"date","from","to"}]} executed on or after `since`.

    A split is a price discontinuity that is not a price move. Unhandled,
    it produced the 471%-annualised "volatility" that put a company with
    0.9% leverage on the page as being in distress. Adjusted bars handle
    it, and this exists so the handling can be verified rather than
    assumed.
    """
    d = _get("/v3/reference/splits",
             {"execution_date.gte": since, "limit": 1000},
             progress=progress)
    if not d:
        return {}
    out: dict = {}
    for r in (d.get("results") or []):
        t = r.get("ticker")
        if not t:
            continue
        out.setdefault(t, []).append({
            "date": r.get("execution_date"),
            "from": r.get("split_from"), "to": r.get("split_to")})
    return out


def is_financial_sic(sic) -> bool:
    """SIC 6000-6799, the same range credit.py refuses — read from the
    reference sweep instead of a per-company SEC request."""
    try:
        code = int(str(sic).strip())
    except (TypeError, ValueError):
        return False
    return 6000 <= code <= 6799


def price_book(dates: list, have: dict | None = None, progress=None) -> dict:
    """{"dates": [...], "series": {ticker: [closes]}} over `dates`.

    Fetches only the days not already in `have`, because five calls a
    minute makes a sixty-day cold build twelve minutes and a daily
    top-up twelve seconds. The published book is restored at the start
    of every scheduled run, so steady state is one call.

    Position IS the date: every ticker gets one slot per date, null
    where it did not trade. The correlation code reads by position, and
    a book that skipped non-trading days turned two identical series
    into a 0.49 correlation.
    """
    have = have or {}
    old_dates = list(have.get("dates") or [])
    old_series = have.get("series") or {}
    by_date = {}
    for i, d in enumerate(old_dates):
        if d in dates:
            by_date[d] = {t: (v[i] if i < len(v) else None)
                          for t, v in old_series.items()}
    missing = [d for d in dates if d not in by_date]
    if progress and missing:
        progress(f"  polygon: {len(missing)} of {len(dates)} days to fetch "
                 f"(~{len(missing) * MIN_INTERVAL_S / 60:.0f} min)")
    for d in missing:
        day = grouped_day(d, progress=progress)
        if day:
            by_date[d] = {t: bar.get("c") for t, bar in day.items()}

    kept = [d for d in dates if d in by_date]
    names: set = set()
    for d in kept:
        names.update(by_date[d])
    series = {}
    for t in names:
        row = [by_date[d].get(t) for d in kept]
        if sum(1 for x in row if x) >= len(kept) // 2:
            series[t] = [None if x is None else round(float(x), 4) for x in row]
    return {"dates": kept, "series": series}
