"""The scheduled publisher must produce files the site can actually read.

This is the job that replaces "visitor presses Run and waits five
minutes": it runs in CI and writes one JSON file per preset. It is tested
here with the screener stubbed, so the logic is verified without a network
round trip — the same reason the site can serve results without one.
"""
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TMP = tempfile.mkdtemp(prefix="sched_")
sys.path.insert(0, str(ROOT))

import db
import screener

OUT = Path(TMP) / "published"

# A stub screener: one preset is made to fail, to prove a single failure
# does not take the whole publish down with it.
STUB = f'''
import sys
sys.path.insert(0, {str(ROOT)!r})
import pandas as pd
import screener


def fake_run(p, progress=print, on_partial=None):
    progress("stub scan")
    if p["min_rr"] == 2.0:                       # the "wide-net" preset
        raise RuntimeError("simulated download failure")
    n = 3 if p["min_rr"] >= 3.0 else 7           # relaxed rules find more
    df = pd.DataFrame([
        {{"ticker": f"ZZ{{i}}", "name": f"Fake {{i}}", "score": 70 - i,
          "price": 100.0 + i, "stop": 95.0, "resistance": 115.0, "RR": 3.0,
          "sector": "Technology", "shares": 1.0, "risk_EUR": 20.0,
          "flags": "", "RSI": 44.0}}
        for i in range(n)])
    return {{"df": df, "rejections": {{"AAA": "not in uptrend",
                                     "BBB": "not in uptrend",
                                     "CCC": "liquidity ($12M/day)"}},
            "universe_size": 1500, "elapsed_s": 12, "params": p,
            "portfolio": None, "near": [], "relax_hints": {{}}, "pending": [],
            "breadth": {{"pct": 58, "risk_factor": 1.0}},
            "health": {{"blocked_unverified": 0}}}}


screener.run_screener = fake_run
# The publisher now also asks for benchmark closes to publish market regime
# (screener._get_benchmarks). Left unmocked that is a real Yahoo call from
# every one of this file's subprocesses — this file's whole point is that
# the logic is verified without a network round trip, so it is stubbed
# empty here, the same as run_screener above. The regime-specific test
# further down replaces this one line with real synthetic closes.
screener._get_benchmarks = lambda progress=print: {{}}
import runpy
import backtest as _bt


def fake_sim(p, data, universe, progress=print, reuse=True):
    progress("stub simulation")
    return {{"n": 900, "n_stocks": 1000, "profit_factor": 1.07,
            "curve": [{{"date": "2022-01-03", "cum_r": 0}}],
            "portfolio": {{"slots": 5, "profit_factor": 1.07, "n": 238,
                          "win_rate_pct": 39, "mdd_pct": 18.4, "sortino": 0.3,
                          "return_pct": 11.2}},
            "spy": {{"return_pct": 98.6, "mdd_pct": 18.8}}}}


_bt.run_backtest = fake_sim
sys.argv = ["scheduled_scan.py", "--out", {str(OUT)!r}, "--universe-max", "1500"]
runpy.run_path({str(ROOT / "scripts" / "scheduled_scan.py")!r}, run_name="__main__")
'''

script = Path(TMP) / "drive.py"
script.write_text(STUB)
env = dict(os.environ, MARKET_DB=f"{TMP}/m.db", JOURNAL_DB=f"{TMP}/j.db",
           SCREENER_CACHE_DB=f"{TMP}/c.db",
           SCAN_RETRY_WAIT_S="0")      # the retry is tested, not its wall clock
proc = subprocess.run([sys.executable, str(script)], env=env,
                      capture_output=True, text=True, timeout=300)
assert proc.returncode == 0, f"publisher failed:\n{proc.stdout}\n{proc.stderr}"

# ---- the two healthy presets published, the failing one did not ----
written = sorted(p.name for p in OUT.glob("*.json"))
assert written == ["balanced.json", "index.json", "relaxed.json"], written

