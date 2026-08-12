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

# A volatility measured over years can be supplied instead, and must be
# the one actually used — not averaged with, or overridden by, the short
# window that is only there to price the shares.
supplied = credit.report("X", 1e11, rising[:60], 4e10, 9e10,
                         vol=0.55, vol_obs=1200)
assert supplied["equity_vol"] == 0.55, supplied["equity_vol"]
assert supplied["vol_obs"] == 1200 and supplied["vol_thin"] is False
assert supplied["vol_source"] == "published"
assert thin["vol_source"] == "price window"
# and it must move the answer: more volatility is less distance
assert supplied["dd"] < thin["dd"], (supplied["dd"], thin["dd"])
print(f"a supplied volatility is used, not the 60-day window: "
      f"{thin['dd']} at {thin['equity_vol']:.0%} becomes {supplied['dd']} at 55%")

# a supplied volatility that is absent or nonsense falls back rather than
# poisoning the solve with a zero or a negative
for bad in (None, 0.0, -0.3):
    fb = credit.report("X", 1e11, rising, 4e10, 9e10, vol=bad, vol_obs=999)
    assert fb["dd"] is not None and fb["vol_source"] == "price window", (bad, fb)
    assert fb["vol_obs"] != 999, "a rejected volatility must not keep its sample size"
print("a missing, zero or negative published volatility falls back to the window")

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


# ---- both halves of a balance sheet must describe the same balance sheet ----
# T-Mobile stopped tagging `Liabilities` in 2013 and never stopped tagging
# `LiabilitiesCurrent`. Reading the latest of each gave current liabilities
# from 2026 against a total from 2013: 10.3bn instead of 157.3bn, market
# leverage of 9% instead of 27%, and a distance of 12.23 instead of 8.21.
# HPE moved a whole band the same way, from "watch it" to "comfortable".
NOW = "2026-08-08"


def _facts(**tags):
    return {"facts": {"us-gaap": {
        t: {"units": {"USD": [{"form": "10-Q", "end": e, "val": v}]}}
        for t, (e, v) in tags.items()}}}


tmus = _facts(LiabilitiesCurrent=("2026-06-30", 23.554e9),
              Liabilities=("2013-03-31", 10.271e9),
              LiabilitiesAndStockholdersEquity=("2026-06-30", 213.553e9),
              StockholdersEquity=("2026-06-30", 56.265e9))
bs = credit.balance_sheet(tmus, today=NOW)
assert bs["source"] == "assets minus equity", bs
assert abs(bs["total_liabilities"] - 157.288e9) < 1e7, bs["total_liabilities"]
assert bs["as_of"] == "2026-06-30"
print(f"a 2013 total is not combined with a 2026 current: the identity gives "
      f"{bs['total_liabilities']/1e9:.1f}bn, not 10.3bn")

# with no identity to fall back on, it refuses rather than mixing dates.
# A total old enough to fail the age gate is dropped before it can be
# mismatched with anything; the case that survives that gate is two
# filings a quarter apart, and it must be refused too.
stale = _facts(LiabilitiesCurrent=("2026-06-30", 23.554e9),
               Liabilities=("2013-03-31", 10.271e9))
bs2 = credit.balance_sheet(stale, today=NOW)
assert bs2["source"] is None and bs2["total_liabilities"] is None, bs2
assert credit.default_point(bs2["current_liabilities"],
                            bs2["total_liabilities"]) is None

skew = _facts(LiabilitiesCurrent=("2026-06-30", 23.554e9),
              Liabilities=("2026-03-31", 150.0e9))
bs3 = credit.balance_sheet(skew, today=NOW)
assert bs3["source"] is None, bs3
assert bs3["mismatched"] is True and bs3["total_as_of"] == "2026-03-31", bs3
assert bs3["total_liabilities"] is None, \
    "a total from another quarter must not sit in the field the model reads"
assert bs3["total_unusable"] == 150.0e9, "it is still reported, just not used"
assert credit.default_point(bs3["current_liabilities"],
                            bs3["total_liabilities"]) is None
print("two filings one quarter apart are reported separately, never combined")

# a total below current is a contradiction, not negative long-term debt
assert credit.default_point(23.554e9, 10.271e9) is None, \
    "clamping the difference to zero turns a mismatch into a confident number"
print("total below current refuses instead of clamping to zero long-term debt")

