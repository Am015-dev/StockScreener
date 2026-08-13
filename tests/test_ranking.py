"""The ranker must exclude for the right reason and never flatter a gap.

Two properties matter more than the rest:

  - a name that fails a filter is excluded WITH the reason, because the
    excluded list is where most of the information is
  - a missing measurement is never scored as a good one
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import plan                                                      # noqa: E402
import ranking                                                   # noqa: E402


def cand(t, **kw):
    base = {"ticker": t, "name": f"{t} Inc", "price": 100.0,
            "market_value": 50e9, "annual_vol": 0.30, "dd": 6.0,
            "days_to_earnings": 60, "sector": "Technology"}
    base.update(kw)
    return base


# ---- 1. every hard filter excludes, and says why in words ----
cases = [
    ("price", cand("LOW", price=2.0), "under the 5 floor"),
    ("size", cand("SMALL", market_value=4e8), "under the 2B floor"),
    ("size unknown", cand("NOSIZE", market_value=None), "could not be established"),
    ("volatility", cand("WILD", annual_vol=1.4), "sane stop"),
    ("earnings", cand("SOON", days_to_earnings=4), "gaps straight through"),
    ("credit", cand("DEBT", dd=0.8), "balance sheet is the risk"),
]
for label, c, fragment in cases:
    ok, why, _ = ranking.filters(c)
    assert not ok, f"{label}: should have been excluded, was not"
    assert fragment in why, f"{label}: reason was {why!r}"
    print(f"{label} excludes, and says so: {why[:64]}...")

ok, why, flags = ranking.filters(cand("GOOD"))
assert ok and not why, (ok, why)
print("a clean name passes every filter")

# ---- 2. a missing measurement is flagged, never silently passed ----
ok, why, flags = ranking.filters(cand("NOCRED", dd=None))
assert ok and "credit not measured" in flags, (ok, flags)
print("an unmeasured credit standing passes but carries the flag")

ok, why, flags = ranking.filters(cand("BANK", dd=None, is_financial=True))
assert ok and "credit not modelled for financials" in flags, flags
print("a bank is not marked risky for a model that does not fit it")

ok, why, flags = ranking.filters(cand("NODATE", days_to_earnings=None))
assert ok and "earnings date unverified" in flags, flags
print("an unknown report date is flagged rather than assumed clear")

# ...but absence from a COMPLETE calendar is the all-clear, not a gap.
# The calendar is built by walking every trading day in the next 45, so a
# name that never appears has nothing scheduled in them. Flagging that as
# "unverified" put a warning badge on all five live picks and told the
# reader to go and check the one thing the tool had already checked.
ok, why, flags = ranking.filters(cand("NODATE", days_to_earnings=None),
                                 cal_complete=True)
assert ok and "earnings date unverified" not in flags, flags
print("absence from a complete calendar is the all-clear, not a warning")

p = plan.trade_plan(dict(cand("NODATE", days_to_earnings=None),
                         cal_complete=True, annual_vol=0.3), risk_budget=100)
assert "Nothing scheduled" in p["earnings_text"], p["earnings_text"]
p2 = plan.trade_plan(dict(cand("NODATE", days_to_earnings=None),
                          annual_vol=0.3), risk_budget=100)
assert "check before entering" in p2["earnings_text"], p2["earnings_text"]
print("  and the plan says which of the two it is, in words")

# ...and it scores in the MIDDLE, not at the top. Unknown must never be
# worth more than measured-and-good, which is the failure that turns a
# data gap into a recommendation.
res = ranking.score([cand("BEST", dd=9.0), cand("MID", dd=5.0),
                     cand("WORST", dd=2.5), cand("UNKNOWN", dd=None)])
by = {r["ticker"]: r["components"]["credit headroom"] for r in res["ranked"]}
assert by["BEST"] > by["UNKNOWN"] > by["WORST"], by
print(f"an unmeasured credit scores mid-range ({by['UNKNOWN']}), between "
      f"the best ({by['BEST']}) and the worst ({by['WORST']})")

# ---- 3. with nothing confirmed, the forward-looking component is ZERO ----
# Neither optional component is wired in this call (no patterns_report worth
# anything, no corr_by_ticker at all), so both sit at zero and the total
# reflects it: 60, not 100.
res = ranking.score([cand("A"), cand("B", annual_vol=0.5)],
                    patterns_report={"tradeable": []})
assert res["pattern_component_active"] is False
assert all(r["components"]["confirmed pattern"] == 0 for r in res["ranked"])
assert res["portfolio_component_active"] is False
assert all(r["components"]["adds to what you own"] == 0 for r in res["ranked"])
assert res["max_points_available"] == 60.0, res["max_points_available"]
print("with no shape confirmed, every name scores 0 on the pattern component")
print("with no holdings supplied, every name scores 0 on the portfolio component")
print("  and the page is told only 60 points were available, not 100")

# The old behaviour: corr_by_ticker=None made fit_v fall back to a constant
# 1.0 for EVERY ticker — never zero, never different between names, so it
# inflated every score by the same 20 points while a bar on the page
# implied it was measuring something. That is worse than showing zero: a
# constant dressed as a differentiator. Pinned so it cannot come back.
same_score_gap = res["ranked"][0]["score"] - res["ranked"][1]["score"]
assert res["ranked"][0]["components"]["adds to what you own"] == \
    res["ranked"][1]["components"]["adds to what you own"] == 0, \
    "an inactive portfolio component must be zero, not a shared constant"
print("  and it is a real zero, not the old silent constant of 20")

# An EMPTY dict is not the same as no dict: it means holdings were supplied
# and none of them correlate, which is a real measurement and should score.
res_empty = ranking.score([cand("A"), cand("B")], corr_by_ticker={})
assert res_empty["portfolio_component_active"] is True
assert res_empty["max_points_available"] == 80.0, res_empty["max_points_available"]
print("an empty (but present) holdings dict activates the component, unlike None")

# a shape that survived the search but NOT the holdout must not count
res2 = ranking.score([cand("A")], patterns_report={"tradeable": [
    {"pattern": "a new 20-day high", "confirmed": False,
     "after_costs_pct": 0.9}]})
assert res2["pattern_component_active"] is False, \
    "an unconfirmed shape was allowed to move a name up the list"
print("a shape that did not hold up out of sample does not count either")

# and one that DID hold up does
res3 = ranking.score(
    [cand("HIT", patterns_today=["a new 20-day high"]), cand("MISS")],
    patterns_report={"tradeable": [
        {"pattern": "a new 20-day high", "confirmed": True,
         "after_costs_pct": 0.9, "holdout": {"after_costs_pct": 0.6}}]})
assert res3["pattern_component_active"] is True
hit = next(r for r in res3["ranked"] if r["ticker"] == "HIT")
miss = next(r for r in res3["ranked"] if r["ticker"] == "MISS")
assert hit["components"]["confirmed pattern"] > \
    miss["components"]["confirmed pattern"], (hit, miss)
print("a confirmed shape does move the name that is showing it")

# ---- 4. the share count round-trips against the risk budget ----
row = ranking.score([cand("SIZE", price=50.0, annual_vol=0.4)],
                    risk_budget=250.0)["ranked"][0]
p = plan.trade_plan(row, risk_budget=250.0)
assert p["usable"], p
risked = p["shares"] * (p["entry"] - p["stop"])
assert abs(risked - 250.0) <= (p["entry"] - p["stop"]) + 0.01, \
    f"{p['shares']} shares risks {risked}, not 250"
print(f"{p['shares']} shares x {p['entry'] - p['stop']:.2f} per share = "
      f"{risked:.0f} at risk against a 250 budget")

# and there is no price target anywhere in the plan — a target implies a
# forecast this tool has measured and not found
blob = " ".join(str(v) for v in p.values()).lower()
assert "target" not in blob, "the plan printed a price target"
print("the plan states a typical move and never a target")

# ---- 4b. a superlative belongs to one name only ----
# The first draft told the reader that two different names each had "the
# most room on its debts of anything that cleared today". Both cards were
# worded from the same branch, and neither knew about the other.
rows = ranking.score([cand("AAA", dd=9.0), cand("BBB", dd=8.0),
                      cand("CCC", dd=7.0)])["ranked"]
lines = [plan.thesis(r, rank=i) for i, r in enumerate(rows)]
supers = [ln for ln in lines if "the most" in ln or "the calmest" in ln
          or "moves least" in ln]
assert len(supers) <= 1, f"more than one name claimed a superlative: {supers}"
print(f"of {len(lines)} theses, {len(supers)} claims a superlative")

# ---- 4c. a move too small to be worth its costs is thrown out ----
# This was a scoring component and it was wrong there: rewarding a large
# move against costs while also rewarding low volatility rewards two
# opposite things, and on real data they cancelled — a name scoring 18/20
# for being calm and 2/20 for barely moving came out ranked first on the
# strength of neither.
ok, why, _ = ranking.filters(cand("SLEEPY", annual_vol=0.02))
assert not ok and "worth taking" in why, (ok, why)
print(f"a name too quiet to pay for its own spread is excluded: {why[:58]}...")

comps = ranking.score([cand("X")])["ranked"][0]["components"]
assert "move against costs" not in comps, \
    "the self-cancelling cost component is back in the score"
assert sum(ranking.score([cand("X")])["ranked"][0]["component_max"].values()) == 100
print("the score no longer contains two components that cancel each other")

# ---- 5. the order is stable, so a refresh does not reshuffle it ----
many = [cand(f"T{i}", annual_vol=0.3, dd=5.0) for i in range(12)]
a = [r["ticker"] for r in ranking.score(many)["ranked"]]
b = [r["ticker"] for r in ranking.score(list(reversed(many)))["ranked"]]
assert a == b, "the same inputs produced two different orders"
print("identical inputs give an identical order, whatever order they arrive in")

# ---- 6. correlation with what you already own pushes a name down ----
res = ranking.score([cand("SAME"), cand("DIFF")],
                    corr_by_ticker={"SAME": 0.95, "DIFF": 0.05})
same = next(r for r in res["ranked"] if r["ticker"] == "SAME")
diff = next(r for r in res["ranked"] if r["ticker"] == "DIFF")
assert diff["components"]["adds to what you own"] > \
    same["components"]["adds to what you own"], (same, diff)
print("a name that moves like your existing book scores below one that does not")

print("\nALL RANKING TESTS PASSED")