index = json.loads((OUT / "index.json").read_text())
assert [p["preset"] for p in index["presets"]] == ["balanced", "relaxed"], index
assert len(index["failures"]) == 1, index["failures"]
assert index["failures"][0]["preset"] == "wide-net"
assert "simulated download failure" in index["failures"][0]["error"]
print("one preset failing did not lose the others — and is reported, not hidden")

# ---- each payload carries everything the site needs to display it ----
for name, expect_n in (("balanced", 3), ("relaxed", 7)):
    d = json.loads((OUT / f"{name}.json").read_text())
    for k in ("results", "top_picks", "params_used", "results_ts",
              "universe_size", "scan_hash", "breadth", "rejection_summary"):
        assert d.get(k) is not None, f"{name} missing {k}"
    assert len(d["results"]) == expect_n, (name, len(d["results"]))
    assert len(d["top_picks"]) == min(3, expect_n)
    assert d["universe_size"] == 1500
    assert d["results"][0]["ticker"] == "ZZ0"
    # the hash must be the one the app looks scans up by, or the site will
    # never match a published preset to the filters on screen
    assert d["scan_hash"] == db.scan_hash(screener.clean_params(d["params_used"])), name
    # rejections must arrive aggregated, not as a per-ticker dump
    reasons = {r["reason"]: r["count"] for r in d["rejection_summary"]}
    assert reasons.get("not in uptrend") == 2, reasons
    assert reasons.get("liquidity") == 1, reasons
print("payloads carry results, filters, a matching hash and aggregated rejections")

# ---- the simulation is published with the picks ----
for name in ("balanced", "relaxed"):
    d = json.loads((OUT / f"{name}.json").read_text())
    bt = d.get("backtest")
    assert bt, f"{name} published no simulation — the analytics half stays blank"
    assert bt["portfolio"]["profit_factor"] == 1.07
    assert "curve" not in bt, "the full-signal curve is dead weight in the payload"
    assert d["bt_rules"], "a published simulation must say which rules produced it"
    assert set(d["bt_rules"]) == set(db.TECH_PARAMS)
assert all(p["has_simulation"] for p in index["presets"]), index
print("simulations published alongside the picks, tagged with their rules")

# --no-simulation must skip it, for a picks-only run
FAST = STUB.replace('"--universe-max", "1500"', '"--universe-max", "1500", "--no-simulation"')
OUT3 = Path(TMP) / "published3"
s3 = Path(TMP) / "drive_fast.py"
s3.write_text(FAST.replace(str(OUT), str(OUT3)))
p3 = subprocess.run([sys.executable, str(s3)], env=env, capture_output=True,
                    text=True, timeout=300)
assert p3.returncode == 0, p3.stderr
d3 = json.loads((OUT3 / "balanced.json").read_text())
assert "backtest" not in d3, "--no-simulation must publish picks only"
print("--no-simulation publishes picks only, as intended")

# the presets must actually differ, or publishing three is pointless
bal = json.loads((OUT / "balanced.json").read_text())
rel = json.loads((OUT / "relaxed.json").read_text())
assert bal["scan_hash"] != rel["scan_hash"], "presets must be distinguishable"
assert bal["params_used"]["min_rr"] > rel["params_used"]["min_rr"]
print("presets are distinct filter sets, filed under different hashes")

# ---- market regime is published alongside the picks ----
# screener.run() has always computed and gated on this internally
# (require_market_uptrend) but never published the verdict, so /today could
# rank five buy plans during a confirmed downtrend with nothing anywhere
# saying the backdrop had turned. Real synthetic closes here (not the empty
# stub the other runs use): 200 rising points for US, 200 falling points
# for EU, so market_uptrend has an unambiguous answer for each.
REGIME = STUB.replace(
    "screener._get_benchmarks = lambda progress=print: {}",
    "_us_close = pd.Series([float(x) for x in range(200, 400)])\n"
    "_eu_close = pd.Series([float(x) for x in range(400, 200, -1)])\n"
    "screener._get_benchmarks = lambda progress=print: "
    "{'US': _us_close, 'EU': _eu_close}")