# ---- one currency, and it is the one the market cap is quoted in ----
# Enbridge files in CAD; Toyota files in JPY and its yen rows outrank its
# dollar ones. Weighing 31.4 trillion yen of liabilities against a dollar
# market capitalisation produced "comfortable" for a company it had priced
# at ten times its real size.
yen = {"facts": {"us-gaap": {"Liabilities": {"units": {
    "JPY": [{"form": "20-F", "end": "2026-03-31", "val": 31.4e12}],
    "USD": [{"form": "20-F", "end": "2026-03-31", "val": 212e9}]}},
    "LiabilitiesCurrent": {"units": {
        "JPY": [{"form": "20-F", "end": "2026-03-31", "val": 20.1e12}],
        "USD": [{"form": "20-F", "end": "2026-03-31", "val": 136e9}]}}}}}
bsy = credit.balance_sheet(yen, today=NOW)
assert bsy["total_liabilities"] == 212e9, bsy["total_liabilities"]
assert bsy["current_liabilities"] == 136e9
print("a filer reporting in two currencies is read in dollars, not whichever "
      "unit happens to carry the later date")

cad_only = {"facts": {"us-gaap": {"Liabilities": {"units": {
    "CAD": [{"form": "40-F", "end": "2026-06-30", "val": 93.7e9}]}}}}}
assert credit.balance_sheet(cad_only, today=NOW)["total_liabilities"] is None, \
    "a non-USD filer must be refused, not converted at an implied rate of 1"
print("a filer reporting only in another currency is refused outright")

# ---- and the balance sheet itself has an age limit ----
ancient = _facts(LiabilitiesCurrent=("2019-09-30", 1.0e9),
                 Liabilities=("2019-09-30", 3.0e9))
assert credit.balance_sheet(ancient, today=NOW)["total_liabilities"] is None
fresh = _facts(LiabilitiesCurrent=("2026-06-30", 1.0e9),
               Liabilities=("2026-06-30", 3.0e9))
assert credit.balance_sheet(fresh, today=NOW)["total_liabilities"] == 3.0e9
print(f"a filing older than {credit.FILING_MAX_AGE_DAYS} days is refused, "
      f"the same as a stale share count")

print("\nBALANCE-SHEET INTEGRITY PINNED")


# ---- the distance over time, which is what makes it a report ----
# A company at 4.0 and falling is a different story from one at 4.0 and
# rising, and a single figure cannot tell them apart. The share count and
# the balance sheet are fixed between filings, so the distance moves with
# the market value of equity — computable from prices already published.
flat = [100.0] * 60
falling = [round(100 * (1 - 0.5 * i / 60), 2) for i in range(60)]
rising = [round(100 * (1 + 0.5 * i / 60), 2) for i in range(60)]

h_flat = credit.history(flat, shares=1e9, default_point=4e10, vol=0.30)
h_down = credit.history(falling, shares=1e9, default_point=4e10, vol=0.30)
h_up = credit.history(rising, shares=1e9, default_point=4e10, vol=0.30)
assert len(h_flat) == 60 and len(h_down) == 60
assert max(h_flat) - min(h_flat) < 1e-6, "a flat price cannot move the distance"
assert h_down[-1] < h_down[0] - 0.3, (h_down[0], h_down[-1])
assert h_up[-1] > h_up[0] + 0.3, (h_up[0], h_up[-1])
print(f"distance follows the price: halving takes {h_down[0]:.2f} to "
      f"{h_down[-1]:.2f}, a 50% rise takes {h_up[0]:.2f} to {h_up[-1]:.2f}")

# the last point of the history must agree with the headline figure
dp = credit.default_point(4e10 * 0.4, 4e10 * 1.0)
full = credit.report("X", 1e9 * flat[-1], flat, 4e10 * 0.4, 4e10 * 1.0, vol=0.30)
tail = credit.history(flat, 1e9, full["default_point"], 0.30)[-1]
assert abs(tail - full["dd"]) < 0.01, (tail, full["dd"])
print(f"the last point of the line is the headline number: {tail:.2f} vs "
      f"{full['dd']:.2f}")

