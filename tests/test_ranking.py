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
            "dollar_vol": 500e6, "annual_vol": 0.30, "dd": 6.0,
            "days_to_earnings": 60, "sector": "Technology"}
    base.update(kw)
    return base


# ---- 1. every hard filter excludes, and says why in words ----
cases = [
    ("price", cand("LOW", price=2.0), "under the 5 floor"),
    ("liquidity", cand("THIN", dollar_vol=3e6), "too thin to get out"),
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
res = ranking.score([cand("A"), cand("B", annual_vol=0.5)],
                    patterns_report={"tradeable": []})
assert res["pattern_component_active"] is False
assert all(r["components"]["confirmed pattern"] == 0 for r in res["ranked"])
assert res["max_points_available"] == 80.0, res["max_points_available"]
print("with no shape confirmed, every name scores 0 on the pattern component")
print("  and the page is told only 80 points were available, not 100")

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