assert "_eu_close" in REGIME, "regime stub not wired"
OUT6 = Path(TMP) / "published6"
s6 = Path(TMP) / "drive_regime.py"
s6.write_text(REGIME.replace(str(OUT), str(OUT6)))
p6 = subprocess.run([sys.executable, str(s6)], env=env, capture_output=True,
                    text=True, timeout=300)
assert p6.returncode == 0, p6.stderr
reg = json.loads((OUT6 / "regime.json").read_text())
assert reg["US"] is True, reg      # ends 200 points above its own start
assert reg["EU"] is False, reg     # ends 200 points below its own start
idx6 = json.loads((OUT6 / "index.json").read_text())
assert idx6["regime"] == {"US": True, "EU": False}, idx6["regime"]
print("market regime (US uptrend, EU downtrend) published to regime.json, "
      "and carried into index.json")

# ---- when every preset fails, publish nothing and exit non-zero ----
ALL_FAIL = STUB.replace('if p["min_rr"] == 2.0:', "if True:")
s2 = Path(TMP) / "drive_fail.py"
OUT2 = Path(TMP) / "published2"
s2.write_text(ALL_FAIL.replace(str(OUT), str(OUT2)))
p2 = subprocess.run([sys.executable, str(s2)], env=env,
                    capture_output=True, text=True, timeout=300)
assert p2.returncode == 1, "a total failure must exit non-zero so CI goes red"
idx2 = json.loads((OUT2 / "index.json").read_text())
assert idx2["presets"] == [] and len(idx2["failures"]) == 3
assert not list(OUT2.glob("balanced.json")), "nothing should be published"
print("a total failure publishes nothing and fails the job loudly")

# ---- an UPSTREAM outage must not report as a defect ----
# Yahoo throttling recurs by nature. Reporting it as a failed job trains
# the reader to ignore the alert, and the real breakage gets ignored too.
UPSTREAM = STUB.replace(
    'if p["min_rr"] == 2.0:                       # the "wide-net" preset\n        raise RuntimeError("simulated download failure")',
    'if True:\n        raise RuntimeError("every price download failed — Yahoo is rate-limiting")')
assert "rate-limiting" in UPSTREAM, "upstream stub not wired"
OUT4 = Path(TMP) / "published4"
s4 = Path(TMP) / "drive_upstream.py"
s4.write_text(UPSTREAM.replace(str(OUT), str(OUT4)))
p4 = subprocess.run([sys.executable, str(s4)], env=env, capture_output=True,
                    text=True, timeout=600)
assert p4.returncode == 75, (p4.returncode, p4.stderr[-400:])
assert "not a defect" in p4.stderr, p4.stderr[-300:]
idx4 = json.loads((OUT4 / "index.json").read_text())
assert idx4["presets"] == [] and len(idx4["failures"]) == 3
print("an upstream outage exits 75 (warn), not 1 (broken)")

# and a genuine code defect still exits 1
BROKEN = STUB.replace(
    'if p["min_rr"] == 2.0:                       # the "wide-net" preset\n        raise RuntimeError("simulated download failure")',
    'if True:\n        raise TypeError("unsupported operand type")')
OUT5 = Path(TMP) / "published5"
s5 = Path(TMP) / "drive_broken.py"
s5.write_text(BROKEN.replace(str(OUT), str(OUT5)))
p5 = subprocess.run([sys.executable, str(s5)], env=env, capture_output=True,
                    text=True, timeout=600)
assert p5.returncode == 1, (p5.returncode, p5.stderr[-300:])
print("a genuine defect still exits 1 (fails the job)")

print("\nALL SCHEDULED-SCAN TESTS PASSED")

