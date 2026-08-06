"""
Pullback-in-Uptrend Swing Screener — with quality & policy gates
----------------------------------------------------------------
Replicates the TradingView/Yahoo pivot scan (support/resistance, R:R ranking)
and adds the filters a chart-only screener lacks:
  - profitability gate (forward EPS > 0)
  - earnings-proximity drop (no entries into binary events)
  - risk-based position sizing against YOUR max risk
  - exclusion list (held-at-cap or permanently passed tickers)

Install once:   pip install yfinance pandas numpy
Run:            python screener.py
Output:         screener_results.csv (ranked by R:R)

Also importable: run_screener(params) returns the results DataFrame + rejection
map, which is how the web app (app.py) drives it. Every threshold is a
parameter (see DEFAULTS) so the UI can expose them as filters. Universe and
OHLC downloads are cached for an hour, so re-running with different filter
values is fast.

Yahoo rate limits (429) never abort a scan: throttled calls retry briefly,
then a cooldown stops further live calls while the scan falls back to disk-
cached data at any age; results built on missing fundamentals are flagged
(`fundamentals_unavailable`) rather than falsely rejected.
"""

import hashlib
import os
import time
from collections import Counter

import numpy as np
import pandas as pd
import requests
import yfinance as yf

try:
    from yfinance.exceptions import YFRateLimitError
except Exception:  # older/newer yfinance layouts — fall back to message sniffing
    class YFRateLimitError(Exception):
        pass

import cache_store
import universe_static

# ----------------------- PARAMETERS -----------------------
# All Yahoo sectors except Financial Services (the no-financials rule —
# enforced again at stage 2 as a safety net for EU strays).
ALL_SECTORS = [
    "Technology", "Healthcare", "Industrials", "Consumer Cyclical",
    "Consumer Defensive", "Energy", "Basic Materials",
    "Communication Services", "Utilities", "Real Estate",
]
EU_REGIONS = ["de", "fr", "ch", "gb", "it", "nl", "dk", "se", "no", "es"]
SECTOR_EXCLUDE = {"Financial Services"}  # hard rule, not user-tunable

DEFAULTS = {
    # universe
    "include_us": True,
    "include_eu": True,
    "sectors": list(ALL_SECTORS),
    "min_mkt_cap_b": 10.0,       # $B
    "min_dollar_vol_m": 200.0,   # $M/day traded (price x volume)
    "universe_max": 600,         # cap scan size: less rate-limit risk, less RAM
    "exclude": "AVGO, AMZN, GOOG, NVDA, AAL",  # held-at-cap or permanent passes
    # setup
    "rsi_low": 35.0,             # pullback zone, not collapse
    "rsi_high": 55.0,
    "min_rr": 3.0,
    "swing_lookback": 60,        # days for resistance (swing high)
    "pivot_k": 3,                # bars each side for a pivot low
    "stop_buffer_pct": 1.5,      # stop % below support
    "min_stop_atr": 1.0,         # stop must sit >= this many ATRs away (noise gate)
    # policy gates
    "require_profitable": True,  # forward EPS > 0  (the anti-AAL gate)
    "require_analyst_buy": False,  # analyst consensus Buy or better (Yahoo data)
    "earnings_drop_days": 10,    # no entries into binary events
    "require_market_uptrend": True,  # benchmark (SPY / STOXX50) above its SMA200
    # sizing
    "ticket_eur": 250.0,
    "max_risk_eur": 90.0,        # ~0.75% of an ~€11.8k book
    # portfolio awareness (all optional — empty portfolio disables the checks)
    "holdings": [],              # [{"ticker": "AAPL", "shares": 10, "cost": 150.0}]
    "cash_eur": 0.0,             # free cash available to fund new entries
    "current_open_risk_eur": 0.0,  # summed risk of stops already in the market
    "sector_cap_pct": 35.0,      # flag entries pushing a sector past this weight
    "max_open_risk_pct": 6.0,    # aggregate open-risk ceiling as % of book
    # trade economics
    "commission_eur": 1.0,       # per order
    "spread_bps": 5.0,           # half-spread + slippage estimate
    "max_friction_pct": 20.0,    # reject if costs eat > this % of target profit (0 = off)
    # output
    "show_near": True,           # also rank the closest non-qualifying setups
}
NEAR_MAX = 10          # near-miss board size
NEAR_MISS_PENALTY = 8  # score points per failed gate on the near-miss board
EURUSD = 1.08                    # rough conversion for sizing math

BENCHMARKS = {"US": "SPY", "EU": "^STOXX50E"}  # regime + relative-strength references

# Optional, free (finnhub.io free tier, no card): cross-checks earnings dates
# for US tickers. Leave unset and the screener still works — Yahoo-only dates,
# with an `earnings_unverified` flag when none is found.
FINNHUB_KEY = os.environ.get("FINNHUB_API_KEY", "")

FALLBACK_UNIVERSE = [
    "AAPL","MSFT","NVDA","AMZN","GOOG","META","AVGO","LLY","UNH","XOM",
    "JNJ","PG","HD","KO","PEP","COST","MRK","ABBV","CVX","WMT","CAT",
    "GE","GEV","BA","MU","AMD","QCOM","TXN","LIN","ETN","CCJ","TMUS",
    "RHM.DE","SIE.DE","SAP.DE","AIR.PA","MC.PA","ASML.AS","NESN.SW",
    "NOVN.SW","RHHBY","AZN.L","SHEL.L","ULVR.L","NOVO-B.CO","RACE.MI",
]

CACHE_TTL = 3600      # reuse universe/OHLC for an hour
INFO_TTL = 86400      # fundamentals/earnings drift slowly — reuse for a day
STALE_OK = float("inf")  # cache_store TTL meaning "any age beats no data"
DOWNLOAD_CHUNK = 150  # tickers per batch: small enough for live partial results
RATE_LIMIT_COOLDOWN = 180  # once Yahoo hard-429s us, stop live calls this long
# per-API cooldowns: the quote API often clears within a minute, and only a
# handful of calls need it per scan — give it a short window plus a retry
# pass at the end of the scan instead of writing off the whole run
SCOPE_COOLDOWN = {"screen": 300, "chart": 180, "quote": 45}
QUOTE_RETRY_MAX_WAIT = 60  # max seconds the end-of-scan fundamentals retry waits
RETRY_DELAYS = (2, 5)      # backoff before giving up on a rate-limited call
# ----------------------------------------------------------


# ----------------------- rate-limit guard -----------------------
# Yahoo throttles by IP and a hard 429 tends to persist for minutes. One
# rate-limited call must never kill a scan (stale cache beats no scan), and
# once we know we're throttled there is no point firing hundreds more doomed
# requests — a cooldown window short-circuits live calls to the stale path.
#
# Crucially, Yahoo limits each API separately, and by very different amounts:
# the screener API ("screen") trips almost immediately from datacenter IPs,
# the chart/price API ("chart") is the most tolerant, quote/fundamentals
# ("quote") sits in between. The breaker is therefore scoped per API class —
# a blocked screener query must not stop the price download from being tried.
_rl = {"until": {}, "hits": 0}   # scope -> cooldown deadline


def _rate_limited_now(scope: str = "chart") -> bool:
    return time.time() < _rl["until"].get(scope, 0.0)


def _note_rate_limited(scope: str):
    _rl["hits"] += 1
    cooldown = SCOPE_COOLDOWN.get(scope, RATE_LIMIT_COOLDOWN)
    _rl["until"][scope] = max(_rl["until"].get(scope, 0.0),
                              time.time() + cooldown)


