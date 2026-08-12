"""Find patterns — and refuse to believe them until they survive a null.

This project already learned what happens when a pattern is adopted
first and tested last: the pullback rule was built, tuned, published and
acted on, and when it was finally run against coin-flip entry through
identical exit code it came out statistically indistinguishable from the
coin flip, twice. Months of work on a signal that carried no
information.

So the order is inverted here. A pattern does not exist in this module
until it has been measured against a null, and the null is the strict
kind. Three things ruin retail backtests, and all three are handled:

1. THE MARKET DID IT, NOT THE PATTERN. If a pattern happens to fire on
   days the whole market rose, every "win" is one event wearing a
   hundred hats. The null here is DATE-MATCHED: for a pattern that fired
   on 400 stock-days, the null draws 400 random stock-days from exactly
   the same calendar dates. Whatever the market did on those days, it
   did to both. This is the single most important thing in the file.

2. TESTING TWENTY THINGS AND KEEPING THE BEST. At p < 0.05, one in
   twenty coin flips looks significant; a library of twenty patterns
   will hand you a winner every time. Benjamini-Hochberg controls the
   false discovery rate across the whole family, and the report states
   how many were tested so the reader can see the correction was needed.

3. A RESULT TOO SMALL TO TRADE. Statistical significance is not an
   edge: a pattern can be real and still lose money after costs. Every
   surviving pattern is reported with its effect size in percent AND
   against a round-trip cost, so "significant but not worth it" is
   visible rather than flattering.

Nothing here predicts. It measures whether a described shape has been
followed by different-than-usual returns, on this data, over this
window — and says how confident that is, with what is left after costs.
"""
from __future__ import annotations

import math
import random
from statistics import NormalDist

_PHI = NormalDist().cdf

# What a round trip actually costs a small account: spread plus
# commission, as a share of the position. A pattern whose whole effect
# is smaller than this is a way to pay a broker.
ROUND_TRIP_COST_PCT = 0.20

# How far ahead an entry is measured. Short enough that the signal has
# not been swamped by everything else; long enough to be tradeable.
DEFAULT_HORIZON = 5


# --------------------------------------------------------------------
# The pattern library. Each takes a window of closes ENDING at the
# candidate day and returns True if the shape is present. They see only
# the past — a pattern that peeks at the future is the other classic way
# to produce a beautiful backtest that loses money.
# --------------------------------------------------------------------

def _ret(a, b):
    return (a / b - 1.0) if (a and b and b > 0) else 0.0


def p_three_down(w):
    """Three consecutive lower closes."""
    return len(w) >= 4 and w[-1] < w[-2] < w[-3] < w[-4]


def p_gap_down_3pct(w):
    """A single session down 3% or more."""
    return len(w) >= 2 and _ret(w[-1], w[-2]) <= -0.03


def p_gap_up_3pct(w):
    return len(w) >= 2 and _ret(w[-1], w[-2]) >= 0.03


def p_new_20d_high(w):
    return len(w) >= 21 and w[-1] >= max(w[-21:])


def p_new_20d_low(w):
    return len(w) >= 21 and w[-1] <= min(w[-21:])


def p_pullback_in_uptrend(w):
    """The project's own falsified rule, kept in the library on purpose.

    It belongs here precisely because it is known to be worthless: a
    discovery framework that cannot reproduce a known negative result is
    not measuring anything. If this one ever comes out 'significant',
    the framework is broken, not the market.
    """
    if len(w) < 51:
        return False
    sma = sum(w[-50:]) / 50
    off_high = _ret(w[-1], max(w[-20:]))
    return w[-1] > sma and -0.08 <= off_high <= -0.02


def p_above_50d(w):
    if len(w) < 51:
        return False
    return w[-1] > sum(w[-50:]) / 50


def p_below_50d(w):
    if len(w) < 51:
        return False
    return w[-1] < sum(w[-50:]) / 50


def p_quiet_week(w):
    """Five sessions with no move bigger than 1% — unusual calm."""
    if len(w) < 6:
        return False
    return all(abs(_ret(w[-i], w[-i - 1])) < 0.01 for i in range(1, 6))