# ---- the price book must not be truncated alphabetically ----
# The first version walked data.columns.levels[0], which pandas returns
# SORTED, and sliced it. A cap of 1,200 over a 1,500-name scan therefore
# dropped everything after roughly the letter T rather than the 300 least
# liquid names. WM, WCN and XPO were all on the published board and all
# missing from the book, so the check answered "overlap could not be
# measured" for exactly the names at the end of the alphabet.
import numpy as _np
import pandas as _pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import scheduled_scan

_n = 120
_small = [f"A{i:03d}" for i in range(60)]
_big = ["WM", "WCN", "XPO"]
_idx = _pd.bdate_range(end=_pd.Timestamp.today(), periods=_n)
_frame = _pd.concat({t: _pd.DataFrame(
    {"Open": _np.linspace(10, 20, _n), "High": _np.linspace(10, 21, _n),
     "Low": _np.linspace(9, 19, _n), "Close": _np.linspace(10, 20, _n),
     "Volume": _np.full(_n, 1e6)}, index=_idx) for t in _big + _small}, axis=1)

# the scan's universe is ordered largest-first, so the big names lead
screener._cache.update(ohlc=_frame, universe=_big + _small)
_book = scheduled_scan.price_book(max_tickers=30, days=60)
# the published book is one shared calendar plus one aligned series per
# ticker; correlating without the calendar lines columns up by row position
assert set(_book) == {"dates", "series"}, sorted(_book)
assert len(_book["dates"]) == 60, len(_book["dates"])
_series = _book["series"]
assert len(_series) == 30, len(_series)
assert all(len(v) == 60 for v in _series.values()), \
    "every series must be the same length as the calendar, nulls included"
assert set(_big) <= set(_series), \
    f"a cap must drop the smallest names, not the end of the alphabet: {sorted(_series)[:5]}"
print(f"price book cap keeps the largest names: {_big} survived a cap of 30 over "
      f"{len(_big) + len(_small)} tickers")

# and with no universe order recorded it must still return something usable
screener._cache.update(ohlc=_frame, universe=None)
_fallback = scheduled_scan.price_book(max_tickers=30, days=60)
assert len(_fallback["series"]) == 30, len(_fallback["series"])
print("with no universe order recorded it still fills the book rather than emptying it")

# a ticker with too little history is omitted, never padded
_short = _pd.concat({"TINY": _pd.DataFrame(
    {"Open": [1.0] * 10, "High": [1.0] * 10, "Low": [1.0] * 10,
     "Close": [1.0] * 10, "Volume": [1e6] * 10},
    index=_pd.bdate_range(end=_pd.Timestamp.today(), periods=10))}, axis=1)
screener._cache.update(ohlc=_short, universe=["TINY"])
assert scheduled_scan.price_book(days=60) == {}, "10 closes cannot fill a 60-day book"
print("a ticker with less than the full window is omitted, not padded")

print("\nPRICE-BOOK ORDERING PINNED")


# ---- the volatility book: one float per ticker, all the history ----
# The price book carries 60 closes because 1,500 x 60 is already 600KB.
# The credit model needs an annualised volatility, and 60 returns
# estimates one badly. A volatility is a single number, so it can be
# computed over everything the scan holds and published at full length.
_days = 500
_vidx = _pd.bdate_range(end=_pd.Timestamp.today(), periods=_days)


def _wiggle(vol_annual, seed):
    """A price path with a KNOWN annualised volatility."""
    rng = _np.random.default_rng(seed)
    step = vol_annual / _np.sqrt(252.0)
    return 100.0 * _np.exp(_np.cumsum(rng.normal(0, step, _days)))


_paths = {"CALM": _wiggle(0.12, 1), "MID": _wiggle(0.30, 2),
          "WILD": _wiggle(0.75, 3)}
_vframe = _pd.concat({t: _pd.DataFrame(
    {"Open": p, "High": p, "Low": p, "Close": p,
     "Volume": _np.full(_days, 1e6)}, index=_vidx)
    for t, p in _paths.items()}, axis=1)
screener._cache.update(ohlc=_vframe, universe=list(_paths))