# and it refuses rather than drawing a line from nothing
for bad in (dict(closes=[], shares=1e9, default_point=4e10, vol=0.3),
            dict(closes=flat, shares=None, default_point=4e10, vol=0.3),
            dict(closes=flat, shares=1e9, default_point=None, vol=0.3),
            dict(closes=flat, shares=1e9, default_point=4e10, vol=0.0)):
    assert credit.history(bad["closes"], bad["shares"], bad["default_point"],
                          bad["vol"]) is None, bad
print("a history that cannot be computed is None, never a flat line at zero")

print("\nDISTANCE HISTORY PINNED")


# ---- restating a published standing against today's close ----
# The book stores a filing and a price. Only the price moves daily, and
# re-solving for it costs nothing — which is what frees the scheduled
# run from re-measuring the same 97 companies every time to refresh a
# number it could have computed locally.
CLOSES = [100.0 * (1.0 + 0.012 * ((i % 7) - 3)) for i in range(120)]
REP = credit.report("TST", 60e9, CLOSES, 10e9, 30e9, as_of="2026-06-30")
REP["shares"] = 60e9 / CLOSES[-1]          # the count the 60bn came from
assert REP["dd"] is not None

same = credit.restate(REP, CLOSES[-1])
assert abs(same["dd"] - REP["dd"]) < 0.02, (same["dd"], REP["dd"])
assert same["restated"] is True and same["stored_dd"] == REP["dd"]
print("restating at the same price reproduces the published distance")

up, down = credit.restate(REP, 150.0), credit.restate(REP, 60.0)
assert up["dd"] > same["dd"] > down["dd"], (up["dd"], same["dd"], down["dd"])
assert up["market_leverage"] < same["market_leverage"] < down["market_leverage"]
assert up["band"] == credit.band(up["dd"])
assert up["verdict"].startswith(str(up["dd"]))
print("a higher price moves it away from trouble and a lower one towards it, "
      "and the words follow the number")

# the balance sheet is NOT touched — that is the whole claim the report
# makes about this window, and a restatement that silently moved the
# debts would make the sparkline a lie
for k in ("default_point", "total_liabilities", "current_liabilities",
          "as_of", "shares", "equity_vol"):
    assert up.get(k) == REP.get(k), k
print("the filing is carried through untouched — only the price moves")

# it must refuse rather than invent: no price, no shares, no measurement
assert credit.restate(REP, None) is None
assert credit.restate(REP, 0) is None
assert credit.restate(REP, -5) is None
assert credit.restate(REP, "n/a") is None
assert credit.restate(dict(REP, shares=None), 100.0) is None
assert credit.restate(dict(REP, dd=None), 100.0) is None
assert credit.restate(dict(REP, equity_vol=0), 100.0) is None
assert credit.restate(None, 100.0) is None
assert credit.restate({}, 100.0) is None
print("anything it cannot restate returns nothing, so the caller keeps the "
      "published figure instead of showing an invented one")

# and it agrees with the series the sparkline is drawn from, because they
# are the same computation and must not drift apart
h = credit.history([137.0], REP["shares"], REP["default_point"],
                   REP["equity_vol"])
assert abs(h[0] - credit.restate(REP, 137.0)["dd"]) < 0.01
print("the headline and the chart are the same calculation")

print("\nRESTATEMENT PINNED")


# ---- a volatility that is not a measurement must be refused ----
# The published book carried 21 names above 200% annualised and one at
# 982%, against a median of 36%. Every one was a thinly traded secondary
# listing — OTC ADRs, London IOB lines, grey-market tickers — whose
# "close" is a quote that did not trade: it sits still for days and then
# catches up in one jump, and the standard deviation of THAT is a fact
# about a data feed.
#
# It reached the reader as a verdict. QH was published as "in distress on
# this measure" — the strongest words this model has — on 0.9% leverage,
# because a 471% volatility swamped a balance sheet with almost no debt
# in it. A reader who checked that against the company would have stopped
# believing the whole page, and would have been right to.
assert credit.usable_volatility(0.25) is None
assert credit.usable_volatility(1.2) is None
assert "not a measurement" in (credit.usable_volatility(4.716) or "")
assert credit.usable_volatility(None) is not None
assert credit.usable_volatility(0) is not None
print("an implausible volatility is refused, and says why")