def p_wide_week(w):
    """Five sessions with at least three moves bigger than 2%."""
    if len(w) < 6:
        return False
    big = sum(1 for i in range(1, 6) if abs(_ret(w[-i], w[-i - 1])) > 0.02)
    return big >= 3


def p_down_10pct_from_20d_high(w):
    if len(w) < 21:
        return False
    return _ret(w[-1], max(w[-21:])) <= -0.10


LIBRARY = {
    "three lower closes in a row": p_three_down,
    "a single session down 3% or more": p_gap_down_3pct,
    "a single session up 3% or more": p_gap_up_3pct,
    "a new 20-day high": p_new_20d_high,
    "a new 20-day low": p_new_20d_low,
    "10% or more below its 20-day high": p_down_10pct_from_20d_high,
    "trading above its 50-day average": p_above_50d,
    "trading below its 50-day average": p_below_50d,
    "a week with no move over 1%": p_quiet_week,
    "a week with three moves over 2%": p_wide_week,
    "pullback in an uptrend (this project's falsified rule)":
        p_pullback_in_uptrend,
}


# --------------------------------------------------------------------
# Measurement
# --------------------------------------------------------------------

def occurrences(series: dict, fn, horizon: int = DEFAULT_HORIZON,
                min_history: int = 51) -> list:
    """[(date_index, ticker, forward_return)] wherever the shape appears.

    Forward return is measured from the close on the signal day to the
    close `horizon` sessions later — the same convention for the pattern
    and for the null, which is what makes them comparable.
    """
    hits = []
    for t, closes in (series or {}).items():
        c = [x if (x and x > 0) else None for x in (closes or [])]
        n = len(c)
        for i in range(min_history, n - horizon):
            if c[i] is None or c[i + horizon] is None:
                continue
            window = [x for x in c[max(0, i - 60):i + 1] if x is not None]
            if len(window) < min_history:
                continue
            try:
                if fn(window):
                    hits.append((i, t, _ret(c[i + horizon], c[i])))
            except Exception:                                 # noqa: BLE001
                continue
    return hits


def date_matched_null(series: dict, hit_days: list, horizon: int,
                      seed: int, min_history: int = 51) -> list:
    """Random entries on THE SAME DAYS the pattern fired.

    This is the heart of the module. A pattern that fires disproportion-
    ately on days the market rallied will show a wonderful average
    return and mean nothing; drawing the null from the same calendar
    days removes that entirely, because whatever the market did that
    day, it did to both samples.
    """
    rng = random.Random(seed)
    by_day: dict = {}
    for t, closes in (series or {}).items():
        c = [x if (x and x > 0) else None for x in (closes or [])]
        for i in range(min_history, len(c) - horizon):
            if c[i] is not None and c[i + horizon] is not None:
                by_day.setdefault(i, []).append((t, _ret(c[i + horizon], c[i])))
    out = []
    for day in hit_days:
        pool = by_day.get(day)
        if pool:
            out.append(rng.choice(pool)[1])
    return out


def _mean(xs):
    return sum(xs) / len(xs) if xs else 0.0


def _sd(xs):
    if len(xs) < 2:
        return 0.0
    m = _mean(xs)
    return math.sqrt(sum((x - m) ** 2 for x in xs) / (len(xs) - 1))