def _is_rate_limit(e: Exception) -> bool:
    return (isinstance(e, YFRateLimitError)
            or "too many requests" in str(e).lower() or "429" in str(e))


def _yahoo_call(fn, scope: str = "chart"):
    """Run one live Yahoo call with retry + per-API circuit breaker.

    Returns (ok, value). Retries a rate-limited call with short backoff;
    when retries are exhausted, opens this scope's cooldown window and
    returns (False, None). During that window every call in the same scope
    short-circuits to (False, None) without touching the network — other
    scopes keep trying. Non-rate-limit exceptions propagate so callers keep
    their existing error handling.
    """
    if _rate_limited_now(scope):
        return False, None
    delays = list(RETRY_DELAYS)
    while True:
        try:
            return True, fn()
        except Exception as e:
            if not _is_rate_limit(e):
                raise
            if not delays:
                _note_rate_limited(scope)
                return False, None
            time.sleep(delays.pop(0))


def _num(v, default, lo, hi, cast=float):
    try:
        return min(hi, max(lo, cast(v)))
    except (TypeError, ValueError):
        return default


def clean_params(overrides: dict | None) -> dict:
    """Merge user overrides onto DEFAULTS, coercing and clamping everything.

    Never raises on bad input — a garbage value falls back to its default.
    """
    o = overrides or {}
    p = dict(DEFAULTS)

    p["include_us"] = bool(o.get("include_us", p["include_us"]))
    p["include_eu"] = bool(o.get("include_eu", p["include_eu"]))
    if not (p["include_us"] or p["include_eu"]):
        p["include_us"] = True

    sectors = o.get("sectors", p["sectors"])
    if isinstance(sectors, (list, tuple)):
        picked = [s for s in ALL_SECTORS if s in sectors]
        p["sectors"] = picked or list(ALL_SECTORS)

    p["min_mkt_cap_b"] = _num(o.get("min_mkt_cap_b"), p["min_mkt_cap_b"], 0.5, 5000)
    p["min_dollar_vol_m"] = _num(o.get("min_dollar_vol_m"), p["min_dollar_vol_m"], 0, 100000)
    p["universe_max"] = _num(o.get("universe_max"), p["universe_max"], 50, 5000, int)
    p["rsi_low"] = _num(o.get("rsi_low"), p["rsi_low"], 0, 100)
    p["rsi_high"] = _num(o.get("rsi_high"), p["rsi_high"], 0, 100)
    if p["rsi_low"] > p["rsi_high"]:
        p["rsi_low"], p["rsi_high"] = p["rsi_high"], p["rsi_low"]
    p["min_rr"] = _num(o.get("min_rr"), p["min_rr"], 0.5, 20)
    p["swing_lookback"] = _num(o.get("swing_lookback"), p["swing_lookback"], 10, 250, int)
    p["pivot_k"] = _num(o.get("pivot_k"), p["pivot_k"], 1, 10, int)
    p["stop_buffer_pct"] = _num(o.get("stop_buffer_pct"), p["stop_buffer_pct"], 0, 10)
    p["min_stop_atr"] = _num(o.get("min_stop_atr"), p["min_stop_atr"], 0, 5)
    p["require_profitable"] = bool(o.get("require_profitable", p["require_profitable"]))
    p["require_analyst_buy"] = bool(o.get("require_analyst_buy", p["require_analyst_buy"]))
    p["require_market_uptrend"] = bool(o.get("require_market_uptrend", p["require_market_uptrend"]))
    p["earnings_drop_days"] = _num(o.get("earnings_drop_days"), p["earnings_drop_days"], 0, 60, int)
    p["ticket_eur"] = _num(o.get("ticket_eur"), p["ticket_eur"], 1, 1e7)
    p["max_risk_eur"] = _num(o.get("max_risk_eur"), p["max_risk_eur"], 1, 1e6)

    p["cash_eur"] = _num(o.get("cash_eur"), p["cash_eur"], 0, 1e9)
    p["current_open_risk_eur"] = _num(o.get("current_open_risk_eur"),
                                      p["current_open_risk_eur"], 0, 1e9)
    p["sector_cap_pct"] = _num(o.get("sector_cap_pct"), p["sector_cap_pct"], 5, 100)
    p["max_open_risk_pct"] = _num(o.get("max_open_risk_pct"), p["max_open_risk_pct"], 0.5, 100)
    p["commission_eur"] = _num(o.get("commission_eur"), p["commission_eur"], 0, 1000)
    p["spread_bps"] = _num(o.get("spread_bps"), p["spread_bps"], 0, 500)
    p["max_friction_pct"] = _num(o.get("max_friction_pct"), p["max_friction_pct"], 0, 100)
    p["show_near"] = bool(o.get("show_near", p["show_near"]))

    holdings = o.get("holdings", p["holdings"])
    clean_h = []
    if isinstance(holdings, (list, tuple)):
        for h in holdings[:100]:
            try:
                t = str(h["ticker"]).strip().upper()
                sh = float(h["shares"])
                cb = float(h.get("cost", 0) or 0)
                if t and sh > 0:
                    clean_h.append({"ticker": t, "shares": sh, "cost": cb})
            except (KeyError, TypeError, ValueError):
                continue
    p["holdings"] = clean_h

    exclude = o.get("exclude", p["exclude"])
    if isinstance(exclude, (list, tuple)):
        exclude = ",".join(exclude)
    p["exclude"] = str(exclude)
    return p


def _exclude_set(p: dict) -> set[str]:
    return {t.strip().upper() for t in p["exclude"].replace(";", ",").split(",") if t.strip()}


# ----------------------- cached market data -----------------------
_cache: dict = {
    "universe_key": None, "universe": None, "universe_ts": 0.0,
    "ohlc_key": None, "ohlc": None, "ohlc_ts": 0.0,
    "bench": None, "bench_ts": 0.0,   # region -> Close series
    "info": {},       # ticker -> (ts, info dict)
    "earnings": {},   # ticker -> (ts, days_to_earnings | None)
    "finnhub": {},    # ticker -> (ts, days_to_earnings | None)
    "fhf": {},        # ticker -> (ts, finnhub fundamentals dict | None)
}


def clear_cache():
    _cache.update(universe_key=None, universe=None, universe_ts=0.0,
                  ohlc_key=None, ohlc=None, ohlc_ts=0.0,
                  bench=None, bench_ts=0.0, info={}, earnings={}, finnhub={}, fhf={})
    cache_store.clear()


def _fresh(ts: float, ttl: float = CACHE_TTL) -> bool:
    return time.time() - ts < ttl


