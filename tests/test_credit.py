"""A Moody's EDF report, minus the part that cannot be bought for free.

The uploaded report prices Rheinmetall's default risk. Almost all of it
is reproducible: the model is Merton's, published in 1974, the balance
sheet is on SEC XBRL for nothing, and the share price is on every feed.

What Moody's sells is the final step — mapping Distance to Default onto
an empirical default frequency using a default database built over
decades. That cannot be reproduced, and the textbook stand-in is worse
than useless: the normal tail returns 0.000000% for Apple, which is not a
small number but a false one.

So these tests pin two things. That the model computes what it can, and
that it refuses to compute what it cannot.
"""
import math
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
os.environ.setdefault("MARKET_DB", os.path.join(tempfile.mkdtemp(), "m.db"))
sys.path.insert(0, str(ROOT))

import credit

# ---- the solver recovers what was put in ----
# A firm whose assets are worth 1,000 with 20% vol and a default point of
# 400 has a known equity value; feeding that equity back must recover the
# assets. Without this the whole report is unfalsifiable arithmetic.
from statistics import NormalDist

N = NormalDist().cdf
V0, sV0, D, r, T = 1000.0, 0.20, 400.0, 0.0375, 1.0
d1 = (math.log(V0 / D) + (r + sV0 ** 2 / 2) * T) / (sV0 * math.sqrt(T))
d2 = d1 - sV0 * math.sqrt(T)
E0 = V0 * N(d1) - D * math.exp(-r * T) * N(d2)
sE0 = N(d1) * sV0 * V0 / E0
got = credit.solve_merton(E0, D, sE0, r, T)
assert got is not None, "the solve must converge on its own output"
V, sV = got
assert abs(V - V0) < 1.0, (V, V0)
assert abs(sV - sV0) < 1e-4, (sV, sV0)
print(f"solver round-trips: assets {V0:.0f}/vol {sV0:.0%} -> equity {E0:.1f} -> "
      f"recovered {V:.1f}/{sV:.2%}")

dd = credit.distance_to_default(V, sV, D, r)
assert dd is not None and 4.0 < dd < 5.0, dd
print(f"distance to default on that firm: {dd:.2f} standard deviations")

# ---- leverage and volatility move it the right way ----
base = credit.distance_to_default(1000.0, 0.20, 400.0)
assert credit.distance_to_default(1000.0, 0.20, 800.0) < base, "more debt must be worse"
assert credit.distance_to_default(1000.0, 0.40, 400.0) < base, "more vol must be worse"
assert credit.distance_to_default(2000.0, 0.20, 400.0) > base, "more assets must be better"
print("direction: more debt worse, more volatility worse, more assets better")

# ---- the default point follows the published convention ----
assert credit.default_point(100.0, 300.0) == 100.0 + 0.5 * 200.0
assert credit.default_point(None, 300.0) is None
assert credit.default_point(100.0, None) is None
assert credit.default_point(0.0, 0.0) is None
print("default point = short-term + half long-term; missing inputs refuse")

# ---- volatility ----
assert credit.equity_volatility([100] * 80) is None, "a flat line has no volatility"
assert credit.equity_volatility([100, 101, 102]) is None, "3 closes is not a measurement"
# a smooth exponential has IDENTICAL log returns and therefore zero
# variance — the function is right to refuse it, so the fixture needs
# actual noise to be testing what it claims to test
import random as _rnd

_r = _rnd.Random(11)
rising = [100.0]
for _ in range(90):
    rising.append(rising[-1] * math.exp(_r.gauss(0.0004, 0.018)))
vol = credit.equity_volatility(rising)
assert vol is not None and 0.15 < vol < 0.45, vol
assert credit.equity_volatility([100 * (1.001 ** i) for i in range(80)]) is None, \
    "a series with no variance has no volatility, however much it rises"
print(f"volatility needs {credit.MIN_OBS}+ observations and refuses a flat series")