_vols = scheduled_scan.volatility_book()
assert set(_vols) == set(_paths), _vols
for _t, _target in (("CALM", 0.12), ("MID", 0.30), ("WILD", 0.75)):
    _got = _vols[_t]["vol"]
    assert abs(_got - _target) < _target * 0.12, \
        f"{_t}: measured {_got:.3f} against a built-in {_target:.2f}"
assert _vols["CALM"]["vol"] < _vols["MID"]["vol"] < _vols["WILD"]["vol"]
_shown = ", ".join("%s %.2f" % (t, v["vol"]) for t, v in _vols.items())
print("volatility book recovers the volatility it was given: " + _shown)

# it must rest on far more history than the 60-close price book
assert all(v["obs"] >= 400 for v in _vols.values()), _vols
assert all(v["as_of"] for v in _vols.values()), "each figure carries its date"
print(f"each figure rests on {_vols['MID']['obs']} returns, not 60")

# a flat series is not a zero-risk stock, and a short one is not measurable
_flat = _pd.concat({"FLAT": _pd.DataFrame(
    {"Open": [5.0] * _days, "High": [5.0] * _days, "Low": [5.0] * _days,
     "Close": [5.0] * _days, "Volume": [1e6] * _days}, index=_vidx)}, axis=1)
screener._cache.update(ohlc=_flat, universe=["FLAT"])
assert scheduled_scan.volatility_book() == {}, \
    "a series that never moves must be omitted, not published as zero risk"

screener._cache.update(ohlc=_short, universe=["TINY"])
assert scheduled_scan.volatility_book() == {}, \
    "10 closes cannot support an annualised volatility"
print("a flat series and a short series are both omitted rather than published")

# and the whole book must stay small enough to fetch on a 512MB instance
import json as _json
screener._cache.update(ohlc=_vframe, universe=list(_paths))
_per = len(_json.dumps(scheduled_scan.volatility_book())) / 3
assert _per < 120, f"{_per:.0f} bytes per ticker would be 180KB at 1,500 names"
print(f"{_per:.0f} bytes per ticker — about {_per * 1500 / 1024:.0f}KB for a full scan")

print("\nVOLATILITY BOOK PINNED")


# ---- the credit book: built here, because the SEC refuses the web host ----
# Measured: every SEC call from the Render instance times out, while the
# same request from another address answers in 0.3s. The scan already runs
# on a runner with a fresh IP for exactly this reason with Yahoo, so the
# filings are read here too and the standing is published.
_cb_calls = []


def _fake_sec_get(url, timeout=20):
    _cb_calls.append(url)
    if "company_tickers" in url:
        return {"fields": ["cik", "name", "ticker", "exchange"],
                "data": [[1, "Alpha", "AAA", "NYSE"], [2, "Beta", "BBB", "NYSE"]]}
    cik = int(url.split("CIK")[1][:10])
    lev = 0.3 if cik == 1 else 1.4          # Beta is far more levered
    if url.endswith("Liabilities.json"):
        return {"units": {"USD": [{"form": "10-Q", "end": "2026-06-30",
                                   "val": lev * 1e11}]}}
    if url.endswith("LiabilitiesCurrent.json"):
        return {"units": {"USD": [{"form": "10-Q", "end": "2026-06-30",
                                   "val": lev * 0.4e11}]}}
    if url.endswith("EntityCommonStockSharesOutstanding.json"):
        return {"units": {"shares": [{"form": "10-Q", "end": "2026-07-20",
                                      "val": 1e9}]}}
    return {"units": {}}


scheduled_scan._sec_get = _fake_sec_get
# every fake company is an industrial unless a test says otherwise —
# without this the guard would try a real streamed read of sec.gov
scheduled_scan._sic_of = lambda cik: "3711"
_cb_prices = {"dates": DATES if False else [f"d{i}" for i in range(60)],
              "series": {"AAA": [100.0] * 60, "BBB": [100.0] * 60}}
