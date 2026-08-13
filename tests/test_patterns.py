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

# ---- 3c. volatile stocks are compared against volatile stocks ----
# The confound that made the first real sweep produce eleven "tradeable"
# patterns, every one of them a volatility shape. Here half the names are
# violent AND drift up, half are calm and flat. No shape carries any
# information — but any shape that selects the violent half will look
# wonderful against a pool that is mostly the calm half.
mixed = {}
rng = random.Random(21)
for t in range(60):
    violent = t < 30
    px = [100.0]
    for _ in range(320):
        # the violent half moves 4x as hard AND drifts up; that drift is
        # a property of the STOCK, not of anything a pattern spotted
        px.append(px[-1] * (1 + rng.gauss(0.0040 if violent else 0.0002,
                                          0.032 if violent else 0.008)))
    mixed[f"{'V' if violent else 'C'}{t}"] = px

# "a single session up 3% or more" fires almost only in the violent half
naive = patterns.test_pattern(mixed, patterns.p_gap_up_3pct, seeds=200,
                              match_volatility=False)
matched = patterns.test_pattern(mixed, patterns.p_gap_up_3pct, seeds=200)
assert naive and matched, (naive, matched)
assert naive["p"] < 0.05, \
    f"the confound did not reproduce, so this test proves nothing: {naive}"
assert matched["p"] > 0.05, \
    f"a pure volatility-selection effect survived the matched null: {matched}"
assert matched["edge_pct"] < naive["edge_pct"], (naive, matched)
print(f"a shape that only selects volatile stocks: {naive['edge_pct']:+.2f}% "
      f"at p = {patterns.p_words(naive['p'])} against any stock, but "
      f"{matched['edge_pct']:+.2f}% at p = {patterns.p_words(matched['p'])} "
      f"against equally volatile ones")

# and the matching must not blunt a genuine edge: the planted one from
# section 2 still has to come through when the null is volatility-matched
assert row["survives"] and row["p"] < 0.01, row
print("  while the planted edge still survives the same matching")

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

# and the family has to span every holding period, not just one. Trying
# three horizons is three times the tests; correcting each separately
# would hand out three tickets in the same lottery at the price of one.
many = patterns.sweep_many(noise, [3, 5], seeds=30)
sizes = {v["family_size"] for rows in many.values() for v in rows.values()
         if v and v.get("family_size")}
assert len(sizes) == 1, f"rows corrected against different families: {sizes}"
one = patterns.sweep(noise, horizon=5, seeds=30)
one_size = {v["family_size"] for v in one.values() if v.get("family_size")}
assert sizes.pop() > one_size.pop(), \
    "two holding periods were corrected as if only one had been tried"
assert not [k for rows in many.values() for k, v in rows.items()
            if v and v.get("survives")], "found an edge in a random walk"
print("two holding periods are corrected as one family, not two")

# a single genuinely small p in a small family does survive
bh2 = patterns.benjamini_hochberg(
    {"a": {"p": 0.001, "edge_pct": 1.0, "horizon": 5, "n": 200,
           "after_costs_pct": 0.8},
     "b": {"p": 0.6, "edge_pct": 0.0, "horizon": 5, "n": 200,
           "after_costs_pct": -0.2}}, fdr=0.10)
assert bh2["a"]["survives"] and not bh2["b"]["survives"]
print("a strong result in a small family still survives the correction")

# ---- 5b. an edge that only existed in the first half is caught ----
# The failure that has ended more strategies than bad statistics ever
# did: the shape really was there, and it was still nothing but the
# window. Plant a genuine edge in the FIRST half only, then let the
# search find it and the held-back half refuse to confirm it.
def half_planted(seed=31, n_days=520, n_tickers=60):
    rng = random.Random(seed)
    out = {}
    for t in range(n_tickers):
        px, boost = [100.0], 0
        for d in range(n_days):
            step = rng.gauss(0.0004, 0.018)
            # the edge is real, and it stops existing halfway through
            if boost > 0 and d < n_days // 2:
                step += 0.0035
            if boost > 0:
                boost -= 1
            px.append(px[-1] * (1 + step))
            if len(px) > 51 and patterns.p_three_down(px):
                boost = 5
        out[f"H{t}"] = [round(x, 4) for x in px]
    return out


hp = half_planted()
found = patterns.sweep_with_holdout(hp, [5], seeds=60)
row5 = found[5]["three lower closes in a row"]
assert row5 and row5.get("survives"), \
    f"the search did not even find the planted first-half edge: {row5}"
