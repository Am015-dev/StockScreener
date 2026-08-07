"""The site must serve scans it did not run.

The heavy work moved to a scheduled CI job that publishes finished results
to a data branch. A page load should therefore cost a fetch, not a
five-minute scan. These tests pin the adoption logic offline: no network,
no scanning.
"""
import importlib
import json
import os
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TMP = tempfile.mkdtemp(prefix="published_")
for k, f in (("MARKET_DB", "market.db"), ("JOURNAL_DB", "journal.db"),
             ("SCREENER_CACHE_DB", "cache.db"), ("RESULTS_CSV", "results.csv")):
    os.environ[k] = os.path.join(TMP, f)
sys.path.insert(0, str(ROOT))

import screener

BALANCED = screener.clean_params({})
RELAXED = screener.clean_params({"min_rr": 2.2, "rsi_high": 62})


def payload(preset, params, n, age_s=60):
    rows = [{"ticker": f"ZZ{i}", "score": 70 - i, "price": 100.0 + i,
             "stop": 95.0, "resistance": 115.0, "RR": 3.0} for i in range(n)]
    return {"preset": preset, "results": rows, "top_picks": rows[:3],
            "near_board": [], "relax_hints": {}, "pending": [],
            "breadth": {"pct": 58, "risk_factor": 1.0},
            "health": {"blocked_unverified": 0}, "params_used": params,
            "universe_size": 1500, "scanned": 1500, "elapsed_s": 240,
            "results_ts": time.time() - age_s, "rejection_summary": [],
            "log": ["scan complete"], "scan_hash": "x"}


FILES = {
    "index.json": {"generated_at": time.time(), "failures": [], "presets": [
        {"preset": "balanced", "scan_hash": "a", "results_ts": time.time() - 600,
         "n_results": 4, "universe_size": 1500},
        {"preset": "relaxed", "scan_hash": "b", "results_ts": time.time() - 60,
         "n_results": 9, "universe_size": 1500},
    ]},
    "balanced.json": payload("balanced", BALANCED, 4, age_s=600),
    "relaxed.json": payload("relaxed", RELAXED, 9, age_s=60),
}

fetches = []


class FakeResp:
    def __init__(self, body, status=200):
        self._b, self.status_code = body, status

    def json(self):
        if self._b is None:
            raise ValueError("not json")
        return self._b


def fake_get(url, headers=None, timeout=None):
    name = url.rsplit("/", 1)[1]
    fetches.append(name)
    if name not in FILES:
        return FakeResp(None, 404)
    return FakeResp(FILES[name])


import app as app_mod

app_mod.rq = None
import requests

requests.get = fake_get
importlib.reload(app_mod)
requests.get = fake_get

# ---- startup adopts the freshest published preset, with no scan ----
st = app_mod._state
assert st["status"] == "done", st["status"]
assert st["published_preset"] == "relaxed", st["published_preset"]
assert len(st["results"]) == 9, len(st["results"])
assert st["universe_size"] == 1500
assert "index.json" in fetches and "relaxed.json" in fetches
assert "balanced.json" not in fetches, "only the chosen preset should be fetched"
log = " ".join(st["log"])
assert "no scanning was needed" in log, log
print(f"cold start adopted the '{st['published_preset']}' preset: "
      f"{len(st['results'])} picks from {st['universe_size']} stocks, no scan run")

client = app_mod.app.test_client()
s = client.get("/status").get_json()
assert s["status"] == "done" and len(s["results"]) == 9

# each published preset is stored under ITS OWN filters, so the
# filter-keyed loader serves the right one
r = client.post("/snapshot/load", json={"min_rr": 2.2, "rsi_high": 62}).get_json()
assert r["found"] is True and r["n_results"] == 9, r
print("published presets are filed under their own filters")

# ---- published results that are older than what we have are ignored ----
app_mod._state["results_ts"] = time.time() + 3600      # pretend we just scanned
assert app_mod._load_published(force=True) is False, \
    "a published scan older than the live one must not overwrite it"
print("a stale published scan cannot overwrite fresher local results")

# ---- an unreachable or malformed feed must degrade, never crash ----
def dead_get(url, headers=None, timeout=None):
    raise ConnectionError("no network")


requests.get = dead_get
assert app_mod._load_published(force=True) is False
requests.get = lambda u, headers=None, timeout=None: FakeResp(None, 500)
assert app_mod._load_published(force=True) is False
requests.get = lambda u, headers=None, timeout=None: FakeResp({"presets": []})
assert app_mod._load_published(force=True) is False
print("unreachable, erroring and empty feeds all degrade quietly")

# ---- a published payload without its filters cannot be filed ----
requests.get = lambda u, headers=None, timeout=None: FakeResp(
    FILES["index.json"] if u.endswith("index.json")
    else dict(payload("relaxed", RELAXED, 3), params_used=None))
app_mod._state["results_ts"] = 0
assert app_mod._load_published(force=True) is False, \
    "results with no filters attached must not be adopted"
print("a payload missing its filters is refused, not guessed at")

# ---- the endpoint reports what is published ----
requests.get = fake_get
app_mod._published.update(ts=0, index=None)
d = client.get("/published").get_json()
assert d["index"] and len(d["index"]["presets"]) == 2
assert d["base"].startswith("https://")
print(f"/published lists {len(d['index']['presets'])} presets")

print("\nALL PUBLISHED-RESULTS TESTS PASSED")