_cb_vols = {"AAA": {"vol": 0.25, "obs": 900}, "BBB": {"vol": 0.25, "obs": 900}}
_cb = scheduled_scan.credit_book(["AAA", "BBB"], _cb_prices, _cb_vols, now=1_000_000)

assert set(_cb) == {"AAA", "BBB"}, _cb
assert _cb["AAA"]["dd"] > _cb["BBB"]["dd"], \
    f"the less levered company must sit further from default: {_cb}"
assert all(r["vol_source"] == "published" for r in _cb.values()), _cb
assert all(r["built"] == 1_000_000 for r in _cb.values()), _cb
print(f"credit book measured both names off filings: AAA {_cb['AAA']['dd']}, "
      f"BBB {_cb['BBB']['dd']}")

# a company with no published prices cannot be valued, and is skipped
# rather than reported without a market value
_no_price = scheduled_scan.credit_book(
    ["AAA", "ZZZ"], {"dates": _cb_prices["dates"],
                     "series": {"AAA": _cb_prices["series"]["AAA"]}}, _cb_vols,
    now=1_000_000)
assert set(_no_price) == {"AAA"}, _no_price
print("a name with no published prices is skipped, not valued without one")

# ---- the book accumulates across runs, and drops what has gone stale ----
# One run cannot measure the whole liquid universe — that is a quarter of
# an hour of SEC calls. So each run refreshes what fits in its budget and
# carries the rest forward. What it must NOT carry is a standing whose
# price has moved on: the distance is a market value against a balance
# sheet, so a week-old entry is a week-old market capitalisation.
_prev = {"OLD": {"ticker": "OLD", "dd": 4.0, "sic": "3711", "capck": True,
                 "built": 1_000_000 - 30 * 86400},
         "KEPT": {"ticker": "KEPT", "dd": 6.0, "sic": "3711", "capck": True,
                  "built": 1_000_000 - 3600}}
_merged = scheduled_scan.credit_book(["AAA"], _cb_prices, _cb_vols,
                                     prev=_prev, now=1_000_000)
assert "KEPT" in _merged, "a recent standing must be carried forward"
assert "OLD" not in _merged, \
    "a standing older than the freshness limit carries a stale market value"
assert "AAA" in _merged, "this run's measurements must still land"
print(f"the book carries {len(_merged)} forward: recent entries kept, entries "
      f"older than {scheduled_scan.CREDIT_MAX_AGE_S // 86400} days dropped")

# the least recently measured go first, so coverage rotates instead of
# re-measuring the same names every hour
_wide_prices = {"dates": _cb_prices["dates"],
                "series": {t: [100.0] * 60 for t in ("AAA", "BBB")}}
_stale_first = scheduled_scan.credit_book(
    [], _wide_prices, _cb_vols,
    prev={"BBB": {"ticker": "BBB", "dd": 1.0, "sic": "3711", "capck": True,
                  "built": 1_000_000 - scheduled_scan.CREDIT_REFRESH_S - 60},
          "AAA": {"ticker": "AAA", "dd": 1.0, "sic": "3711", "capck": True,
                  "built": 1_000_000 - 60}},
    max_names=1, now=1_000_000)
assert _stale_first["BBB"]["built"] == 1_000_000, \
    "the longest-unmeasured name must be the one refreshed"
assert _stale_first["AAA"]["built"] == 1_000_000 - 60, "the fresh one is left"
print("each run refreshes whatever has gone longest without measuring")

# ---- and a name measured recently must not be measured AGAIN ----
# This is the bug that froze coverage at 97 companies. Every run walked
# the board first, re-measured all of it to pick up a newer share price,
# hit its budget, and never reached a single company it had not already
# seen. The site now re-solves the stored filing against the latest close
# on read, so those calls bought nothing at all — they cost the SEC
# budget that should have gone to company number 98.
_seen_urls = []


def _counting_sec(url, timeout=20):
    _seen_urls.append(url)
    return _fake_sec_get(url)