def test_pattern(series: dict, fn, horizon: int = DEFAULT_HORIZON,
                 seeds: int = 200, min_hits: int = 30) -> dict | None:
    """Measure one pattern against its date-matched null.

    Returns None when the shape is too rare to say anything about: a
    pattern with nine occurrences is an anecdote, and reporting a
    p-value for it would be arithmetic dressed as evidence.
    """
    hits = occurrences(series, fn, horizon)
    if len(hits) < min_hits:
        return None
    days = [d for d, _, _ in hits]
    real = [r for _, _, r in hits]
    real_mean = _mean(real)

    null_means = []
    for s in range(seeds):
        draw = date_matched_null(series, days, horizon, seed=s)
        if draw:
            null_means.append(_mean(draw))
    if len(null_means) < 5:
        return None

    nm, nsd = _mean(null_means), _sd(null_means)
    beat = sum(1 for x in null_means if x >= real_mean)
    # THE RESOLUTION LIMIT, which cost a real detection before it was
    # handled: a permutation test with k draws cannot report a p below
    # 1/(k+1). At 25 draws the floor is 0.038, and Benjamini-Hochberg
    # over eleven patterns demands 0.009 of its strongest — so a planted
    # edge of +1.16% at z = 10.9 was measured perfectly and then thrown
    # away by its own p-value floor.
    #
    # When NO null draw beats the real mean, the permutation test has
    # said all it can ("below my floor") and the floor is not the
    # answer. The null means are averages over hundreds of entries, so
    # the central limit theorem makes them near-normal, and the normal
    # tail refines what the permutation could only bound. Used ONLY in
    # that case: where draws did beat it, the empirical count is the
    # honest number and no model improves on it.
    p_perm = (beat + 1) / (len(null_means) + 1)
    z = (real_mean - nm) / nsd if nsd > 1e-12 else 0.0
    p = p_perm
    p_source = "permutation"
    if beat == 0 and z > 0:
        p_norm = 1.0 - _PHI(z)
        if p_norm < p_perm:
            p, p_source = max(p_norm, 1e-12), "normal tail below the floor"
    return {
        "n": len(hits),
        "tickers": len({t for _, t, _ in hits}),
        "mean_pct": round(real_mean * 100, 3),
        "null_mean_pct": round(nm * 100, 3),
        "edge_pct": round((real_mean - nm) * 100, 3),
        "after_costs_pct": round(((real_mean - nm) * 100) - ROUND_TRIP_COST_PCT, 3),
        "z": round(z, 2),
        "p": round(p, 6),
        "p_perm": round(p_perm, 4),
        "p_source": p_source,
        "permutations": len(null_means),
        "horizon": horizon,
        "hit_rate_pct": round(100 * sum(1 for r in real if r > 0) / len(real), 1),
    }


def benjamini_hochberg(results: dict, fdr: float = 0.10) -> dict:
    """Which p-values survive testing a whole family of patterns.

    Eleven patterns at p < 0.05 will hand you a "discovery" one time in
    two by chance alone. BH controls the expected share of false
    discoveries among the things reported, which is the correction that
    matches what this is for: finding candidates worth a second look,
    not proving a theorem.
    """
    items = [(k, v) for k, v in results.items() if v and v.get("p") is not None]
    if not items:
        return {}
    items.sort(key=lambda kv: kv[1]["p"])
    m = len(items)
    cutoff = 0.0
    for i, (_, v) in enumerate(items, start=1):
        if v["p"] <= (i / m) * fdr:
            cutoff = v["p"]
    out = {}
    for k, v in items:
        out[k] = dict(v, survives=bool(cutoff and v["p"] <= cutoff),
                      family_size=m, fdr=fdr)
    return out


def sweep(series: dict, horizon: int = DEFAULT_HORIZON, seeds: int = 200,
          library: dict | None = None, progress=None) -> dict:
    """Test every pattern in the library, then correct for having done so."""
    lib = library or LIBRARY
    raw = {}
    for name, fn in lib.items():
        r = test_pattern(series, fn, horizon=horizon, seeds=seeds)
        raw[name] = r
        if progress:
            progress(f"  {name}: " + ("too rare to test" if not r else
                     f"n={r['n']} edge={r['edge_pct']:+.3f}% p={r['p']:.3f}"))
    return benjamini_hochberg(raw)


def verdict(row: dict | None) -> str:
    """What a result means, in words that do not overclaim."""
    if not row:
        return "too rare on this data to say anything about."
    if not row.get("survives"):
        return (f"no better than random entry on the same days "
                f"(p = {row['p']:.2f} across {row.get('family_size', '?')} "
                f"patterns tested).")
    if row["after_costs_pct"] <= 0:
        return (f"a real difference of {row['edge_pct']:+.2f}% over "
                f"{row['horizon']} sessions — but round-trip costs of "
                f"{ROUND_TRIP_COST_PCT}% eat it, so it is not tradeable.")
    return (f"{row['edge_pct']:+.2f}% over {row['horizon']} sessions against "
            f"random entry on the same days ({row['n']} occurrences, "
            f"p = {row['p']:.3f}), {row['after_costs_pct']:+.2f}% after costs.")
