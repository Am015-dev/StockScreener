"""/today must refuse to publish a list it cannot check for earnings.

A stop does not protect you across a report — the price gaps straight
through it. That is the reason the earnings filter exists, and it fails
open: when the calendar cannot be read, every name comes back "date
unknown", collects a flag, and sails through. Five confident-looking
plans with the one check that matters silently switched off is worse
than no list at all, and it is exactly the failure this project keeps
finding in itself.

So the property under test is a refusal, not an output.
"""
import json
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

# a published directory holding prices and volatility but NO calendar
tmp = tempfile.mkdtemp(prefix="today-gate-")
series = {f"T{i}": [100.0 + (i % 7) + j * 0.1 for j in range(60)]
          for i in range(40)}
Path(tmp, "prices.json").write_text(json.dumps(
    {"dates": [f"2026-06-{(j % 28) + 1:02d}" for j in range(60)],
     "series": series}))
Path(tmp, "vol.json").write_text(json.dumps(
    {t: {"vol": 0.28, "obs": 250, "as_of": "2026-08-11"} for t in series}))
Path(tmp, "credit.json").write_text(json.dumps(
    {t: {"ticker": t, "dd": 7.0, "equity": 4e10, "shares": 1e8,
         "band": "comfortable"} for t in series}))

os.environ["PUBLISHED_DIR"] = tmp
os.environ["PUBLISHED_BASE"] = "http://127.0.0.1:1"
os.environ["PUBLISHED_FETCH_S"] = "1"
os.environ["SKIP_WARM"] = "1"

import app                                                       # noqa: E402

# SKIP_WARM stops the boot loader, so fill the books the same way it would
for name, store in (("prices.json", app._book), ("vol.json", app._vols),
                    ("credit.json", app._creds)):
    store.update(data=json.loads(Path(tmp, name).read_text()), ts=9e9)

c = app.app.test_client()
r = c.get("/today")
assert r.status_code == 200, r.status_code
body = r.data.decode()

assert "No list today" in body, \
    "a list was published with the earnings check switched off"
assert "earnings calendar could not be read" in body
print("with no earnings calendar, /today refuses to publish a list")

# and it refuses out loud, naming the consequence rather than shrugging
assert "gaps straight through" in body, \
    "the refusal does not say why it matters"
print("  and says why: a stop does not survive a report")

# no plan may be rendered at all — not even a partial one
assert 'class="card pick"' not in body, "a trade plan was rendered anyway"
assert "Buy at market" not in body
print("  and renders no trade plan, not even a partial one")

# the root is the same page: the gate cannot be walked around by URL
assert c.get("/").data == r.data, "/ and /today disagree"
print("the root serves the same refusal — no way around it by URL")

# ---- the front page must never wait on the network ----
# It did. _published_get is a network fetch bounded by PUBLISHED_FETCH_S
# (75 seconds by default) and the route called it for patterns.json on
# every cache miss, so a cold instance served "/" only after that fetch
# finished or gave up. The site went unreachable and the release gate
# found it the honest way: twenty-three assertions passed and the
# twenty-fourth timed out navigating to the root.
#
# PUBLISHED_BASE points at an unroutable address in this test, so any
# route that still reaches out will take the full ceiling.
import time                                                     # noqa: E402

app.PUBLISHED_BASE = "http://10.255.255.1"
app.PUBLISHED_FETCH_S = 75.0
app._today_memo.update(key=None, res=None)     # force the slow path
for path in ("/", "/today", "/patterns"):
    t0 = time.time()
    resp = c.get(path)
    took = time.time() - t0
    assert resp.status_code == 200, (path, resp.status_code)
    assert took < 5.0, f"{path} waited {took:.0f}s on the network"
    print(f"{path} answers in {took:.2f}s with the network unreachable")

print("\nALL /today GATE TESTS PASSED")
