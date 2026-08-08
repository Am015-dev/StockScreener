"""A restarted process must open with the last scan already loaded.

Renders' free tier restarts on every deploy and when the instance wakes,
so 'press Run and wait' as the only path to seeing anything is a bug, not
a limitation. This test imports the app twice against the same database
and asserts the second import serves the stored scan — and that a scan too
old to trade is withheld while its simulation is kept."""
import importlib
import os
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TMP = tempfile.mkdtemp(prefix="startup_")
os.environ["MARKET_DB"] = os.path.join(TMP, "market.db")
os.environ["JOURNAL_DB"] = os.path.join(TMP, "journal.db")
os.environ["SCREENER_CACHE_DB"] = os.path.join(TMP, "cache.db")
os.environ["RESULTS_CSV"] = os.path.join(TMP, "results.csv")
os.environ["SKIP_WARM"] = "1"
# The deploy branch SHIPS published/*.json, so a CI checkout has real
# scan results on disk that the app adopts at import — overriding
# whatever this test set up. Point the lookup somewhere empty.
os.environ["PUBLISHED_DIR"] = os.path.join(TMP, "no_published")
os.makedirs(os.environ["PUBLISHED_DIR"], exist_ok=True)
sys.path.insert(0, str(ROOT))

import db
import screener

STRICT = screener.clean_params({"min_rr": 3.0})
LOOSE = screener.clean_params({"min_rr": 2.0})

FRESH = {
    "results": [{"ticker": "ZZA", "score": 71, "price": 100.0},
                {"ticker": "ZZB", "score": 63, "price": 50.0}],
    "top_picks": [{"ticker": "ZZA", "score": 71, "price": 100.0}],
    "universe_size": 612, "scanned": 612, "elapsed_s": 180,
    "params_used": STRICT, "breadth": {"pct": 61, "risk_factor": 1.0},
    "health": {"blocked_unverified": 2},
    "backtest": {"n": 2102, "profit_factor": 1.18, "n_stocks": 614},
    "bt_status": "done",
    "results_ts": time.time() - 3600 * 2,     # two hours old: still tradeable
}

# ---- a recent scan is served on startup ----
assert db.save_snapshot(STRICT, FRESH) is True
app_mod = importlib.import_module("app")
st = app_mod._state
assert st["status"] == "done", st["status"]
assert len(st["results"]) == 2 and st["top_picks"][0]["ticker"] == "ZZA"
assert st["universe_size"] == 612
assert st["backtest"]["n"] == 2102 and st["bt_status"] == "done"
assert st["results_ts"] == FRESH["results_ts"]
log = " ".join(st["log"])
assert "Loaded the last scan from the database" in log, log
assert "2.0h old" in log, log
assert "rerun before acting" in log, log
assert "Simulation results restored" in log, log
print("cold start served the stored scan:", log.split(".")[0])

# the HTTP surface exposes it immediately, with no scan run
client = app_mod.app.test_client()
status = client.get("/status").get_json()
assert status["status"] == "done" and len(status["results"]) == 2
assert status["backtest"]["profit_factor"] == 1.18
print("/status served stored results on a cold process — no scan required")

# ---- a scan too old to trade is withheld; its simulation is not ----
STALE = dict(FRESH, results_ts=time.time() - 3600 * 40)   # 40h old
assert db.save_snapshot(STRICT, STALE) is True
importlib.reload(app_mod)
st2 = app_mod._state
assert not st2["results"], "stale entry/stop levels must not be shown"
assert st2["status"] != "done"
log2 = " ".join(st2["log"])
assert "40h old" in log2 and "gone stale" in log2, log2
assert st2["backtest"] and st2["backtest"]["n"] == 2102, \
    "the simulation is a historical record and does not go stale with prices"
assert st2["bt_status"] == "done"
print("stale scan withheld, simulation kept:", log2.split(".")[0])

# ---- loading by filters: the page asks for what it is showing ----
importlib.reload(app_mod)
client = app_mod.app.test_client()
LOOSE_SNAP = dict(FRESH, results_ts=time.time() - 600,
                  results=[{"ticker": "ZZL", "score": 55}] * 5,
                  top_picks=[{"ticker": "ZZL", "score": 55}], params_used=LOOSE)
assert db.save_snapshot(STRICT, dict(FRESH, results_ts=time.time() - 300)) is True
assert db.save_snapshot(LOOSE, LOOSE_SNAP) is True

r = client.post("/snapshot/load", json={"min_rr": 2.0}).get_json()
assert r["found"] is True and r["n_results"] == 5, r
assert client.get("/status").get_json()["results"][0]["ticker"] == "ZZL"

r = client.post("/snapshot/load", json={"min_rr": 3.0}).get_json()
assert r["found"] is True and r["n_results"] == 2, r
assert client.get("/status").get_json()["results"][0]["ticker"] == "ZZA", \
    "switching filters must show that filter set's own scan"

# filters never scanned: say so, invent nothing
r = client.post("/snapshot/load", json={"min_rr": 1.75}).get_json()
assert r["found"] is False and r["n_stored"] >= 2, r
assert "not been scanned" in r["message"]
print("filter-keyed loading OK: each filter set serves its own stored scan")

idx = client.get("/snapshot/index").get_json()["snapshots"]
assert len(idx) >= 2 and all("min_rr" in s for s in idx)
print(f"/snapshot/index lists {len(idx)} stored filter sets")

# ---- the browser mirror refuses to overwrite something newer ----
app_mod._state["results_ts"] = time.time()
old_mirror = {"snapshot": dict(FRESH, results_ts=time.time() - 86400,
                               _params=STRICT)}
r = client.post("/snapshot/restore", json=old_mirror).get_json()
assert r["restored"] is False, r
assert client.post("/snapshot/restore", json={"snapshot": {}}).get_json()["restored"] is False
# a mirror without its filters cannot be filed under any filter set
no_params = {"snapshot": dict(FRESH, results_ts=time.time() + 60)}
assert client.post("/snapshot/restore", json=no_params).get_json()["restored"] is False
print("snapshot mirror refuses stale, malformed and unattributed restores")

# ---- a simulation must carry the rules it was run under ----
importlib.reload(app_mod)
client = app_mod.app.test_client()
assert client.get("/status").get_json().get("bt_rules") is None

captured = {}


def fake_sim(params, data, universe, progress=print, reuse=True):
    captured["rules"] = {k: params.get(k) for k in db.TECH_PARAMS}
    return {"n": 5, "n_stocks": 10, "profit_factor": 1.2, "from_db": False}


app_mod.backtest_mod.run_backtest = fake_sim
app_mod.screener._cache["universe"] = ["ZZA"]
app_mod._run_backtest_thread(screener.clean_params({"min_rr": 2.75}))

rules = client.get("/status").get_json()["bt_rules"]
assert rules is not None, "the shown simulation must say which rules produced it"
assert rules["min_rr"] == 2.75, rules
assert set(rules) == set(db.TECH_PARAMS), "every rule that changes results must be recorded"
assert rules == captured["rules"], "recorded rules must match what was simulated"
print("simulations carry their rule set — old numbers cannot pass as new ones")

print("\nALL APP-STARTUP TESTS PASSED")
