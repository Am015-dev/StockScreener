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

# ---- the report states how much history its volatility rests on ----
# KMV uses a year of daily returns. The published price book carries 60
# closes. A distance computed off a quarter of a year is not as firm as
# one computed off four, and the report must not present them alike.
thin = credit.report("X", 1e11, rising[:60], 4e10, 9e10)
thick = credit.report("X", 1e11, (rising * 6)[:400], 4e10, 9e10)
assert thin["vol_obs"] == 60 and thin["vol_thin"] is True, thin
assert thick["vol_obs"] == 400 and thick["vol_thin"] is False, thick
print(f"a distance built on {thin['vol_obs']} closes is flagged thin; "
      f"{thick['vol_obs']} is not")

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

# ---- fetching: the cheap endpoint is an optimisation, not the truth ----
# companyconcept is 19KB per tag against 3.7MB for the whole filer, so it
# is tried first. But the two are not equivalent: for Ford, companyconcept
# returns ZERO rows for `Liabilities` while companyfacts returns 139, most
# recent 2026-03-31. Trusting the cheap one alone made those companies
# report "cannot assess" while the data sat in the other endpoint.
CONCEPT = "companyconcept"
FACTS = "companyfacts"


def fake_sec(concept_rows: dict, facts_node: dict | None = None,
             fail: set = frozenset(), calls: list | None = None):
    def get_json(url):
        if calls is not None:
            calls.append(url)
        if FACTS in url:
            if FACTS in fail:
                raise RuntimeError("companyfacts unavailable")
            if facts_node is None:
                raise RuntimeError("no companyfacts")
            return facts_node
        tag = url.rsplit("/", 1)[-1].replace(".json", "")
        if tag in fail:
            raise RuntimeError("refused")
        return {"units": concept_rows.get(tag, {})}
    return get_json


ROW = [{"form": "10-Q", "end": "2026-06-27", "val": 275e9}]
CUR = [{"form": "10-Q", "end": "2026-06-27", "val": 149e9}]

# happy path: the cheap endpoint answers, the big one is never touched
calls = []
bs = credit.fetch_balance_sheet(320193, fake_sec(
    {"Liabilities": {"USD": ROW}, "LiabilitiesCurrent": {"USD": CUR}}, calls=calls))
assert bs["total_liabilities"] == 275e9 and bs["current_liabilities"] == 149e9
assert bs["source_endpoint"] == CONCEPT
assert not any(FACTS in c for c in calls), \
    "the 3.7MB endpoint must not be fetched when the cheap one answered"
print(f"cheap endpoint answers in {len(calls)} small calls; the 3.7MB one is not touched")

# the Ford case: concept present but EMPTY, facts has the data
calls = []
ford_facts = {"facts": {"us-gaap": {
    "Liabilities": {"units": {"USD": [{"form": "10-Q", "end": "2026-03-31",
                                       "val": 244.95e9}]}},
    "LiabilitiesCurrent": {"units": {"USD": [{"form": "10-Q", "end": "2026-03-31",
                                              "val": 106.7e9}]}}}}}
bs = credit.fetch_balance_sheet(37996, fake_sec(
    {"Liabilities": {"USD": []}, "LiabilitiesCurrent": {"USD": []}},
    facts_node=ford_facts, calls=calls))
assert bs["total_liabilities"] == 244.95e9, bs
assert bs["source_endpoint"] == FACTS
assert any(FACTS in c for c in calls), "an empty cheap response must fall through"
print("an empty companyconcept response falls through to companyfacts (the Ford case)")

# an empty unit list is a MISS, not a zero
bs = credit.fetch_balance_sheet(1, fake_sec({"Liabilities": {"USD": []}}))
assert bs["total_liabilities"] is None
assert "Liabilities" in bs["tags_failed"], bs["tags_failed"]
print("an empty unit list counts as a failed tag, never as a balance of zero")

# both endpoints down: refuse, do not invent
bs = credit.fetch_balance_sheet(1, fake_sec({}, fail={"Liabilities",
                                                      "LiabilitiesCurrent", FACTS}))
assert bs["total_liabilities"] is None and bs["current_liabilities"] is None
r = credit.report("X", 1e11, rising, bs["current_liabilities"], bs["total_liabilities"])
assert r["dd"] is None and "Cannot assess" in r["verdict"]
print("with both endpoints refusing, the report refuses too")

# a partial outage must not produce a half-built answer
bs = credit.fetch_balance_sheet(1, fake_sec(
    {"LiabilitiesCurrent": {"USD": CUR}}, fail={"Liabilities", FACTS}))
assert bs["current_liabilities"] == 149e9
assert bs["total_liabilities"] is None
assert credit.default_point(bs["current_liabilities"],
                            bs["total_liabilities"]) is None
print("half a balance sheet yields no default point — the missing half is not assumed")

print("\nALL SEC-FETCH TESTS PASSED")


# ---- the share count: market capitalisation without a market data feed ----
# Equity value is shares x last close. The share count is the only part of
# that which has to come from filings, and it is the part most likely to be
# silently wrong: Ford's cover-page tag has been frozen at its 2011 share
# register for fifteen years and the SEC endpoint still returns it without
# complaint. Every case below is taken from a live response.
TODAY = "2026-08-08"


