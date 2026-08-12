"""The pattern finder must find a planted edge and reject a fake one.

A discovery framework that cannot do BOTH is not measuring anything.
The two tests that matter here are:

  - plant a real effect in synthetic data; the framework must find it
  - give it pure noise; the framework must find nothing, repeatedly

Everything else is detail.
"""
import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import patterns                                                   # noqa: E402


def make_series(n_tickers=60, n_days=400, seed=1, edge_after=None,
                edge_size=0.0):
    """Random walks, optionally with a real effect planted.

    `edge_after` is a predicate on the window; when it fires, the next
    `horizon` days get an extra drift. That is a genuine, known edge —
    the only kind whose recovery proves the machinery works.
    """
    rng = random.Random(seed)
    out = {}
    for t in range(n_tickers):
        px = [100.0]
        boost = 0
        for _ in range(n_days):
            step = rng.gauss(0.0004, 0.018)
            if boost > 0:
                step += edge_size
                boost -= 1
            px.append(px[-1] * (1 + step))
            if edge_after and len(px) > 51 and edge_after(px):
                boost = 5
        out[f"T{t}"] = [round(x, 4) for x in px]
    return out


# ---- 1. pure noise: the framework must find nothing ----
noise = make_series(seed=7)
res = patterns.sweep(noise, seeds=60)
survivors = [k for k, v in res.items() if v.get("survives")]
assert not survivors, f"found an edge in a random walk: {survivors}"
print(f"on {len(noise)} random walks, {len(res)} patterns tested, "
      f"{len(survivors)} survived — as it must be")

# and the project's own falsified rule must be among the rejected
pull = res.get("pullback in an uptrend (this project's falsified rule)")
if pull:
    assert not pull.get("survives"), pull
    print("the project's own falsified rule is rejected here too")

# ---- 2. a planted edge: the framework must find it ----
# a real +0.35%/day drift for five days after three consecutive down
# closes — big enough to be unmistakable, so a failure here is the
# framework's fault and not the sample's
planted = make_series(seed=11, edge_after=patterns.p_three_down,
                      edge_size=0.0035)
res2 = patterns.sweep(planted, seeds=60)
row = res2.get("three lower closes in a row")
assert row, "the planted pattern was not even measured"
assert row["survives"], f"planted edge not found: {row}"
assert row["edge_pct"] > 0.5, row
print(f"a planted edge is recovered: {row['edge_pct']:+.2f}% over "
      f"{row['horizon']} sessions, p = {row['p']:.2g} ({row['p_source']}), "
      f"n = {row['n']}")

# the resolution limit itself, pinned: a permutation test with k draws
# cannot report below 1/(k+1), and a detection must not be lost to that
assert row["p"] < row["p_perm"], (row["p"], row["p_perm"])
assert row["p_source"].startswith("normal"), row["p_source"]
assert row["p_perm"] == round(1 / (row["permutations"] + 1), 4), row
print(f"  and the permutation floor ({row['p_perm']}) did not swallow it")

# ...but the refinement must not run away with itself. A normal tail
# fitted to the null draws and extrapolated far enough will hand back
# 0.0, and "the probability of this happening by chance is zero" is the
# one statement no sample of any size can support.
assert row["p"] >= patterns.P_FLOOR, row
assert round(row["p"], 6) > 0, row
print(f"  and the reported p stops at {patterns.P_FLOOR:g} rather than "
      f"claiming zero")

# ---- 3. the date-matched null is what makes it honest ----
# On a day every stock rose, a pattern that fired that day would show a
# wonderful return and mean nothing. The null draws from the SAME days,
# so a pattern that only picks market-wide up days has no edge over it.
rally = {}
rng = random.Random(3)
for t in range(40):
    px = [100.0]
    for d in range(300):
        # every stock moves together: pure market, no stock-specific signal
        px.append(px[-1] * (1 + (0.02 if d % 20 == 0 else -0.001)))
    rally[f"M{t}"] = px
r_rally = patterns.sweep(rally, seeds=30)
alive = [k for k, v in r_rally.items() if v.get("survives")]
assert not alive, f"a market-wide move was mistaken for a pattern: {alive}"
print("a pattern that only rides market-wide days shows no edge over its "
      "date-matched null")