def build_universe(p: dict, progress=print) -> list[str]:
    min_cap = p["min_mkt_cap_b"] * 1e9
    key = (p["include_us"], p["include_eu"], tuple(p["sectors"]), round(min_cap),
           p["universe_max"])
    if _cache["universe_key"] == key and _fresh(_cache["universe_ts"]):
        age = int((time.time() - _cache["universe_ts"]) / 60)
        progress(f"Reusing cached universe ({len(_cache['universe'])} tickers, {age}m old).")
        return _cache["universe"]
    hit, stored = cache_store.fetch(f"universe:{key}", CACHE_TTL)
    if hit:
        progress(f"Loaded universe from disk cache ({len(stored)} tickers).")
        _cache.update(universe_key=key, universe=stored, universe_ts=time.time())
        return stored

    us_syms: list[str] = []
    eu_syms: list[str] = []
    try:
        if p["include_us"]:
            for sector in p["sectors"]:
                if _rate_limited_now("screen"):
                    progress("  Yahoo rate limit hit — skipping remaining US sector queries")
                    break
                q = yf.EquityQuery("and", [
                    yf.EquityQuery("eq", ["region", "us"]),
                    yf.EquityQuery("eq", ["sector", sector]),
                    yf.EquityQuery("gt", ["intradaymarketcap", min_cap]),
                ])
                try:
                    ok, res = _yahoo_call(lambda q=q: yf.screen(
                        q, size=250, sortField="intradaymarketcap", sortAsc=False),
                        scope="screen")
                    if not ok:
                        progress(f"  us/{sector}: rate-limited")
                        continue
                    got = [x["symbol"] for x in res.get("quotes", [])]
                    progress(f"  us/{sector}: {len(got)}")
                    us_syms += got
                except Exception as e:
                    progress(f"  us/{sector} failed: {e}")
        if p["include_eu"]:
            # sector filtered later via info (Yahoo's EU sector tagging is spotty)
            for region in EU_REGIONS:
                if _rate_limited_now("screen"):
                    progress("  Yahoo rate limit hit — skipping remaining EU region queries")
                    break
                q = yf.EquityQuery("and", [
                    yf.EquityQuery("eq", ["region", region]),
                    yf.EquityQuery("gt", ["intradaymarketcap", min_cap]),
                ])
                try:
                    ok, res = _yahoo_call(lambda q=q: yf.screen(
                        q, size=100, sortField="intradaymarketcap", sortAsc=False),
                        scope="screen")
                    if not ok:
                        progress(f"  {region}: rate-limited")
                        continue
                    got = [x["symbol"] for x in res.get("quotes", [])]
                    progress(f"  {region}: {len(got)}")
                    eu_syms += got
                except Exception as e:
                    progress(f"  {region} failed: {e}")
    except AttributeError:
        progress("  yf.screen unavailable — update yfinance: pip install -U yfinance")
    if not us_syms and not eu_syms:
        # Yahoo gave us nothing (usually a rate limit): a stale universe from a
        # past run beats the bundled list, which beats failing outright.
        # the stale universe wins at any size: the disk-cached OHLC is keyed
        # to it, so this chain is what keeps a full-Yahoo-outage scan alive
        hit, stale = cache_store.fetch(f"universe:{key}", STALE_OK)
        if hit and stale:
            progress(f"  Yahoo screener unavailable — reusing last known universe "
                     f"from disk ({len(stale)} tickers, may be stale).")
            _cache.update(universe_key=key, universe=stale, universe_ts=time.time())
            return stale
        us_syms = list(universe_static.US_CORE) if p["include_us"] else []
        eu_syms = list(universe_static.EU_CORE) if p["include_eu"] else []
        progress(f"  Yahoo screener unavailable — using the built-in large-cap "
                 f"universe ({len(us_syms) + len(eu_syms)} tickers; size/sector "
                 f"re-checked from live data during the scan).")
        if not us_syms and not eu_syms:
            us_syms = list(FALLBACK_UNIVERSE)
    elif _rate_limited_now("screen") and len(us_syms) + len(eu_syms) < 150:
        # the screener died partway: top up the partial result with the
        # bundled list so a half-blocked run doesn't shrink the scan
        before = len(us_syms) + len(eu_syms)
        if p["include_us"]:
            us_syms += universe_static.US_CORE
        if p["include_eu"]:
            eu_syms += universe_static.EU_CORE
        progress(f"  screener queries were cut short at {before} tickers — "
                 f"topped up with the built-in large-cap universe.")

    def dedupe(lst, seen):
        out = []
        for t in lst:
            if t and t not in seen:
                seen.add(t)
                out.append(t)
        return out

    seen: set = set()
    us, eu = dedupe(us_syms, seen), dedupe(eu_syms, seen)
    # cap the universe (rate-limit + memory safety); each query is already
    # sorted by market cap desc, so truncation keeps the largest names,
    # split proportionally between US and EU
    cap = p["universe_max"]
    if len(us) + len(eu) > cap:
        us_keep = round(cap * len(us) / (len(us) + len(eu)))
        us, eu = us[:us_keep], eu[:cap - us_keep]
        progress(f"  universe capped at {cap} (largest caps kept: {len(us)} US, {len(eu)} EU)")
    out = us + eu
    _cache.update(universe_key=key, universe=out, universe_ts=time.time())
    if not _rate_limited_now("screen"):  # don't overwrite a full stale universe with a partial one
        cache_store.put(f"universe:{key}", out)
    return out


def _get_info(ticker: str) -> dict:
    """Fundamentals dict, or {} when Yahoo won't give us one right now.

    Never raises — this runs inside portfolio valuation and stage 2 of the
    scan, and a single rate-limited call must not abort the whole run. When
    live fetch fails, serves the last disk-cached info at any age (sector and
    market cap drift slowly enough for stale to be useful).
    """
    mem = _cache["info"].get(ticker)
    if mem and _fresh(mem[0], INFO_TTL):
        return mem[1]
    hit, stored = cache_store.fetch(f"info:{ticker}", INFO_TTL)
    if hit:
        _cache["info"][ticker] = (time.time(), stored)
        return stored
    try:
        ok, info = _yahoo_call(lambda: yf.Ticker(ticker).info, scope="quote")
    except Exception:
        ok, info = False, None
    if ok:
        info = info or {}
        _cache["info"][ticker] = (time.time(), info)
        cache_store.put(f"info:{ticker}", info)
        return info
    hit, stale = cache_store.fetch(f"info:{ticker}", STALE_OK)
    return stale if hit and isinstance(stale, dict) else {}


def _get_days_to_earnings(ticker: str) -> int | None:
    mem = _cache["earnings"].get(ticker)
    if mem and _fresh(mem[0], INFO_TTL):
        return mem[1]
    hit, stored = cache_store.fetch(f"earn:{ticker}", INFO_TTL)
    if hit:
        _cache["earnings"][ticker] = (time.time(), stored)
        return stored
    try:
        ok, ed = _yahoo_call(lambda: yf.Ticker(ticker).get_earnings_dates(limit=4),
                             scope="quote")
    except Exception:
        ok, ed = True, None  # genuine "no data" (e.g. ETFs) — cacheable as None
    if not ok:
        # rate-limited: serve any stale date, and don't cache the failure —
        # a poisoned None would suppress the earnings gate for a full TTL
        hit, stale = cache_store.fetch(f"earn:{ticker}", STALE_OK)
        return stale if hit else None
    days = None
    try:
        if ed is not None:
            future = ed[ed.index > pd.Timestamp.now(tz=ed.index.tz)]
            if len(future):
                days = (future.index.min() - pd.Timestamp.now(tz=ed.index.tz)).days
    except Exception:
        pass
    _cache["earnings"][ticker] = (time.time(), days)
    cache_store.put(f"earn:{ticker}", days)
    return days


def _get_benchmarks(progress=print) -> dict:
    """Region -> benchmark Close series (or None if unavailable)."""
    if _cache["bench"] is not None and _fresh(_cache["bench_ts"]):
        return _cache["bench"]
    hit, stored = cache_store.fetch("bench", CACHE_TTL)
    if hit:
        _cache.update(bench=stored, bench_ts=time.time())
        return stored
    bench, data = {}, None
    try:
        ok, data = _yahoo_call(lambda: yf.download(
            list(BENCHMARKS.values()), period="1y", auto_adjust=True,
            group_by="ticker", threads=True, progress=False), scope="chart")
        if not ok:
            data = None
    except Exception as e:
        progress(f"  benchmark download failed ({e})")
    if data is not None:
        for region, sym in BENCHMARKS.items():
            try:
                close = data[sym]["Close"].dropna()
                bench[region] = close if len(close) >= 200 else None
            except Exception:
                bench[region] = None
    if not any(v is not None for v in bench.values()):
        hit, stale = cache_store.fetch("bench", STALE_OK)
        if hit and isinstance(stale, dict) and any(v is not None for v in stale.values()):
            progress("  benchmark download unavailable — using last cached "
                     "benchmarks (stale) for regime/RS checks")
            bench = stale
        else:
            progress("  benchmark data unavailable — regime/RS checks disabled")
            bench = {region: None for region in BENCHMARKS}
    _cache.update(bench=bench, bench_ts=time.time())
    if data is not None and any(v is not None for v in bench.values()):
        cache_store.put("bench", bench)
    return bench