scheduled_scan._sec_get = _counting_sec
_fresh = {"AAA": {"ticker": "AAA", "dd": 1.0, "sic": "3711", "capck": True,
                  "built": 1_000_000 - 60},
          "BBB": {"ticker": "BBB", "dd": 1.0, "sic": "3711", "capck": True,
                  "built": 1_000_000 - 60}}
_seen_urls.clear()
_skipped = scheduled_scan.credit_book(["AAA", "BBB"], _cb_prices, _cb_vols,
                                      prev=_fresh, now=1_000_000)
_filing_calls = [u for u in _seen_urls if "companyconcept" in u]
assert not _filing_calls, f"re-read filings it already had: {_filing_calls[:3]}"
assert _skipped["AAA"]["built"] == 1_000_000 - 60, "the stored entry stands"
assert set(_skipped) == {"AAA", "BBB"}
print("a name measured hours ago costs no SEC call at all")

# so the budget goes where it is worth spending: names never measured
_widen_prices = {"dates": _cb_prices["dates"],
                 "series": {"AAA": [100.0] * 60, "BBB": [100.0] * 60}}
_widened = scheduled_scan.credit_book(
    ["AAA"], _widen_prices, _cb_vols,
    prev={"AAA": {"ticker": "AAA", "dd": 1.0, "sic": "3711", "capck": True,
                  "built": 1_000_000 - 60}},
    max_names=1, now=1_000_000)
assert _widened["BBB"]["built"] == 1_000_000, \
    "the one measurement this run could afford went to the name it had, " \
    "not the one it was missing"
print("the one call it could afford went to the company it did not have")

scheduled_scan._sec_get = _fake_sec_get

# a budget the run cannot exceed, whatever the SEC does
_slow_calls = {"n": 0}


def _slow_sec(url, timeout=20):
    _slow_calls["n"] += 1
    if "company_tickers" in url:
        return _fake_sec_get(url)
    time.sleep(0.2)
    return _fake_sec_get(url)


import time
scheduled_scan._sec_get = _slow_sec
_t0 = time.time()
scheduled_scan.credit_book(["AAA", "BBB"], _cb_prices, _cb_vols,
                           budget_s=0.5, now=1_000_000)
assert time.time() - _t0 < 8, f"the budget was not honoured ({time.time()-_t0:.1f}s)"
print("a slow SEC cannot push the run past its budget")


# and a SEC that refuses must stop the walk rather than grind through it
def _refusing(url, timeout=20):
    if "company_tickers" in url:
        return _fake_sec_get(url)
    raise TimeoutError("SEC refused")


scheduled_scan._sec_get = _refusing
_many = [f"T{i}" for i in range(30)]
_prices_many = {"dates": _cb_prices["dates"],
                "series": {t: [100.0] * 60 for t in _many}}
_out = scheduled_scan.credit_book(_many, _prices_many, {}, now=1_000_000)
assert _out == {}, _out
print("a refusing SEC stops the credit book instead of hammering it")


# ---- a bank or insurer is published as a refusal, not a number ----
# One streamed read of the SIC, no balance-sheet calls at all, and the
# entry carries the reason so the report page can show it.
_sic_asked = []


def _sic_financial(cik):
    _sic_asked.append(cik)
    return "6324" if cik == 2 else "3711"


scheduled_scan._sec_get = _fake_sec_get
scheduled_scan._sic_of = _sic_financial
_fin = scheduled_scan.credit_book(["AAA", "BBB"], _cb_prices, _cb_vols,
                                  now=1_000_000)
assert _fin["AAA"]["dd"] is not None, "the industrial is still measured"
assert _fin["BBB"]["dd"] is None and _fin["BBB"]["not_modelled"], _fin["BBB"]
assert "not modelled" in _fin["BBB"]["verdict"]
assert _fin["BBB"]["sic"] == "6324" and _fin["BBB"]["built"] == 1_000_000
print("an insurer is published as 'not modelled', with the reason attached")

