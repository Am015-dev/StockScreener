"""A year of daily closes and volumes, one request per company.

The pattern sweep needs long history for several hundred names. The
Polygon free tier gives it a whole market day per call, but at five
calls a minute that is fifty minutes for a year — and it needs a key
this project cannot assume anyone has added.

Yahoo's chart endpoint answers a full year of daily bars for one company
in a single request, no key. Four hundred names take about eight
minutes, which is nothing on a build machine, and the result is cached
between runs so the second sweep fetches only what it does not hold.

Volume is kept, not discarded, and it matters more than it looks: it is
what lets the universe be chosen from the START of the window. Ranking
by what is heavily traded today and then replaying a year backwards is a
universe picked with knowledge of the future — the names that collapsed
fell out of today's list, the names that took off joined it — and every
momentum-flavoured shape gets paid for that twice.
"""
from __future__ import annotations

import json
import random
import time
import urllib.error
import urllib.parse
import urllib.request

CHART = "https://query1.finance.yahoo.com/v8/finance/chart/{t}?range={r}&interval=1d"
UA = {"User-Agent": "Mozilla/5.0 (compatible; pullback-screener/1.0)"}
PAUSE_S = 0.55
TIMEOUT_S = 30


def one(ticker: str, span: str = "1y", tries: int = 3) -> dict | None:
    """{date: (close, volume)} for one company, or None.

    None means "no usable answer", never an empty book that would be
    mistaken for a company that did not trade.
    """
    url = CHART.format(t=urllib.parse.quote(ticker), r=span)
    for k in range(tries):
        try:
            req = urllib.request.Request(url, headers=UA)
            with urllib.request.urlopen(req, timeout=TIMEOUT_S) as fh:
                d = json.load(fh)
            res = (d.get("chart") or {}).get("result") or []
            if not res:
                return None
            r = res[0]
            ts = r.get("timestamp") or []
            q = ((r.get("indicators") or {}).get("quote") or [{}])[0]
            cl = q.get("close") or []
            vol = q.get("volume") or []
            if len(ts) != len(cl):
                return None
            out = {}
            for j, (s, c) in enumerate(zip(ts, cl)):
                if c and c > 0:
                    day = time.strftime("%Y-%m-%d", time.gmtime(s))
                    v = vol[j] if j < len(vol) and vol[j] else 0
                    out[day] = (round(float(c), 4), int(v))
            return out or None
        except urllib.error.HTTPError as e:
            if e.code in (429, 503):
                time.sleep(2 ** k + random.random() * 2)
                continue
            return None
        except Exception:                                     # noqa: BLE001
            time.sleep(1 + k)
    return None


def year_book(tickers: list, span: str = "1y", have: dict | None = None,
              progress=None, coverage: float = 0.8,
              completeness: float = 0.95) -> dict:
    """{dates, series, volume} on ONE calendar grid.

    Every stock is compared against every other stock on the same day, so
    the grid has to be shared. A day only makes the grid if most names
    traded on it, and a name only stays if it traded on most of the grid
    — otherwise a thinly-listed ticker drags holes through the middle of
    everyone's comparison.
    """
    got = dict((have or {}).get("raw") or {})
    todo = [t for t in tickers if t not in got]
    for i, t in enumerate(todo, 1):
        c = one(t, span=span)
        if c:
            got[t] = c
        if progress and (i % 50 == 0 or i == len(todo)):
            progress(f"  history {i}/{len(todo)} fetched, {len(got)} held")
        time.sleep(PAUSE_S)

    counts: dict = {}
    for c in got.values():
        for d in c:
            counts[d] = counts.get(d, 0) + 1
    need = coverage * max(1, len(got))
    dates = sorted(d for d, n in counts.items() if n >= need)
    if not dates:
        return {"dates": [], "series": {}, "volume": {}, "raw": got}

    series, volume = {}, {}
    for t, c in got.items():
        closes = [c[d][0] if d in c else None for d in dates]
        if sum(1 for x in closes if x) < completeness * len(dates):
            continue
        series[t] = closes
        volume[t] = [c[d][1] if d in c else 0 for d in dates]
    return {"dates": dates, "series": series, "volume": volume, "raw": got}


def universe_at_start(book: dict, cap: int = 400, sessions: int = 20,
                      min_dollar_vol: float = 5e6) -> list:
    """The most heavily traded names AT THE BEGINNING of the window.

    This is the whole point of keeping volume. A universe ranked on the
    last day of the window is a universe chosen with knowledge of how the
    window turned out; ranked on the first twenty sessions it is what
    somebody could have screened on that morning.

    It does not fix everything. Companies that were delisted during the
    window are absent from the data altogether and nothing here brings
    them back, so what is left still leans towards survivors — just far
    less than before.
    """
    series = book.get("series") or {}
    volume = book.get("volume") or {}
    liq = {}
    for t, vols in volume.items():
        closes = series.get(t) or []
        n = min(sessions, len(vols), len(closes))
        dv = sorted(vols[i] * closes[i] for i in range(n)
                    if vols[i] and closes[i])
        if len(dv) >= sessions * 0.8:
            med = dv[len(dv) // 2]
            if med >= min_dollar_vol:
                liq[t] = med
    return sorted(liq, key=lambda t: -liq[t])[:cap]
