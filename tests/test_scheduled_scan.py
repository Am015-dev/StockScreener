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
sys.argv = ["scheduled_scan.py", "--out", {str(OUT)!r}, "--universe-max", "1500"]
runpy.run_path({str(ROOT / "scripts" / "scheduled_scan.py")!r}, run_name="__main__")
'''

script = Path(TMP) / "drive.py"
script.write_text(STUB)
env = dict(os.environ, MARKET_DB=f"{TMP}/m.db", JOURNAL_DB=f"{TMP}/j.db",
           SCREENER_CACHE_DB=f"{TMP}/c.db")
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

print("\nALL SCHEDULED-SCAN TESTS PASSED")