# entries built before the sector guard existed carry no "sic" and must
# be re-measured, not carried — that is how MOH leaves the book
_pre_guard = {"MOH": {"ticker": "MOH", "dd": 2.38, "built": 1_000_000 - 60}}
_purged = scheduled_scan.credit_book([], _cb_prices, _cb_vols,
                                     prev=_pre_guard, now=1_000_000)
assert "MOH" not in _purged or _purged["MOH"].get("sic"), _purged.get("MOH")
print("a pre-guard entry is not carried forward on trust")

# and a refusal is remembered like a measurement: no SEC calls next run
_sic_asked.clear()
_kept = scheduled_scan.credit_book(["BBB"], _cb_prices, _cb_vols,
                                   prev=_fin, now=1_000_000 + 3600)
assert _kept["BBB"]["not_modelled"] and not _sic_asked
print("the refusal is carried forward without asking the SEC again")

scheduled_scan._sic_of = lambda cik: "3711"


# ---- a name that cannot be measured is an answer, not a queue poison ----
# Unrecorded failures kept built=0, sorted to the FRONT of the next run's
# queue, and once five existed every run re-attempted the same five,
# tripped the miss-abort, and measured nothing new — coverage froze at
# one run's worth of names, every timestamp in the live book from a
# single run. A refusal is now stored with its reason, skipped while
# fresh like any measurement, and never counted as an SEC failure.
_dead = {"n": 0}


def _sec_dead_quote(url, timeout=20):
    if "company_tickers" in url:
        return {"fields": ["cik", "name", "ticker", "exchange"],
                "data": [[i + 1, f"N{i}", f"DQ{i}", "NYSE"] for i in range(6)]
                        + [[99, "Good", "GOOD", "NYSE"]]}
    _dead["n"] += 1
    cik = int(url.split("CIK")[1][:10])
    lev = 0.3
    if url.endswith("Liabilities.json"):
        return {"units": {"USD": [{"form": "10-Q", "end": "2026-06-30",
                                   "val": lev * 1e11}]}}
    if url.endswith("LiabilitiesCurrent.json"):
        return {"units": {"USD": [{"form": "10-Q", "end": "2026-06-30",
                                   "val": lev * 0.4e11}]}}
    if url.endswith("EntityCommonStockSharesOutstanding.json"):
        return {"units": {"shares": [{"form": "10-Q", "end": "2026-07-20",
                                      "val": 1e9}]}}
    return {"units": {}}


scheduled_scan._sec_get = _sec_dead_quote
# six stale quotes (a price that never moves) ahead of one good name
_stale_series = {f"DQ{i}": [50.0] * 60 for i in range(6)}
_stale_series["GOOD"] = [100.0 + (i % 9) * 0.7 for i in range(60)]
_dq_prices = {"dates": [f"d{i}" for i in range(60)], "series": _stale_series}
_book1 = scheduled_scan.credit_book([f"DQ{i}" for i in range(6)] + ["GOOD"],
                                    _dq_prices, {}, now=1_000_000)
assert _book1["GOOD"]["dd"] is not None, \
    "six unmeasurable names ahead of it must not stop the good one"
for i in range(6):
    e = _book1[f"DQ{i}"]
    assert e["dd"] is None and e["unmeasurable"] and e["verdict"], e
print("six dead quotes in a row do not abort the run, and each is recorded")

# next run: the recorded refusals are skipped while fresh, costing nothing
_dead["n"] = 0
_book2 = scheduled_scan.credit_book(["DQ0", "DQ1", "GOOD"], _dq_prices, {},
                                    prev=_book1, now=1_000_000 + 3600)
assert _dead["n"] == 0, f"{_dead['n']} SEC calls spent re-refusing known refusals"
assert _book2["DQ0"]["unmeasurable"]
print("a recorded refusal costs no SEC call the next run")

scheduled_scan._sec_get = _fake_sec_get

print("\nCREDIT BOOK PINNED")
