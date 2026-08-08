"""How close is this company to not being able to pay its debts?

A Moody's Analytics EDF report answers that for a fee. Most of what it
contains is not proprietary: the model underneath is Merton's, published
in 1974, and the inputs are a company's balance sheet and its share
price. The SEC gives the first away through XBRL, and the second is on
every price feed.

What Moody's actually sells is the last step. They map the model's
Distance to Default onto an *empirical* default frequency using a
default database built over decades. That mapping cannot be reproduced
from public data, and the textbook substitute — the normal tail, N(-DD) —
is badly wrong in a specific direction: for Apple it returns a
probability of 0.000000%, which is not a small number, it is a false one.
Real investment-grade companies default at rates the normal distribution
calls impossible.

So this module computes and reports what is honest:

  Distance to Default   how many standard deviations of asset value sit
                        between the company and its default point. A real
                        number, comparable across companies, free.
  Peer percentile       where that sits among comparable companies. This
                        is what a reader actually acts on, and ranking
                        needs no calibration at all — which is why the
                        free version of this report is strongest exactly
                        where the paid one is most expensive.
  The drivers           leverage and asset volatility, the two things a
                        reader can watch change.

It does NOT report a default probability, because it cannot compute one
that would be true. Saying "0.000000%" would be worse than saying
nothing.
"""
from __future__ import annotations

import math
from statistics import NormalDist

_N = NormalDist().cdf

# The KMV default point: all short-term liabilities plus half the
# long-term. A firm does not default when total debt exceeds assets, it
# defaults when it cannot meet what is due, so the near-term claims carry
# full weight and the rest carries half. This is the published
# convention, not a fitted parameter.
LONG_TERM_WEIGHT = 0.5
HORIZON_YEARS = 1.0
# Trading days behind the volatility estimate. Sixty is what the
# published price book carries; it is short for this purpose and the
# report says so rather than pretending otherwise.
MIN_OBS = 40


def equity_volatility(closes) -> float | None:
    """Annualised volatility of daily log returns. None if unmeasurable."""
    c = [float(x) for x in (closes or []) if x and float(x) > 0]
    if len(c) < MIN_OBS + 1:
        return None
    rets = [math.log(c[i] / c[i - 1]) for i in range(1, len(c))]
    n = len(rets)
    mean = sum(rets) / n
    var = sum((r - mean) ** 2 for r in rets) / (n - 1)
    sd = math.sqrt(var) * math.sqrt(252.0)
    return sd if sd > 1e-6 else None


def solve_merton(equity: float, default_point: float, equity_vol: float,
                 rf: float = 0.0375, T: float = HORIZON_YEARS,
                 iters: int = 300) -> tuple[float, float] | None:
    """Back out asset value and asset volatility from observable equity.

    Equity is a call option on the firm's assets struck at its debt, so
    two equations in two unknowns:
        E   = V*N(d1) - D*exp(-rT)*N(d2)
        sE*E = N(d1)*sV*V
    Solved by fixed-point iteration. Returns None if it will not converge
    — a non-converging solve means the inputs are inconsistent, and a
    number produced anyway would be arithmetic rather than measurement.
    """
    if not (equity > 0 and default_point > 0 and equity_vol and equity_vol > 0):
        return None
    V, sV = equity + default_point, equity_vol * equity / (equity + default_point)
    for _ in range(iters):
        if sV <= 1e-9 or V <= 0:
            return None
        rt = sV * math.sqrt(T)
        d1 = (math.log(V / default_point) + (rf + sV * sV / 2) * T) / rt
        d2 = d1 - rt
        nd1 = _N(d1)
        if nd1 <= 1e-12:
            return None
        V_new = (equity + default_point * math.exp(-rf * T) * _N(d2)) / nd1
        sV_new = equity_vol * equity / (nd1 * V_new)
        if abs(V_new - V) / max(V, 1.0) < 1e-10 and abs(sV_new - sV) < 1e-12:
            return V_new, sV_new
        V, sV = V_new, sV_new
    return None      # did not converge — say so rather than round it off


def distance_to_default(asset_value: float, asset_vol: float,
                        default_point: float, drift: float = 0.0375,
                        T: float = HORIZON_YEARS) -> float | None:
    """Standard deviations of asset value between the firm and default."""
    if not (asset_value > 0 and asset_vol > 0 and default_point > 0):
        return None
    return ((math.log(asset_value / default_point)
             + (drift - asset_vol * asset_vol / 2) * T)
            / (asset_vol * math.sqrt(T)))


def default_point(current_liabilities: float | None,
                  total_liabilities: float | None) -> float | None:
    """ST + 0.5*LT, refusing to guess when a component is missing."""
    if current_liabilities is None or total_liabilities is None:
        return None
    long_term = max(0.0, total_liabilities - current_liabilities)
    dp = current_liabilities + LONG_TERM_WEIGHT * long_term
    return dp if dp > 0 else None