# ---- 3b. the clustering bug itself, pinned ----
# This is the one that got through. Run over a 60-session book, the
# framework called this project's own falsified pullback rule
# significant at p = 0.004 — on 289 "observations" sitting on FOUR
# calendar days. Everything firing on one day shares that day's market
# move, so those 289 carried about as much evidence as four. A big `n`
# on a handful of days is not a sample, and must be refused as one.
# a shape that exists ONLY on three calendar days, in every ticker at
# once — a sector-wide gap, an index rebalance, a Fed morning
spiky = {t: list(c) for t, c in make_series(seed=5).items()}
SPIKE_DAYS = (120, 180, 240)
for c in spiky.values():
    for d in SPIKE_DAYS:
        c[d] = round(c[d - 1] * 1.25, 4)
jumped = lambda w: w[-1] / w[-2] > 1.2                           # noqa: E731

clustered = patterns.test_pattern(spiky, jumped, min_hits=30)
assert clustered is None, \
    f"hundreds of stock-days on three calendar days were treated as a " \
    f"sample: {clustered}"
# and the refusal is about days, not about n: force the day floor down
# and the same shape is measurable, which proves the day count is what
# rejected it rather than some unrelated guard
forced = patterns.test_pattern(spiky, jumped, min_hits=30, min_days=3)
assert forced and forced["days"] == 3 and forced["n"] >= 100, forced
print(f"{forced['n']} observations on {forced['days']} days is refused as a "
      f"sample — the day is the unit, not the stock-day")

# ---- 4. rarity is refused, not reported with a p-value ----
assert patterns.test_pattern(noise, lambda w: len(w) > 9999) is None
rare = patterns.test_pattern(noise, lambda w: w[-1] > 1e9)
assert rare is None, "a pattern with no occurrences must not get a p-value"
print("a shape too rare to measure is refused rather than given a p-value")

# ---- 5. multiple testing is corrected, and the correction is visible ----
# THE property that matters: one lucky-looking result among twenty
# nothings must NOT survive. p = 0.04 is "significant" on its own and is
# exactly what you expect to see once when you test twenty coin flips.
fam = {"lucky": {"p": 0.04, "edge_pct": 0.1, "horizon": 5, "n": 100,
                 "after_costs_pct": -0.1}}
fam.update({f"dud{i}": {"p": 0.5, "edge_pct": 0.0, "horizon": 5, "n": 100,
                        "after_costs_pct": -0.2} for i in range(19)})
bh = patterns.benjamini_hochberg(fam, fdr=0.10)
assert all(v["family_size"] == 20 for v in bh.values())
assert not bh["lucky"]["survives"], \
    "a p = 0.04 among twenty tests is what luck looks like, not a discovery"
assert not any(v["survives"] for v in bh.values())
print("one p = 0.04 among twenty nothings does not survive the correction")

# and the converse, so the correction is not just a blanket refusal:
# twenty results all at p = 0.04 DO survive, and correctly — the expected
# number of false discoveries is 20 x 0.04 = 0.8, well inside the 10%
# of twenty that the FDR allows. Pinned because it looks wrong and is not.
allsig = {f"p{i}": {"p": 0.04, "edge_pct": 0.1, "horizon": 5, "n": 100,
                    "after_costs_pct": -0.1} for i in range(20)}
assert all(v["survives"] for v in patterns.benjamini_hochberg(allsig, 0.10).values())
print("twenty results all at p = 0.04 do survive — which is BH working, not failing")

# a single genuinely small p in a small family does survive
bh2 = patterns.benjamini_hochberg(
    {"a": {"p": 0.001, "edge_pct": 1.0, "horizon": 5, "n": 200,
           "after_costs_pct": 0.8},
     "b": {"p": 0.6, "edge_pct": 0.0, "horizon": 5, "n": 200,
           "after_costs_pct": -0.2}}, fdr=0.10)
assert bh2["a"]["survives"] and not bh2["b"]["survives"]
print("a strong result in a small family still survives the correction")

# ---- 6. costs are stated, so 'real but not tradeable' is visible ----
v = patterns.verdict({"survives": True, "edge_pct": 0.10, "horizon": 5,
                      "n": 100, "p": 0.01, "after_costs_pct": -0.10,
                      "family_size": 11})
assert "not tradeable" in v, v
v2 = patterns.verdict({"survives": True, "edge_pct": 1.2, "horizon": 5,
                       "n": 100, "p": 0.001, "after_costs_pct": 1.0,
                       "family_size": 11})
assert "after costs" in v2 and "+1.20%" in v2, v2
assert "no better than random" in patterns.verdict(
    {"survives": False, "p": 0.4, "family_size": 11})
assert "too rare" in patterns.verdict(None)
print("a significant result smaller than costs is reported as untradeable")

print("\nALL PATTERN TESTS PASSED")