def _region(ticker: str) -> str:
    return "EU" if "." in ticker else "US"


def market_uptrend(close: pd.Series | None) -> bool | None:
    if close is None or len(close) < 200:
        return None
    return bool(float(close.iloc[-1]) > float(close.rolling(200).mean().iloc[-1]))


def rel_strength(close: pd.Series, bench_close: pd.Series | None, days: int = 63) -> float | None:
    """Stock return minus benchmark return over `days` bars, in pct points."""
    if bench_close is None or len(close) < days + 1 or len(bench_close) < days + 1:
        return None
    sr = float(close.iloc[-1] / close.iloc[-days - 1] - 1)
    br = float(bench_close.iloc[-1] / bench_close.iloc[-days - 1] - 1)
    return round((sr - br) * 100, 1)


def atr(hist: pd.DataFrame, period: int = 14) -> float | None:
    h, l, c = hist["High"], hist["Low"], hist["Close"]
    pc = c.shift(1)
    tr = pd.concat([h - l, (h - pc).abs(), (l - pc).abs()], axis=1).max(axis=1)
    v = float(tr.rolling(period).mean().iloc[-1])
    return v if np.isfinite(v) and v > 0 else None


def pullback_volume_ratio(vol: pd.Series, recent: int = 10, base: int = 30) -> float | None:
    """Recent volume vs the prior base period. < 1 = quiet pullback (healthy),
    > 1 = selling on rising volume (distribution risk)."""
    if len(vol) < recent + base:
        return None
    r = float(vol.tail(recent).mean())
    b = float(vol.iloc[-(recent + base):-recent].mean())
    return round(r / b, 2) if b > 0 else None


def _finnhub_fundamentals(ticker: str) -> dict | None:
    """Free fallback for company basics + analyst consensus when Yahoo's
    quote API is blocked (finnhub.io free tier; reliable mainly for US
    symbols). Returns {"marketCap", "name", "rec_mean", "analysts_n"} or None.
    """
    if not FINNHUB_KEY:
        return None
    mem = _cache["fhf"].get(ticker)
    if mem and _fresh(mem[0], INFO_TTL):
        return mem[1]
    hit, stored = cache_store.fetch(f"fhf:{ticker}", INFO_TTL)
    if hit:
        _cache["fhf"][ticker] = (time.time(), stored)
        return stored
    out = None
    try:
        r = requests.get("https://finnhub.io/api/v1/stock/profile2",
                         params={"symbol": ticker, "token": FINNHUB_KEY}, timeout=10)
        prof = r.json() if r.status_code == 200 else {}
        r2 = requests.get("https://finnhub.io/api/v1/stock/recommendation",
                          params={"symbol": ticker, "token": FINNHUB_KEY}, timeout=10)
        recs = r2.json() if r2.status_code == 200 else []
        rec_mean = analysts_n = None
        if isinstance(recs, list) and recs:
            latest = recs[0]
            counts = [(1, latest.get("strongBuy") or 0), (2, latest.get("buy") or 0),
                      (3, latest.get("hold") or 0), (4, latest.get("sell") or 0),
                      (5, latest.get("strongSell") or 0)]
            total = sum(n for _, n in counts)
            if total:
                rec_mean = round(sum(w * n for w, n in counts) / total, 2)
                analysts_n = total
        mc = (prof or {}).get("marketCapitalization")  # reported in millions
        if mc or rec_mean is not None:
            out = {"marketCap": mc * 1e6 if mc else None,
                   "name": (prof or {}).get("name"),
                   "rec_mean": rec_mean, "analysts_n": analysts_n}
    except Exception:
        out = None
    _cache["fhf"][ticker] = (time.time(), out)
    cache_store.put(f"fhf:{ticker}", out)
    return out


def _finnhub_days_to_earnings(ticker: str) -> int | None:
    """Free cross-check of the next earnings date (US symbols only on the free
    tier). Returns None when no key is set, on any error, or no date found."""
    if not FINNHUB_KEY or _region(ticker) != "US":
        return None
    mem = _cache["finnhub"].get(ticker)
    if mem and _fresh(mem[0], INFO_TTL):
        return mem[1]
    hit, stored = cache_store.fetch(f"fh:{ticker}", INFO_TTL)
    if hit:
        _cache["finnhub"][ticker] = (time.time(), stored)
        return stored
    days = None
    try:
        today = pd.Timestamp.now().normalize()
        r = requests.get(
            "https://finnhub.io/api/v1/calendar/earnings",
            params={"from": today.strftime("%Y-%m-%d"),
                    "to": (today + pd.Timedelta(days=90)).strftime("%Y-%m-%d"),
                    "symbol": ticker, "token": FINNHUB_KEY},
            timeout=10)
        events = (r.json() or {}).get("earningsCalendar", [])
        dates = [pd.Timestamp(e["date"]) for e in events if e.get("date")]
        future = [d for d in dates if d >= today]
        if future:
            days = int((min(future) - today).days)
    except Exception:
        pass
    _cache["finnhub"][ticker] = (time.time(), days)
    cache_store.put(f"fh:{ticker}", days)
    return days


# ----------------------- indicators -----------------------
def rsi(series: pd.Series, period: int = 14) -> float:
    delta = series.diff()
    gain = delta.clip(lower=0).rolling(period).mean()
    loss = (-delta.clip(upper=0)).rolling(period).mean()
    rs = gain / loss.replace(0, np.nan)
    return float((100 - 100 / (1 + rs)).iloc[-1])


def last_pivot_low(low: pd.Series, k: int) -> float | None:
    """Most recent bar that is the minimum of its +/- k neighbourhood."""
    vals = low.values
    for i in range(len(vals) - k - 1, k, -1):
        window = vals[i - k : i + k + 1]
        if vals[i] == window.min():
            return float(vals[i])
    return None


# ----------------------- portfolio -----------------------
def _to_eur(value: float, ticker: str) -> float:
    """Rough currency normalization: US listings are USD, EU listings are
    treated as EUR-equivalent (CHF/GBP/DKK strays accepted as approximation)."""
    return value / EURUSD if _region(ticker) == "US" else value


def _last_close(container, ticker: str) -> float | None:
    try:
        return float(container[ticker]["Close"].dropna().iloc[-1])
    except Exception:
        try:
            return float(container["Close"].dropna().iloc[-1])
        except Exception:
            return None


