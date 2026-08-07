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