assert row5.get("confirmed") is False, \
    f"an edge that stopped existing halfway through was confirmed: {row5}"
assert "did NOT hold up" in row5["holdout_note"], row5
print(f"an edge present only in the first half is found ("
      f"{row5['edge_pct']:+.2f}%) and then refused by the held-back half "
      f"({row5['holdout']['edge_pct']:+.2f}%)")
assert "not a finding" in patterns.verdict(row5), patterns.verdict(row5)
print("  and its verdict says so instead of quoting the flattering number")

# ---- 6. costs are stated, so 'real but not tradeable' is visible ----
v = patterns.verdict({"survives": True, "edge_pct": 0.10, "horizon": 5,
                      "n": 100, "p": 0.01, "after_costs_pct": -0.10,
                      "family_size": 11})
assert "not tradeable" in v, v
v2 = patterns.verdict({"survives": True, "edge_pct": 1.2, "horizon": 5,
                       "n": 100, "p": 0.001, "after_costs_pct": 1.0,
                       "family_size": 11})
assert "after costs" in v2 and "+1.20%" in v2, v2
assert "no better than buying a random stock that was moving just as much" \
    in patterns.verdict({"survives": False, "p": 0.4, "family_size": 11})
assert "too rare" in patterns.verdict(None)
print("a significant result smaller than costs is reported as untradeable")

print("\nALL PATTERN TESTS PASSED")


# ---- 7. combinatorial holdout: one split's luck is not the whole story ----
# sweep_with_holdout() answers "did it hold up on THE held-back half" —
# one boolean, riding on wherever that one cut fell. combinatorial_holdout()
# adds "how many DIFFERENT held-out stretches does it hold up on", without
# touching the original answer.

# 7a. it must not change a single field sweep_with_holdout already reports
hp = half_planted()
plain = patterns.sweep_with_holdout(hp, [5], seeds=60)
combo = patterns.combinatorial_holdout(hp, [5], n_blocks=6, k_test=1, seeds=60)
plain_row = plain[5]["three lower closes in a row"]
combo_row = combo[5]["three lower closes in a row"]
for key in ("edge_pct", "p", "n", "days", "confirmed", "holdout", "holdout_note",
           "survives"):
    assert combo_row[key] == plain_row[key], \
        (key, combo_row[key], plain_row[key])
print("combinatorial_holdout leaves every field sweep_with_holdout already "
      "reports exactly as it was")