def _portfolio_state(p: dict, data, universe: list[str], progress=print) -> dict | None:
    """Value the user's holdings and derive book size, sector weights, and the
    remaining aggregate risk budget. Returns None when no portfolio is given."""
    if not p["holdings"] and p["cash_eur"] <= 0:
        return None
    extra = {}
    missing = [h["ticker"] for h in p["holdings"]
               if data is None or h["ticker"] not in universe]
    if missing:
        try:
            ok, d2 = _yahoo_call(lambda: yf.download(
                missing, period="5d", auto_adjust=True,
                group_by="ticker", threads=True, progress=False), scope="chart")
            if ok and d2 is not None:
                for t in missing:
                    px = _last_close(d2, t)
                    if px is not None:
                        extra[t] = px
        except Exception:
            pass

    positions, sector_val = {}, {}
    for h in p["holdings"]:
        t = h["ticker"]
        price = extra.get(t)
        if price is None and data is not None:
            price = _last_close(data, t)
        if price is None:
            progress(f"  portfolio: no price for {t} — position ignored in book math")
            continue
        value_eur = _to_eur(price * h["shares"], t)
        sector = _get_info(t).get("sector") or "?"
        pnl_pct = round((price / h["cost"] - 1) * 100, 1) if h["cost"] > 0 else None
        positions[t] = {"shares": h["shares"], "price": price, "value_eur": round(value_eur, 2),
                        "sector": sector, "pnl_pct": pnl_pct}
        sector_val[sector] = sector_val.get(sector, 0.0) + value_eur

    book_eur = p["cash_eur"] + sum(pos["value_eur"] for pos in positions.values())
    risk_budget_eur = None
    if book_eur > 0:
        risk_budget_eur = round(book_eur * p["max_open_risk_pct"] / 100
                                - p["current_open_risk_eur"], 2)
    port = {"positions": positions, "cash_eur": p["cash_eur"],
            "book_eur": round(book_eur, 2), "sector_val": sector_val,
            "risk_budget_eur": risk_budget_eur,
            "sector_weights": {s: round(v / book_eur * 100, 1)
                               for s, v in sector_val.items()} if book_eur > 0 else {}}
    progress(f"Portfolio: {len(positions)} positions, book €{book_eur:,.0f} "
             f"(cash €{p['cash_eur']:,.0f}), remaining risk budget "
             f"{'n/a' if risk_budget_eur is None else f'€{risk_budget_eur:,.0f}'}")
    return port


def _analyst_label(mean: float | None) -> str | None:
    """Map Yahoo's 1-5 consensus mean to the familiar broker-app wording."""
    if mean is None:
        return None
    if mean <= 1.5:
        return "Strong Buy"
    if mean <= 2.5:
        return "Buy"
    if mean <= 3.5:
        return "Hold"
    if mean <= 4.5:
        return "Sell"
    return "Strong Sell"


# ----------------------- scoring -----------------------
def _clamp01(x: float) -> float:
    return min(max(x, 0.0), 1.0)


def score_row(row: dict, p: dict) -> tuple[int, str]:
    """0-100 composite quality score + a one-line human rationale.

    Weights: R:R (30%), relative strength vs benchmark (15%), pullback depth
    within the RSI band (15%), entry proximity to support (15%), pullback
    volume character (10%), distance to earnings (7.5%), analyst consensus
    (7.5%, neutral when unknown). Each data-quality flag costs 5 points.
    """
    rr_score = min(row["RR"] / 5.0, 1.0)
    span = max(p["rsi_high"] - p["rsi_low"], 1e-9)
    pullback = _clamp01((p["rsi_high"] - row["RSI"]) / span)
    dist_to_support = (row["price"] - row["support"]) / row["price"]
    support_prox = 1.0 - min(dist_to_support / 0.05, 1.0)  # within 5% of support = best
    dte = row.get("days_to_earnings")
    earn = 0.7 if dte is None else min(dte / 30.0, 1.0)
    rs = row.get("rs_3m")
    rs_score = 0.5 if rs is None else _clamp01((rs + 10.0) / 20.0)   # -10%..+10% -> 0..1
    vr = row.get("vol_ratio")
    vol_score = 0.5 if vr is None else _clamp01((1.5 - vr) / 1.0)    # 0.5x -> 1, 1.5x -> 0
    am = row.get("analyst_mean")
    an_score = 0.5 if am is None else _clamp01((4.0 - am) / 3.0)     # 1.0 -> 1, 4.0 -> 0

    flags = [f for f in str(row.get("flags") or "").split(",") if f]
    score = round(100 * (0.30 * rr_score + 0.15 * rs_score + 0.15 * pullback +
                         0.15 * support_prox + 0.10 * vol_score +
                         0.075 * earn + 0.075 * an_score)
                  - 5 * len(flags))
    score = max(min(score, 100), 0)

    bits = [f"R:R {row['RR']:.1f}",
            f"entry {dist_to_support * 100:.1f}% above support",
            f"RSI {row['RSI']:.0f}"]
    if rs is not None:
        bits.append(f"RS vs mkt {rs:+.1f}%")
    if vr is not None:
        bits.append(f"pullback vol {vr:.2f}x" + (" (quiet)" if vr < 1 else ""))
    bits.append("earnings date unknown" if dte is None else f"earnings in {dte}d")
    if row.get("analyst"):
        bits.append(f"analysts: {row['analyst']}")
    if flags:
        bits.append("⚠ " + ", ".join(flags))
    return score, " · ".join(bits)


