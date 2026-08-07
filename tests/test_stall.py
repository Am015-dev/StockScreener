"""A scan must never spin silently.

Observed in production: status "running", an empty log, no results, for
forty minutes. The cause is structural — run_screener made network calls
before emitting its first progress line, so a hang upstream produced a
page that said "scanning" and nothing else, indefinitely. These tests pin
the two fixes: progress before any network call, and a stall that is
reported and escapable rather than endless.
"""
import importlib
import os
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TMP = tempfile.mkdtemp(prefix="stall_")
for k, f in (("MARKET_DB", "m.db"), ("JOURNAL_DB", "j.db"),
             ("SCREENER_CACHE_DB", "c.db"), ("RESULTS_CSV", "r.csv")):
    os.environ[k] = os.path.join(TMP, f)
os.environ["STALL_AFTER_S"] = "2"
sys.path.insert(0, str(ROOT))

import screener

# ---- run_screener must speak before it touches the network ----
said: list[str] = []
order: list[str] = []


def watched_auth():
    order.append("network")
    raise RuntimeError("Yahoo unreachable")


def watched_universe(p, progress=print):
    order.append("universe")
    raise RuntimeError("stop here — we only care about what happened first")


screener._yahoo_auth_session = watched_auth
screener.build_universe = watched_universe
try:
    screener.run_screener({}, progress=lambda m: (said.append(str(m)),
                                                  order.append("progress"))[0])
except RuntimeError:
    pass

assert said, "the scan produced no output at all before failing"
assert order[0] == "progress", f"network ran before any progress: {order[:3]}"
assert "Scan started" in said[0], said[0]
# a failing sign-in must not abort the scan or silence it
assert any("fundamentals sign-in unavailable" in m for m in said), said
assert "universe" in order, "a failed sign-in must not stop the scan"
print(f"progress precedes network: {order[:4]}")
print(f"   first line: {said[0]!r}")

# ---- the app reports a silent scan and offers a way out ----
import app as app_mod

importlib.reload(app_mod)
client = app_mod.app.test_client()

app_mod._state.update(status="running", started_at=time.time() - 60,
                      last_progress_ts=time.time() - 60, log=[], results=[])
s = client.get("/status").get_json()
assert s.get("stalled_s") is not None, "a silent scan must be reported as stalled"
assert s["stalled_s"] >= 59, s["stalled_s"]
print(f"silent scan reported as stalled after {s['stalled_s']}s")

# a scan that IS talking must not be flagged
app_mod._state["last_progress_ts"] = time.time()
assert client.get("/status").get_json().get("stalled_s") is None, \
    "a scan making progress must never be called stalled"

# ---- cancelling clears it and restores what we can legitimately show ----
app_mod._state.update(status="running", last_progress_ts=time.time() - 120)
r = client.post("/cancel").get_json()
assert r["ok"] is True, r
st = client.get("/status").get_json()
assert st["status"] == "error", st["status"]
assert "abandoned" in (st["error"] or "").lower(), st["error"]
assert "Press Run to try again" in st["error"]
assert st.get("stalled_s") is None, "a cancelled scan is not still stalling"
print("cancelling a stalled scan reports it honestly and stops the spinner")

# cancelling when nothing runs is refused rather than corrupting state
app_mod._state["status"] = "done"
assert client.post("/cancel").status_code == 409
print("cancel is refused when no scan is running")

print("\nALL STALL TESTS PASSED")