def band(dd: float | None) -> str | None:
    """Plain words for a Distance to Default.

    Deliberately coarse. The boundaries are round numbers chosen to be
    readable, not fitted to a default database — the whole point of this
    module is that the fitted version is the part that cannot be had for
    free, and dressing round numbers up as calibration would be the same
    dishonesty in a different coat.
    """
    if dd is None:
        return None
    if dd >= 7:
        return "very far from trouble"
    if dd >= 4:
        return "comfortable"
    if dd >= 2.5:
        return "watch it"
    if dd >= 1:
        return "close to the edge"
    return "in distress on this measure"


def report(ticker: str, equity: float | None, closes,
           current_liabilities: float | None, total_liabilities: float | None,
           rf: float = 0.0375, as_of: str | None = None) -> dict:
    """A full assessment, or an explicit refusal naming what was missing."""
    out: dict = {"ticker": (ticker or "").upper(), "as_of": as_of,
                 "missing": [], "dd": None, "band": None}
    if not equity or equity <= 0:
        out["missing"].append("market capitalisation")
    vol = equity_volatility(closes)
    if vol is None:
        out["missing"].append(f"share-price history ({MIN_OBS}+ days)")
    dp = default_point(current_liabilities, total_liabilities)
    if dp is None:
        out["missing"].append("balance sheet (current and total liabilities)")
    if out["missing"]:
        out["verdict"] = ("Cannot assess — missing "
                          + ", ".join(out["missing"]) + ".")
        return out

    out["equity"] = equity
    out["equity_vol"] = round(vol, 4)
    out["default_point"] = dp
    out["current_liabilities"] = current_liabilities
    out["total_liabilities"] = total_liabilities

    solved = solve_merton(equity, dp, vol, rf)
    if solved is None:
        out["verdict"] = ("Cannot assess — the asset-value solve did not "
                          "converge on these inputs.")
        out["missing"].append("a converging solve")
        return out
    V, sV = solved
    dd = distance_to_default(V, sV, dp, rf)
    out.update(asset_value=round(V, 2), asset_vol=round(sV, 4),
               dd=None if dd is None else round(dd, 2), band=band(dd),
               # market leverage: the single most watchable driver
               market_leverage=round(dp / V, 4) if V else None)
    out["verdict"] = (
        f"{out['dd']} standard deviations from its default point — "
        f"{out['band']}." if dd is not None else "Cannot assess.")
    return out


def percentile(dd: float, peers: list[float]) -> int | None:
    """Where a Distance to Default sits among its peers.

    The most useful number in the whole report and the only one that needs
    no proprietary calibration: a ranking is invariant to whatever mapping
    would turn these into probabilities. Where the paid report is most
    expensive, the free one is exactly as good.
    """
    vals = [p for p in (peers or []) if p is not None]
    if len(vals) < 5:
        return None
    below = sum(1 for v in vals if v < dd)
    return int(round(100.0 * below / len(vals)))


# --------------------- reading a filed balance sheet ---------------------
# Filers do not all tag the same way. Carnival reports no `Liabilities`
# line at all, so the total has to come from the accounting identity —
# assets equal liabilities plus equity — rather than being given up as
# unavailable. Ford tags it directly. Both must work, and a company that
# supports neither route has to be refused rather than approximated.
_FORMS = ("10-K", "10-Q", "20-F", "40-F")


def _latest(facts: dict, tag: str) -> dict | None:
    """Most recent reported value for a us-gaap tag, from a companyfacts dict."""
    node = (facts.get("facts", {}).get("us-gaap", {}) or {}).get(tag)
    if not node:
        return None
    best = None
    for rows in (node.get("units") or {}).values():
        for r in rows:
            if r.get("form") in _FORMS and r.get("val") is not None and r.get("end"):
                if best is None or r["end"] > best["end"]:
                    best = r
    return best


def balance_sheet(facts: dict) -> dict:
    """Current and total liabilities, however this filer chose to tag them."""
    out = {"current_liabilities": None, "total_liabilities": None,
           "as_of": None, "source": None}
    cur = _latest(facts, "LiabilitiesCurrent")
    if cur:
        out["current_liabilities"] = float(cur["val"])
        out["as_of"] = cur["end"]

    tot = _latest(facts, "Liabilities")
    if tot:
        out["total_liabilities"] = float(tot["val"])
        out["as_of"] = out["as_of"] or tot["end"]
        out["source"] = "Liabilities"
        return out

    # assets = liabilities + equity, so liabilities = assets - equity
    lse = _latest(facts, "LiabilitiesAndStockholdersEquity")
    eq = (_latest(facts, "StockholdersEquity")
          or _latest(facts,
                     "StockholdersEquityIncludingPortionAttributableTo"
                     "NoncontrollingInterest"))
    if lse and eq and lse["end"] == eq["end"]:
        total = float(lse["val"]) - float(eq["val"])
        if total > 0:
            out["total_liabilities"] = total
            out["as_of"] = lse["end"]
            out["source"] = "assets minus equity"
    return out