# ---- REFUSALS: the reason this is worth reading ----
r_none = credit.report("X", None, [], None, None)
assert r_none["dd"] is None
for want in ("market capitalisation", "share-price history", "balance sheet"):
    assert any(want in m for m in r_none["missing"]), r_none["missing"]
assert "Cannot assess" in r_none["verdict"]
print(f"a report with nothing to go on names all three gaps: {r_none['missing']}")

partial = credit.report("X", 1e9, rising, None, 5e8)
assert partial["dd"] is None and "balance sheet" in " ".join(partial["missing"])
print("a report missing only the balance sheet still refuses rather than assuming one")

# it must never emit a default probability
full = credit.report("X", E0 * 1e9, rising, 100e9, 300e9)
assert full["dd"] is not None, full
for banned in ("edf", "default_probability", "pd", "probability"):
    assert banned not in {k.lower() for k in full}, \
        f"the report must not carry a default probability: {banned}"
src = (ROOT / "credit.py").read_text()
assert "_N(-" not in src.replace(" ", ""), \
    "N(-DD) must not be computed — it returns 0.000000% for Apple, which is false"
print("no default probability is emitted anywhere — that is the part Moody's sells")

# ---- percentile: the useful number, and it needs no calibration ----
peers = [2.7, 2.8, 5.6, 8.7, 9.4, 10.2, 11.5, 13.6]
assert credit.percentile(2.7, peers) == 0
assert credit.percentile(13.6, peers) == 88
assert credit.percentile(5.0, [1.0]) is None, "too few peers to rank against"
print("peer percentile ranks without calibration, and refuses under five peers")

# ---- bands are coarse and honest ----
assert credit.band(12) == "very far from trouble"
assert credit.band(3.0) == "watch it"
assert credit.band(0.5) == "in distress on this measure"
assert credit.band(None) is None
print("bands are round numbers, described as such, not fitted thresholds")

# ---- reading a filed balance sheet, however it was tagged ----
direct = {"facts": {"us-gaap": {
    "LiabilitiesCurrent": {"units": {"USD": [
        {"form": "10-Q", "end": "2026-06-27", "val": 149e9}]}},
    "Liabilities": {"units": {"USD": [
        {"form": "10-Q", "end": "2026-06-27", "val": 275e9}]}}}}}
bs = credit.balance_sheet(direct)
assert bs["total_liabilities"] == 275e9 and bs["source"] == "Liabilities"

# Carnival files no `Liabilities` line at all; assets minus equity is the
# only route, and it must be taken rather than reported as unavailable
identity = {"facts": {"us-gaap": {
    "LiabilitiesCurrent": {"units": {"USD": [
        {"form": "10-Q", "end": "2026-05-31", "val": 10e9}]}},
    "LiabilitiesAndStockholdersEquity": {"units": {"USD": [
        {"form": "10-Q", "end": "2026-05-31", "val": 50e9}]}},
    "StockholdersEquity": {"units": {"USD": [
        {"form": "10-Q", "end": "2026-05-31", "val": 8e9}]}}}}}
bs2 = credit.balance_sheet(identity)
assert bs2["total_liabilities"] == 42e9, bs2
assert bs2["source"] == "assets minus equity"
print(f"both tagging routes read: direct, and assets-minus-equity for filers "
      f"with no Liabilities line")

# mismatched period ends must NOT be combined into a fake total
mismatch = {"facts": {"us-gaap": {
    "LiabilitiesAndStockholdersEquity": {"units": {"USD": [
        {"form": "10-Q", "end": "2026-05-31", "val": 50e9}]}},
    "StockholdersEquity": {"units": {"USD": [
        {"form": "10-K", "end": "2025-11-30", "val": 8e9}]}}}}}
assert credit.balance_sheet(mismatch)["total_liabilities"] is None, \
    "figures from different period ends must not be subtracted from each other"
print("two dates are never subtracted from one another to manufacture a total")

print("\nALL CREDIT-MODEL TESTS PASSED")