# the stale-quote tell, measured rather than asserted: names above 150%
# annualised had a median 49% of days with a return of EXACTLY zero;
# names between 15% and 60% had a median of 0%
_traded = [100.0 * (1 + 0.013 * ((i * 7 % 11) - 5)) for i in range(120)]
_stale = []
for i in range(120):                       # moves one day in four
    _stale.append(_stale[-1] if _stale and i % 4 else 100.0 + i * 3.0)
assert credit.flat_day_share(_traded) < 0.05
assert credit.flat_day_share(_stale) > 0.5
assert credit.usable_volatility(0.4, _traded) is None
assert "stale quote" in (credit.usable_volatility(0.4, _stale) or "")
print(f"a quote unchanged on {credit.flat_day_share(_stale)*100:.0f}% of days is "
      f"refused; a traded one at {credit.flat_day_share(_traded)*100:.0f}% is not")

# and the refusal has to reach report(), including when the volatility was
# handed in from the published book — which is exactly the path QH took
_bad = credit.report("QH", 7.1e9, _traded, 0.03e9, 0.1e9,
                     as_of="2026-06-30", vol=4.716, vol_obs=249)
assert _bad["dd"] is None, _bad["dd"]
assert "a usable volatility" in _bad["missing"]
assert _bad["vol_refused"] and "not a measurement" in _bad["vol_refused"]
print("a published volatility gets the same examination as a measured one")

# A stale series is caught where its volatility is COMPUTED, not where a
# number is handed in. `closes` in report() is the short window used to
# price the shares — a different series from the one the published
# volatility came from — and judging one by the other would refuse a real
# company for the shape of a window that had nothing to do with it.
assert credit.equity_volatility(_stale) is None, "a stale series yields no volatility"
assert credit.equity_volatility(_traded) is not None
# so a flat pricing window with a genuine published volatility still works
_flat_window = [100.0] * 60
assert credit.report("X", 100e9, _flat_window, 10e9, 30e9, vol=0.30)["dd"] is not None
print("the stale-quote gate sits on the series it judges, and nowhere else")

# the ordinary case must be untouched — a guard that refuses everything is
# not a guard
_ok = credit.report("TST", 60e9, CLOSES, 10e9, 30e9, as_of="2026-06-30")
assert _ok["dd"] is not None and _ok["band"]
assert credit.equity_volatility(CLOSES) is not None
print("a normally traded company is still measured")

print("\nUNUSABLE VOLATILITY PINNED")


# ---- the label must say WHICH input made the distance short ----
# From the published book, the two lines that made this necessary:
#   F     debts 78.1% of its value, shares swing 37%/yr -> "watch it"
#   LITE  debts  5.9% of its value, shares swing 94%/yr -> "watch it"
# Identical words. A reader takes "watch it" to mean the company might
# struggle to pay what it owes; for a company owing six percent of its
# market value that is not what the number says, and a label that cannot
# tell the two apart is a label that reads as arbitrary.
_lev = credit.report("LEV", 55.8e9, CLOSES, 149.3e9, 302.9e9, vol=0.37)
_vol = credit.report("VOL", 63.3e9, CLOSES, 2.0e9, 4.0e9, vol=0.94)
assert _lev["dd"] is not None and _vol["dd"] is not None
assert _lev["driven_by"] == "debts", _lev["driven_by"]
assert _vol["driven_by"] == "swings", (_vol["driven_by"], _vol["dd"])
assert "what it owes" in _lev["verdict"]
assert "share price swinging" in _vol["verdict"]
print(f"the heavily indebted one says debts ({_lev['dd']}), the volatile one says "
      f"swings ({_vol['dd']}) — same band, different sentence")

# a company with plenty of room needs no explanation, and must not get a
# manufactured one
_safe = credit.report("SAFE", 4469e9, CLOSES, 149e9, 276e9, vol=0.25)
assert _safe["dd"] > 4 and _safe["driven_by"] is None
assert credit._because(None) == ""
print("a company far from trouble is not given a driver it does not need")

# and the attribution is computed, not guessed from a leverage threshold:
# hold the SAME balance sheet at an ordinary volatility and see if the
# room appears
_dp = credit.default_point(2.0e9, 4.0e9)
_at_ref = credit.distance_to_default(
    *credit.solve_merton(63.3e9, _dp, credit.REFERENCE_VOL), _dp)
assert _at_ref > 4.0 > _vol["dd"], (_at_ref, _vol["dd"])
print(f"held at an ordinary {credit.REFERENCE_VOL*100:.0f}% volatility the same "
      f"balance sheet gives {_at_ref:.1f} — which is what makes it the swings")