# 7b. a pattern present EVERYWHERE reconfirms in every held-out block —
# and a pattern present in only HALF the blocks is caught as unstable,
# even though a single lucky 50/50 cut called it "confirmed"
def alternating_planted(seed=31, n_days=1800, n_tickers=60, n_blocks=6,
                        edge_size=0.006):
    """The edge is real, but it only operates in the EVEN blocks of the
    calendar — a shape that is genuinely unstable across time, the exact
    thing one fixed 50/50 split cannot see because both halves mix on
    and off stretches together."""
    rng = random.Random(seed)
    block_len = n_days // n_blocks
    out = {}
    for t in range(n_tickers):
        px, boost = [100.0], 0
        for d in range(n_days):
            blk = min(n_blocks - 1, d // block_len)
            on = (blk % 2 == 0)
            step = rng.gauss(0.0004, 0.018)
            if boost > 0 and on:
                step += edge_size
            if boost > 0:
                boost -= 1
            px.append(px[-1] * (1 + step))
            if len(px) > 51 and patterns.p_three_down(px):
                boost = 5
        out[f"A{t}"] = [round(x, 4) for x in px]
    return out


def stable_planted(seed=11, n_days=1800, n_tickers=60, edge_size=0.006):
    """The same edge, but operating everywhere — the stable control."""
    rng = random.Random(seed)
    out = {}
    for t in range(n_tickers):
        px, boost = [100.0], 0
        for d in range(n_days):
            step = rng.gauss(0.0004, 0.018)
            if boost > 0:
                step += edge_size
                boost -= 1
            px.append(px[-1] * (1 + step))
            if len(px) > 51 and patterns.p_three_down(px):
                boost = 5
        out[f"S{t}"] = [round(x, 4) for x in px]
    return out


stable = patterns.combinatorial_holdout(stable_planted(), [5], n_blocks=6,
                                        k_test=1, seeds=60)
stable_row = stable[5]["three lower closes in a row"]
assert stable_row["reconfirm_rate"] == 1.0, stable_row
print(f"an edge present in every block reconfirms in all "
      f"{stable_row['combinations']} held-out combinations")

unstable = patterns.combinatorial_holdout(alternating_planted(), [5],
                                          n_blocks=6, k_test=1, seeds=60)
uns_row = unstable[5]["three lower closes in a row"]
assert uns_row["confirmed"] is True, \
    "the fixture must still be confirmed by the plain 50/50 split, or this " \
    "is not testing what it claims to"
assert uns_row["reconfirm_rate"] < stable_row["reconfirm_rate"], \
    (uns_row["reconfirm_rate"], stable_row["reconfirm_rate"])
assert uns_row["reconfirm_rate"] <= 0.75, uns_row
print(f"an edge present in only half the calendar's blocks is called "
      f"'confirmed' by the single 50/50 split — but reconfirms in only "
      f"{uns_row['reconfirmed_in']}/{uns_row['combinations']} "
      f"({uns_row['reconfirm_rate']:.0%}) held-out combinations, well "
      f"below the stable pattern's {stable_row['reconfirm_rate']:.0%} — "
      f"exactly the instability one fixed cut cannot see")

# 7c. no combination may ever concatenate non-adjacent stretches of the
# calendar into a fake contiguous series. Build a fixture where a real
# 3-lower-closes streak sits at the very END of every block; if two
# non-adjacent blocks were ever glued together, the count of hits in
# their union would NOT equal the sum of each block's own hit count.
N_BLOCKS, BLOCK_LEN = 6, 90
glued_closes = []
for b in range(N_BLOCKS):
    base = 100.0 + b * 5
    seg = [base] * (BLOCK_LEN - 4)
    seg += [base, base * 0.97, base * 0.94, base * 0.91]
    glued_closes.extend(seg)
glue_series = {"GLUE": glued_closes}
bounds = patterns._block_bounds(len(glued_closes), N_BLOCKS)


def _count(allowed):
    return len(patterns.occurrences(glue_series, patterns.p_three_down,
                                    horizon=1, min_history=1, allowed_days=allowed))


c0 = _count(patterns._day_set([bounds[0]]))
c2 = _count(patterns._day_set([bounds[2]]))
c_union = _count(patterns._day_set([bounds[0], bounds[2]]))
assert c0 > 0 and c2 > 0, "fixture is not exercising the pattern at all"
assert c_union == c0 + c2, \
    (f"a hit was manufactured or lost at the seam between non-adjacent "
     f"blocks: block0={c0}, block2={c2}, union={c_union}")
print(f"holding out non-adjacent blocks (0 and 2) finds exactly "
      f"{c0} + {c2} = {c_union} hits — never one manufactured at the seam "
      f"a naive concatenation would have created")

print("\nCOMBINATORIAL HOLDOUT PINNED")


# ---- 8. Benjamini-Yekutieli: a stricter cross-check under any dependence ----
by_noise = patterns.sweep(noise, seeds=60)
by_check = patterns.benjamini_yekutieli(
    {k: v for k, v in by_noise.items() if v}, fdr=0.10)
assert all(not v["survives_by"] for v in by_check.values()), \
    "pure noise survived the stricter dependence-robust correction"
print("pure noise survives Benjamini-Yekutieli exactly as it survives "
      "Benjamini-Hochberg: not at all")

by_planted = patterns.benjamini_yekutieli(
    {k: v for k, v in res2.items() if v}, fdr=0.10)
strong = by_planted.get("three lower closes in a row")
assert strong is not None and strong["survives_by"], \
    f"an unmistakable planted edge did not survive the stricter check: {strong}"
assert strong["c_m"] > 1.0, \
    "the harmonic-sum correction factor must make BY strictly stricter than BH"
print(f"an unmistakable planted edge survives the stricter check too "
      f"(c(m) = {strong['c_m']}, so BY's cutoff is {strong['c_m']:.2f}x "
      f"tighter than BH's at the same family size)")

# BY is a cross-check, not a replacement: it must never be asked to survive
# on its own without BH having run first, and it must be at least as hard
# to survive as BH at the same p and family size
import inspect as _insp
assert "cross-check" in patterns.benjamini_yekutieli.__doc__
print("its own docstring says what it is for, so it cannot be swapped in "
      "for benjamini_hochberg by a future edit that does not read this far")

print("\nBENJAMINI-YEKUTIELI PINNED")