def dei_facts(rows, gaap_rows=None):
    node = {"dei": {"EntityCommonStockSharesOutstanding": {"units": {"shares": rows}}}}
    if gaap_rows is not None:
        node["us-gaap"] = {"WeightedAverageNumberOfSharesOutstandingBasic":
                           {"units": {"shares": gaap_rows}}}
    return {"facts": node}


CURRENT = [{"form": "10-Q", "end": "2026-07-17", "val": 14594180000}]

# happy path: the cover-page count is current, and one 19KB call settles it
calls = []
s = credit.shares_outstanding(320193, fake_sec(
    {"EntityCommonStockSharesOutstanding": {"shares": CURRENT}}, calls=calls),
    today=TODAY)
assert s["shares"] == 14594180000, s
assert s["tag"] == "dei:EntityCommonStockSharesOutstanding"
assert not any(FACTS in c for c in calls), \
    "the 3.7MB endpoint must not be fetched when the cheap one answered"
print(f"share count: cover-page tag settles it in {len(calls)} small call")

# the Coca-Cola case: companyconcept returns ZERO rows, companyfacts is current
calls = []
s = credit.shares_outstanding(21344, fake_sec(
    {"EntityCommonStockSharesOutstanding": {"shares": []}},
    facts_node=dei_facts([{"form": "10-Q", "end": "2026-04-28",
                           "val": 4302482418}]), calls=calls), today=TODAY)
assert s["shares"] == 4302482418, s
assert any(FACTS in c for c in calls), "an empty cheap response must fall through"
print("share count: an empty companyconcept falls through to companyfacts (Coca-Cola)")

# the Ford case: the cover-page count is fifteen years stale. It must be
# passed over rather than used, and the substitute must be named.
s = credit.shares_outstanding(37996, fake_sec(
    {"EntityCommonStockSharesOutstanding": {
        "shares": [{"form": "10-Q", "end": "2011-04-28", "val": 3727332952}]},
     "WeightedAverageNumberOfSharesOutstandingBasic": {
        "shares": [{"form": "10-Q", "end": "2026-03-31", "val": 3991000000}]}}),
    today=TODAY)
assert s["shares"] == 3991000000, s
assert s["tag"].endswith("WeightedAverageNumberOfSharesOutstandingBasic"), s
assert s["as_of"] == "2026-03-31"
print("share count: a 2011 register is passed over for a current tag, and named (Ford)")

# both stale: refuse, and carry the date so the refusal can say why
s = credit.shares_outstanding(37996, fake_sec(
    {"EntityCommonStockSharesOutstanding": {
        "shares": [{"form": "10-Q", "end": "2011-04-28", "val": 3727332952}]}},
    facts_node=dei_facts([{"form": "10-Q", "end": "2011-04-28", "val": 3727332952}],
                         gaap_rows=[{"form": "10-K", "end": "2014-12-31",
                                     "val": 3900000000}])), today=TODAY)
assert s["shares"] is None, s
assert s["stale_as_of"] == "2011-04-28", s
print("share count: with every tag stale it refuses, and reports the date it refused on")

# the boundary is a date, not a vibe
edge = [{"form": "10-K", "end": "2025-06-04", "val": 1000}]      # 430 days
over = [{"form": "10-K", "end": "2025-06-03", "val": 1000}]      # 431 days
assert credit._days_old("2025-06-04", TODAY) == credit.SHARES_MAX_AGE_DAYS
assert credit.shares_outstanding(1, fake_sec(
    {"EntityCommonStockSharesOutstanding": {"shares": edge}}),
    today=TODAY)["shares"] == 1000
assert credit.shares_outstanding(1, fake_sec(
    {"EntityCommonStockSharesOutstanding": {"shares": over}},
    facts_node=dei_facts(over)), today=TODAY)["shares"] is None
print(f"share count: {credit.SHARES_MAX_AGE_DAYS} days old is accepted, "
      f"{credit.SHARES_MAX_AGE_DAYS + 1} is not")

# treasury shares are not outstanding shares. Coca-Cola has issued 7.04bn
# against 4.30bn outstanding, so tagging the wrong one overstates market
# capitalisation by 64% and moves the company into a safer band than it is in.
assert not any(tag == "CommonStockSharesIssued" for _, tag in credit.SHARES_TAGS), \
    "issued shares include treasury stock and must never stand in for outstanding"
print("share count: issued shares are never substituted for outstanding shares")

# nothing available anywhere: a clean refusal, not an exception
s = credit.shares_outstanding(1, fake_sec({}, fail={
    "EntityCommonStockSharesOutstanding",
    "WeightedAverageNumberOfSharesOutstandingBasic", FACTS}))
assert s["shares"] is None and s["stale_as_of"] is None, s

# and a refused share count must leave the report refusing, never guessing
r = credit.report("F", None, rising, 106.7e9, 244.95e9)
assert r["dd"] is None and "market" in " ".join(r["missing"]).lower(), r
print("share count: an unavailable count refuses the whole report, it does not guess")

print("\nALL SHARE-COUNT TESTS PASSED")