# restate() must carry the attribution too, or the front page and the
# report page disagree about the same company
_r = credit.restate(dict(_vol, shares=1e9), 63.3)
assert _r["driven_by"] and _r["driven_by"] in ("swings", "both", "debts")
assert credit._because(_r["driven_by"]) in _r["verdict"]
print("a restated standing carries the same explanation as a fresh one")

print("\nDRIVER ATTRIBUTION PINNED")


# ---- restate() and report() must agree on every figure they both emit ----
# They did not: market leverage was debts/ASSET-value in one and
# debts/EQUITY in the other, so the published book said Ford's debts were
# 78% of the business and the page rendered 313%. Two numbers for one
# fact, on two screens, is the whole trust problem in miniature.
_base = credit.report("LEVX", 55.8e9, CLOSES, 149.3e9, 302.9e9, vol=0.37)
_base["shares"] = 55.8e9 / CLOSES[-1]
_same = credit.restate(_base, CLOSES[-1])
for k in ("market_leverage", "asset_vol", "band", "driven_by"):
    a, b = _base.get(k), _same.get(k)
    if isinstance(a, float):
        assert abs(a - b) < 0.005, (k, a, b)
    else:
        assert a == b, (k, a, b)
assert abs(_same["asset_value"] - _base["asset_value"]) / _base["asset_value"] < 0.005
print(f"restated at the same price, every shared figure matches "
      f"(leverage {_base['market_leverage']*100:.1f}% both ways)")

# and leverage is against the business's value, not the share count's —
# the two differ by a factor of four on a company like this
assert _base["market_leverage"] < 1.0, _base["market_leverage"]
assert abs(_base["market_leverage"]
           - _base["default_point"] / _base["asset_value"]) < 1e-3
print("leverage is measured against the whole business, consistently")

print("\nFIGURE CONSISTENCY PINNED")


# ---- banks and insurers are refused on sector, not measured wrongly ----
# MOH, a health insurer, ranked fourth-closest-to-trouble on the live
# site because its medical claims payable were read as debt coming due.
# For a bank the deposits ARE the liabilities; for an insurer the claims
# are funded by premiums already collected. The KMV literature excludes
# financials for exactly this reason, and now so does this.
assert credit.is_financial(6324)          # MOH: hospital & medical plans
assert credit.is_financial("6022")        # state commercial banks
assert credit.is_financial(6798)          # REITs
assert not credit.is_financial(3711)      # motor vehicles
assert not credit.is_financial(None)
assert not credit.is_financial("")
assert not credit.is_financial("n/a")
assert "not modelled" in credit.NOT_MODELLED
print("financials are recognised by SIC and refused with a reason")

print("\nSECTOR GUARD PINNED")


# ---- a price line that cannot price the filings is refused ----
# BABAF — Alibaba's OTC ordinary-share line — was the front page's #1
# "closest to trouble": the filing's ADS-equivalent share count times
# the ordinary-share OTC price understated equity ~8x. No arithmetic on
# that pair is a measurement.
assert credit.secondary_line("BABAF"), "OTC foreign ordinary line"
assert credit.secondary_line("TCEHF")
assert credit.secondary_line("0RYA.IL"), "London IOB line"
assert credit.secondary_line("1KLAC.MI"), "Milan cross-listing"
assert credit.secondary_line("AAPL") is None
assert credit.secondary_line("BRK-B") is None
assert credit.secondary_line("SHEL.L") is None, "a primary LSE listing is not secondary"
print("secondary price lines are recognised and refused; primaries pass")

# and the arithmetic cross-check catches what the suffix rule cannot:
# equity from shares x price must agree with an independent cap reading
assert credit.equity_vs_cap(28.6e9, 230e9), "the BABAF pair, exactly"
assert credit.equity_vs_cap(230e9, 28.6e9), "and the inverse mismatch"
assert credit.equity_vs_cap(100e9, 95e9) is None, "ordinary disagreement passes"
assert credit.equity_vs_cap(None, 230e9) is None
assert credit.equity_vs_cap(100e9, None) is None, "no cap available = no check, not a refusal"
print("an 8x equity/cap disagreement is refused; a 5% one is not")

print("\nUNIT MISMATCH GUARD PINNED")