# ----------------------- the scan -----------------------
def run_screener(params: dict | None = None, progress=print, on_partial=None) -> dict:
    """Full scan. `params` overrides DEFAULTS (see clean_params).

    Returns {"df": DataFrame, "rejections": {ticker: reason},
             "universe_size": int, "elapsed_s": float, "params": dict}.
    """
    p = clean_params(params)
    exclude = _exclude_set(p)
    allowed_sectors = set(p["sectors"])
    t0 = time.time()
    rl_hits_start = _rl["hits"]
    cooling = [s for s in ("screen", "chart", "quote") if _rate_limited_now(s)]
    if cooling:
        progress(f"Note: Yahoo rate-limit cooldown active for {', '.join(cooling)} "
                 f"calls — this run will lean on cached data there.")

    progress("Building universe via Yahoo sector screener...")
    universe = build_universe(p, progress)
    progress(f"Universe: {len(universe)} tickers  [{time.time()-t0:.0f}s]")

    rows = []
    near: list[tuple[dict, list]] = []   # (row, [(gate_key, reason), ...])
    rejections: dict[str, str] = {}

    def reject(t, reason):
        rejections[t] = reason

    bench = _get_benchmarks(progress)
    regime = {region: market_uptrend(close) for region, close in bench.items()}
    for region, up in regime.items():
        label = {True: "UPTREND", False: "DOWNTREND", None: "unknown"}[up]
        progress(f"Market regime {region} ({BENCHMARKS[region]}): {label}")
    if p["require_market_uptrend"] and not any(v for v in regime.values()):
        if all(v is False for v in regime.values()):
            progress("Both benchmarks below SMA200 — regime gate will reject everything. "
                     "Untick 'require market uptrend' to override.")

    port = _portfolio_state(p, None, universe, progress)

    t1 = time.time()
    scanned_n = [0]

    def scan_block(tickers, frame):
        """Run every gate for one batch of tickers against its price frame."""
        for ticker in tickers:
            scanned_n[0] += 1
            if scanned_n[0] % 100 == 0 or scanned_n[0] == len(universe):
                progress(f"  scanning {scanned_n[0]}/{len(universe)}  "
                         f"qualified so far: {len(rows)}  [{time.time()-t1:.0f}s]")
            if ticker.split(".")[0].upper() in exclude:
                continue
            try:
                try:
                    hist = frame[ticker].dropna()
                except Exception:
                    reject(ticker, "no data")
                    continue
                if len(hist) < 210:
                    reject(ticker, "insufficient history")
                    continue

                price = float(hist["Close"].iloc[-1])
                sma200 = float(hist["Close"].rolling(200).mean().iloc[-1])
                avg_vol = float(hist["Volume"].tail(30).mean())
                region = _region(ticker)

                # ---- hard gates: without these there is no trade plan at all ----
                if price <= sma200:
                    reject(ticker, "not in uptrend")
                    continue
                support = last_pivot_low(hist["Low"].tail(120), p["pivot_k"])
                if support is None or support >= price:
                    reject(ticker, "no valid pivot support below price")
                    continue
                stop = support * (1 - p["stop_buffer_pct"] / 100)
                resistance = float(hist["High"].tail(p["swing_lookback"]).max())
                if resistance <= price:
                    reject(ticker, "price at/above swing high (no target)")
                    continue
                risk_ps = price - stop
                reward_ps = resistance - price
                rr = reward_ps / risk_ps if risk_ps > 0 else float("nan")
                if not np.isfinite(rr):
                    reject(ticker, "degenerate risk math")
                    continue

                # ---- Stage 1 soft gates: a failure here still leaves a complete
                # ---- trade plan, so collect misses instead of discarding — the
                # ---- best of them go on the "closest to qualifying" board.
                misses: list[tuple[str, str]] = []
                r = rsi(hist["Close"])
                dollar_vol = avg_vol * price
                a = atr(hist)
                stop_atr = round(risk_ps / a, 2) if a else None
                if p["require_market_uptrend"] and regime.get(region) is False:
                    misses.append(("regime", f"market regime ({BENCHMARKS[region]} below SMA200)"))
                if dollar_vol < p["min_dollar_vol_m"] * 1e6:
                    misses.append(("liquidity", f"liquidity (${dollar_vol/1e6:.0f}M/day)"))
                if not (p["rsi_low"] <= r <= p["rsi_high"]):
                    misses.append(("rsi", f"RSI {r:.0f} outside {p['rsi_low']:.0f}-{p['rsi_high']:.0f}"))
                if rr < p["min_rr"]:
                    misses.append(("min_rr", f"R:R {rr:.1f} < {p['min_rr']:.1f}"))
                if stop_atr is not None and stop_atr < p["min_stop_atr"]:
                    misses.append(("stop_atr", f"stop inside noise ({stop_atr:.1f} ATR < {p['min_stop_atr']:.1f})"))

                rs_3m = rel_strength(hist["Close"], bench.get(region))
                vol_ratio = pullback_volume_ratio(hist["Volume"])
                flags: list[str] = []

                def near_row(extra_flags=(), sector=None, name=None):
                    """Slim row for the near-miss board (no expensive calls)."""
                    fx = EURUSD if region == "US" else 1.0
                    shares = round(min(p["max_risk_eur"] * fx / risk_ps,
                                       p["ticket_eur"] * fx / price), 4)
                    row = {"ticker": ticker, "name": name or ticker, "sector": sector or "?",
                           "mktcap_b": None, "analyst": None, "analyst_mean": None,
                           "analyst_target_up_pct": None,
                           "price": round(price, 2), "support": round(support, 2),
                           "stop": round(stop, 2), "resistance": round(resistance, 2),
                           "RR": round(rr, 2), "RSI": round(r, 1),
                           "rs_3m": rs_3m, "vol_ratio": vol_ratio, "stop_atr": stop_atr,
                           "shares": shares,
                           "risk_EUR": round(_to_eur(shares * risk_ps, ticker), 2),
                           "days_to_earnings": None,
                           "dollar_vol_m": round(dollar_vol / 1e6, 1),
                           "flags": ",".join(list(extra_flags))}
                    score, _ = score_row(row, p)
                    row["score"] = max(score - NEAR_MISS_PENALTY * len(misses), 0)
                    row["why_not"] = "; ".join(m[1] for m in misses)
                    return row

                if misses:
                    reject(ticker, misses[0][1])   # keeps the rejection summary honest
                    if p["show_near"]:
                        near.append((near_row(extra_flags=["gates_skipped"]), list(misses)))
                    continue

                # ---- Stage 2: expensive per-ticker calls, survivors only ----
                progress(f"    [stage 2] {ticker}: technical setup found, fetching fundamentals...")
                info = _get_info(ticker)
                fh = None
                if not info:
                    # Yahoo quote API blocked: try the free Finnhub fallback
                    # before flagging the pick as unverified. The technical
                    # setup is real either way — never falsely reject it.
                    fh = _finnhub_fundamentals(ticker)
                    flags.append("fundamentals_via_finnhub" if fh
                                 else "fundamentals_unavailable")
                fh = fh or {}
                mktcap = info.get("marketCap") or fh.get("marketCap") or 0
                fwd_eps = info.get("forwardEps")
                sector = info.get("sector")
                # analyst consensus — the same indicator broker apps (Revolut
                # etc.) show, via Yahoo's aggregation of sell-side research
                rec_mean = info.get("recommendationMean") or fh.get("rec_mean")
                analysts_n = (info.get("numberOfAnalystOpinions")
                              or fh.get("analysts_n"))
                rec_label = _analyst_label(rec_mean)
                tgt_mean = info.get("targetMeanPrice")
                tgt_up_pct = (round((tgt_mean / price - 1) * 100, 1)
                              if tgt_mean and price > 0 else None)

                # ---- structural filters (fundamental) ----
                if sector in SECTOR_EXCLUDE:
                    reject(ticker, f"excluded sector ({sector})")
                    continue
                if sector and sector not in allowed_sectors:
                    reject(ticker, f"sector not selected ({sector})")
                    continue
                def near_reject(gate, reason):
                    reject(ticker, reason)
                    if p["show_near"]:
                        misses.append((gate, reason))
                        row = near_row(extra_flags=flags, sector=sector,
                                       name=info.get("shortName"))
                        # fundamentals are already in hand at stage 2 — show them
                        row["mktcap_b"] = round(mktcap / 1e9, 1) if mktcap else None
                        row["analyst"] = (f"{rec_label} ({analysts_n})"
                                          if rec_label and analysts_n else rec_label)
                        row["analyst_mean"] = rec_mean
                        row["analyst_target_up_pct"] = tgt_up_pct
                        near.append((row, list(misses)))

                if (info or fh) and mktcap and mktcap < p["min_mkt_cap_b"] * 1e9:
                    near_reject("mkt_cap", f"mkt cap {mktcap/1e9:.1f}B < min")
                    continue
                # profitability gate with a free fallback: Yahoo's forwardEps is
                # often missing for EU names — fall back to trailing EPS (flagged)
                # instead of falsely rejecting as "unprofitable".
                eps_used = fwd_eps
                if eps_used is None and info.get("trailingEps") is not None:
                    eps_used = info["trailingEps"]
                    flags.append("eps_fallback")
                if (p["require_profitable"] and info
                        and (eps_used is None or eps_used <= 0)):
                    near_reject("profitable", f"unprofitable/no EPS data (fwd_eps={fwd_eps})")
                    continue
                if (p["require_analyst_buy"] and rec_mean is not None
                        and rec_mean > 2.5):
                    near_reject("analyst", f"analyst consensus {rec_label} ({rec_mean:.1f})")
                    continue

                # ---- earnings proximity (Yahoo, cross-checked via free Finnhub) ----
                days_to_earnings = _get_days_to_earnings(ticker)
                days_fh = _finnhub_days_to_earnings(ticker)
                if days_to_earnings is None and days_fh is not None:
                    days_to_earnings = days_fh
                    flags.append("earnings_from_finnhub")
                elif (days_to_earnings is not None and days_fh is not None
                      and abs(days_to_earnings - days_fh) > 2):
                    days_to_earnings = min(days_to_earnings, days_fh)  # conservative
                    flags.append("earnings_sources_disagree")
                if days_to_earnings is None:
                    flags.append("earnings_unverified")
                if days_to_earnings is not None and days_to_earnings <= p["earnings_drop_days"]:
                    near_reject("earnings", f"earnings in {days_to_earnings}d")
                    continue

                # ---- sizing off YOUR risk, not a fixed ticket ----
                fx = EURUSD if region == "US" else 1.0  # EUR budget -> listing currency
                shares_by_risk = p["max_risk_eur"] * fx / risk_ps
                shares_by_ticket = p["ticket_eur"] * fx / price
                shares = min(shares_by_risk, shares_by_ticket)

                # ---- portfolio awareness (only when a portfolio was provided) ----
                status = "NEW"
                sector_after = None
                if port is not None:
                    held = port["positions"].get(ticker)
                    if held:
                        status = ("ADD" if held["pnl_pct"] is None
                                  else f"ADD ({held['pnl_pct']:+.1f}%)")
                    pos_value_eur = _to_eur(shares * price, ticker)
                    if port["cash_eur"] <= 0:
                        flags.append("no_cash")
                    elif pos_value_eur > port["cash_eur"]:
                        shares *= port["cash_eur"] / pos_value_eur
                        flags.append("cash_capped")
                    pos_value_eur = _to_eur(shares * price, ticker)
                    if sector and port["book_eur"] > 0:
                        sector_after = round((port["sector_val"].get(sector, 0.0) + pos_value_eur)
                                             / port["book_eur"] * 100, 1)
                        if sector_after > p["sector_cap_pct"]:
                            flags.append("sector_cap")

                shares = round(shares, 4)
                actual_risk_eur = round(_to_eur(shares * risk_ps, ticker), 2)

                # ---- net-of-cost economics: block trades friction eats alive ----
                pos_value_eur = _to_eur(shares * price, ticker)
                target_profit_eur = _to_eur(shares * reward_ps, ticker)
                cost_eur = 2 * p["commission_eur"] + p["spread_bps"] / 1e4 * pos_value_eur
                friction_pct = (round(cost_eur / target_profit_eur * 100, 1)
                                if target_profit_eur > 0 else None)
                if (p["max_friction_pct"] > 0 and friction_pct is not None
                        and friction_pct > p["max_friction_pct"]):
                    near_reject("friction", f"friction (costs {friction_pct:.0f}% of target "
                                            f"profit > {p['max_friction_pct']:.0f}%)")
                    continue

                row = {
                    "ticker": ticker,
                    "name": info.get("shortName") or fh.get("name") or ticker,
                    "sector": sector or "?",
                    "mktcap_b": round(mktcap / 1e9, 1) if mktcap else None,
                "analyst": (f"{rec_label} ({analysts_n})" if rec_label and analysts_n
                            else rec_label),
                "analyst_mean": rec_mean,
                "analyst_target_up_pct": tgt_up_pct,
                    "price": round(price, 2),
                    "support": round(support, 2), "stop": round(stop, 2),
                    "resistance": round(resistance, 2),
                    "RR": round(rr, 2), "RSI": round(r, 1),
                    "rs_3m": rs_3m, "vol_ratio": vol_ratio, "stop_atr": stop_atr,
                    "status": status, "sector_after": sector_after,
                    "friction_pct": friction_pct,
                    "shares": shares, "risk_EUR": actual_risk_eur,
                    "days_to_earnings": days_to_earnings,
                    "flags": ",".join(flags),
                }
                row["score"], row["rationale"] = score_row(row, p)
                rows.append(row)
            except Exception as e:
                reject(ticker, f"data error: {e}")

    def emit_partial():
        if on_partial is not None:
            try:
                on_partial([dict(r) for r in rows], scanned_n[0], len(universe))
            except Exception:
                pass

    # ---- prices: cached frame -> one fast pass; otherwise download and scan
    # ---- batch by batch so results appear while the rest still downloads
    key = hashlib.md5(",".join(universe).encode()).hexdigest()
    data = None
    if _cache["ohlc_key"] == key and _fresh(_cache["ohlc_ts"]):
        age = int((time.time() - _cache["ohlc_ts"]) / 60)
        progress(f"Reusing cached 1y OHLC ({age}m old) — filter-only rerun, fast.")
        data = _cache["ohlc"]
    else:
        hit, stored = cache_store.fetch(f"ohlc:{key}", CACHE_TTL)
        if hit:
            progress("Loaded 1y OHLC from disk cache — no re-download needed.")
            _cache.update(ohlc_key=key, ohlc=stored, ohlc_ts=time.time())
            data = stored

    if data is not None:
        scan_block(list(universe), data)
    else:
        n_chunks = (len(universe) + DOWNLOAD_CHUNK - 1) // DOWNLOAD_CHUNK
        progress(f"Downloading & scanning 1y prices for {len(universe)} tickers in "
                 f"{n_chunks} batches of up to {DOWNLOAD_CHUNK} — results appear "
                 f"below as each batch lands...")
        parts, failed = [], 0
        for ci in range(n_chunks):
            chunk = universe[ci * DOWNLOAD_CHUNK:(ci + 1) * DOWNLOAD_CHUNK]
            if ci and not _rate_limited_now("chart"):
                time.sleep(1)  # don't fire batches back-to-back at Yahoo
            d = None
            try:
                ok, d = _yahoo_call(lambda chunk=chunk: yf.download(
                    chunk, period="1y", auto_adjust=True,
                    group_by="ticker", threads=16, progress=False), scope="chart")
                if not ok:
                    d = None
            except Exception as e:
                progress(f"  batch {ci+1}/{n_chunks} failed: {e}")
                d = None
            if d is None or d.empty:
                failed += 1
                scanned_n[0] += len(chunk)
                progress(f"  batch {ci+1}/{n_chunks}: no data (Yahoo throttled?) — skipped")
            else:
                if not isinstance(d.columns, pd.MultiIndex):  # single-ticker shape
                    d = pd.concat({chunk[0]: d}, axis=1)
                parts.append(d)
                scan_block(chunk, d)
                progress(f"  batch {ci+1}/{n_chunks}: done — "
                         f"{len(rows)} qualified so far")
            emit_partial()
        if parts:
            data = pd.concat(parts, axis=1) if len(parts) > 1 else parts[0]
            _cache.update(ohlc_key=key, ohlc=data, ohlc_ts=time.time())
            try:  # never overwrite a complete stale set with a partial download
                if failed == 0 and float(data.memory_usage().sum()) < 150e6:
                    cache_store.put(f"ohlc:{key}", data)
            except Exception:
                pass
        else:
            # rate-limited into a corner: yesterday's prices still beat no scan
            hit, stale = cache_store.fetch(f"ohlc:{key}", STALE_OK)
            if hit and stale is not None and not getattr(stale, "empty", True):
                progress("  every price download failed — reusing last cached OHLC "
                         "from disk (stale; rerun later for fresh prices).")
                _cache.update(ohlc_key=key, ohlc=stale, ohlc_ts=time.time())
                data = stale
                scan_block(list(universe), stale)
                emit_partial()
            else:
                raise RuntimeError(
                    "every price download failed — Yahoo Finance is likely rate-limiting "
                    "or blocking this server right now; wait a few minutes and rerun "
                    "(on a fresh server, a smaller 'Max stocks to scan' also helps: "
                    "fewer tickers = fewer requests)")

    elapsed = time.time() - t0
    rl_hits = _rl["hits"] - rl_hits_start
    if rl_hits:
        progress(f"⚠ Yahoo rate-limited {rl_hits} call(s) this scan — served "
                 f"cached/stale data where possible; affected results carry "
                 f"'fundamentals_unavailable' or 'earnings_unverified' flags. "
                 f"Rerun in a few minutes for fresh data.")
    # ---- second chance for fundamentals: the quote throttle usually clears
    # ---- within a minute and survivors are few, so retry them once instead
    # ---- of shipping a page full of 'not verified' flags
    flagged = [r["ticker"] for r in rows
               if "fundamentals_unavailable" in (r.get("flags") or "")]
    if data is not None and flagged and len(flagged) <= 30:
        wait = _rl["until"].get("quote", 0.0) - time.time()
        if 0 < wait <= QUOTE_RETRY_MAX_WAIT:
            progress(f"Fundamentals were throttled for {len(flagged)} pick(s) — "
                     f"waiting {int(wait) + 1}s for Yahoo's quote window to reset, "
                     f"then re-verifying...")
            time.sleep(wait + 1)
        if not _rate_limited_now("quote"):
            keep = set(flagged)
            rows[:] = [r for r in rows if r["ticker"] not in keep]
            near[:] = [(row, m) for row, m in near if row["ticker"] not in keep]
            for t in keep:
                rejections.pop(t, None)
            progress(f"Re-verifying fundamentals for {len(keep)} pick(s)...")
            scan_block(sorted(keep), data)
            emit_partial()

    # ---- "closest to qualifying" board + one-click relaxation hints ----
    near_rows: list[dict] = []
    relax_hints: dict = {}
    if near:
        near.sort(key=lambda x: x[0]["score"], reverse=True)
        near_rows = [row for row, _ in near[:NEAR_MAX]]
        # fundamentals for the board itself — only the shown rows, so at most
        # NEAR_MAX quote calls, all behind the rate-limit breaker
        for row in near_rows:
            if row["sector"] == "?":
                info = _get_info(row["ticker"])
                if not info:
                    fh = _finnhub_fundamentals(row["ticker"])
                    if fh:
                        row["name"] = fh.get("name") or row["name"]
                        mc = fh.get("marketCap")
                        row["mktcap_b"] = round(mc / 1e9, 1) if mc else None
                        rm = fh.get("rec_mean")
                        lbl, n_an = _analyst_label(rm), fh.get("analysts_n")
                        row["analyst"] = f"{lbl} ({n_an})" if lbl and n_an else lbl
                        row["analyst_mean"] = rm
                if info:
                    row["name"] = info.get("shortName") or row["name"]
                    row["sector"] = info.get("sector") or "?"
                    mc = info.get("marketCap")
                    row["mktcap_b"] = round(mc / 1e9, 1) if mc else row["mktcap_b"]
                    rm = info.get("recommendationMean")
                    lbl, n_an = _analyst_label(rm), info.get("numberOfAnalystOpinions")
                    row["analyst"] = f"{lbl} ({n_an})" if lbl and n_an else lbl
                    row["analyst_mean"] = rm
                    tm = info.get("targetMeanPrice")
                    if tm and row["price"] > 0:
                        row["analyst_target_up_pct"] = round((tm / row["price"] - 1) * 100, 1)
        buckets: dict[str, list] = {}
        for row, m in near:
            if len(m) == 1:   # a single filter change would qualify these
                buckets.setdefault(m[0][0], []).append(row)
        if "min_rr" in buckets:
            best = max(r["RR"] for r in buckets["min_rr"])
            relax_hints["min_rr"] = {"n": len(buckets["min_rr"]),
                                     "set_to": int(best * 10) / 10}
        if "rsi" in buckets:
            vals = [r["RSI"] for r in buckets["rsi"]]
            relax_hints["rsi"] = {"n": len(buckets["rsi"]),
                                  "lo": min(int(p["rsi_low"]), int(min(vals))),
                                  "hi": max(int(p["rsi_high"]), int(max(vals)) + 1)}
        if "stop_atr" in buckets:
            best = max(r["stop_atr"] or 0 for r in buckets["stop_atr"])
            relax_hints["stop_atr"] = {"n": len(buckets["stop_atr"]),
                                       "set_to": int(best * 20) / 20}
        if "liquidity" in buckets:
            best = max(r.get("dollar_vol_m") or 0 for r in buckets["liquidity"])
            relax_hints["liquidity"] = {"n": len(buckets["liquidity"]),
                                        "set_to": max(int(best), 0)}
        if "regime" in buckets:
            relax_hints["regime"] = {"n": len(buckets["regime"])}
        if "analyst" in buckets:
            relax_hints["analyst"] = {"n": len(buckets["analyst"])}
        progress(f"Nearly qualified: {len(near)} setup(s) failed at least one gate — "
                 f"showing the top {len(near_rows)} with reasons.")

    if not FINNHUB_KEY and any("fundamentals_unavailable" in (r.get("flags") or "")
                               for r in rows):
        progress("Tip: Yahoo's fundamentals API is blocked from this server. Add a "
                 "free FINNHUB_API_KEY (finnhub.io — free signup, no card) as an "
                 "environment variable and the screener will pull company data and "
                 "analyst ratings from Finnhub automatically.")
    progress(f"Scan complete in {elapsed:.0f}s total.")
    df = pd.DataFrame(rows)
    if len(df):
        df = df.sort_values("score", ascending=False).reset_index(drop=True)
        # aggregate risk budget: walking down the ranking, how far does the
        # book's remaining risk capacity reach? (#4: portfolio-level risk cap)
        if port is not None and port["risk_budget_eur"] is not None:
            cum = df["risk_EUR"].cumsum()
            df["cum_risk_EUR"] = cum.round(2)
            df["fits_risk_budget"] = cum <= port["risk_budget_eur"]
            n_fit = int(df["fits_risk_budget"].sum())
            progress(f"Risk budget €{port['risk_budget_eur']:,.0f} covers the "
                     f"top {n_fit} of {len(df)} setups.")
    port_summary = None
    if port is not None:
        port_summary = {"book_eur": port["book_eur"], "cash_eur": port["cash_eur"],
                        "risk_budget_eur": port["risk_budget_eur"],
                        "n_positions": len(port["positions"]),
                        "sector_weights": port["sector_weights"]}
    return {"df": df, "rejections": rejections, "universe_size": len(universe),
            "elapsed_s": elapsed, "params": p, "portfolio": port_summary,
            "near": near_rows, "relax_hints": relax_hints}


def summarize_rejections(rejections: dict[str, str], progress=print):
    if not rejections:
        return
    reasons = Counter(v.split(" (")[0] for v in rejections.values())
    progress("\n--- Rejection summary ---")
    for reason, n in reasons.most_common():
        progress(f"  {n:4d}  {reason}")
    interesting = {t: w for t, w in rejections.items()
                   if "R:R" in w or "earnings" in w or "unprofitable" in w}
    if interesting:
        progress("\n--- Near-misses (had a setup, failed a gate) ---")
        for t, why in interesting.items():
            progress(f"  {t:8s} {why}")


if __name__ == "__main__":
    result = run_screener()
    df = result["df"]
    if len(df):
        df.to_csv("screener_results.csv", index=False)
        top = df.head(3)
        print("\n=== TOP PICKS TODAY ===")
        for _, r in top.iterrows():
            print(f"  {r['ticker']:10s} score {r['score']:3d}  {r['rationale']}")
        print()
        print(df.drop(columns=["rationale"]).to_string(index=False))
    else:
        print("No setups matched today.")
    summarize_rejections(result["rejections"])
