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
import re
from datetime import date, timedelta
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

# A volatility this high is not a volatility.
#
# The published book had 21 names above 200% annualised and one at 982%,
# against a median of 36%. They are all thinly traded secondary listings —
# OTC ADRs, London IOB lines, grey-market tickers — whose "closing price"
# is a quote that did not trade. The quote sits still for days and then
# catches up in one jump, and the standard deviation of that is arithmetic
# about a data feed, not risk.
#
# It reached the reader as a verdict. QH was published as "in distress on
# this measure" — the strongest words this model can say — on 0.9%
# leverage, because a 471% volatility swamped a balance sheet with almost
# no debt in it. A refusal would have been correct and was available.
VOL_MAX = 1.5

# The tell, and it is not subtle. Measured over the published price book:
# names above 150% annualised have a median of 49% of days with a return
# of EXACTLY zero. Names between 15% and 60% have a median of 0%. A price
# that is unchanged to the cent on half the days is not a price.
FLAT_DAY_MAX = 0.20


# Companies the model must refuse on sector, not on arithmetic.
#
# Merton reads "current liabilities" as debt coming due against the
# firm's assets. For a bank, deposits ARE the liabilities — they fund the
# business and grow when it is healthy. For an insurer, "current
# liabilities" are claims payable, funded by premiums already collected.
# Neither is debt the company might fail to roll over, and the KMV
# literature excludes financials for exactly this reason.
#
# This was live: MOH, a health insurer, ranked fourth-closest-to-trouble
# on the site because its medical claims payable were read as debt coming
# due. The number was arithmetic, not measurement.
FINANCIAL_SIC = (6000, 6799)          # depository, insurance, brokers, REITs


def is_financial(sic) -> bool:
    """Whether an SEC SIC code lands in finance/insurance/real estate."""
    try:
        code = int(str(sic).strip())
    except (TypeError, ValueError):
        return False
    return FINANCIAL_SIC[0] <= code <= FINANCIAL_SIC[1]


def parse_submissions_identity(buf: bytes) -> dict:
    """SIC code, sector name and legal name from a SEC submissions buffer.

    The two files that fetch this (app.py, scripts/scheduled_scan.py)
    stream the submissions endpoint and stop after ~64KB rather than
    downloading a filing history nobody asked for — so `buf` is very
    often a TRUNCATED, invalid JSON document. This is a regex on the raw
    bytes rather than json.loads for exactly that reason: the buffer is
    not guaranteed to parse and never will be.

    All three fields sit within the first few hundred bytes of any real
    filer's submissions JSON (`cik, entityType, sic, sicDescription,
    ownerOrg, ..., name, tickers, ...`), well inside the truncation
    window — the SIC lookup this project already made was paying for
    this data and discarding it. `formerNames` appears later in the
    payload and also contains nested "name" keys; taking the FIRST match
    is what keeps this reading the top-level field and not a former one.

    Never raises. Any field not found is None — a truncated or garbage
    buffer is a data gap, not an error.
    """
    m_sic = re.search(rb'"sic"\s*:\s*"?(\d{2,4})"?', buf)
    m_desc = re.search(rb'"sicDescription"\s*:\s*"([^"]*)"', buf)
    m_name = re.search(rb'"name"\s*:\s*"([^"]*)"', buf)
    return {"sic": m_sic.group(1).decode() if m_sic else None,
           "sic_desc": m_desc.group(1).decode("utf-8", "replace") if m_desc else None,
           "name": m_name.group(1).decode("utf-8", "replace") if m_name else None}


def secondary_line(ticker: str) -> str | None:
    """Why this ticker's price line cannot price its filings, or None.

    BABAF — Alibaba's over-the-counter ordinary-share line — was the
    front page's number one "closest to trouble" because the SEC filing's
    share count (an ADS-equivalent figure) was multiplied by the ordinary
    -share OTC price: equity understated roughly eight times, leverage
    "79%" against a true ~29%. The filing and that price line do not
    describe the same share, and no arithmetic on the pair is a
    measurement. US OTC foreign-ordinary lines are five letters ending in
    F; London IOB and Milan cross-listings carry their own suffixes.
    """
    t = (ticker or "").upper()
    if len(t) == 5 and t.endswith("F") and "." not in t and "-" not in t:
        return ("this is an over-the-counter line of a foreign company — "
                "its SEC filing and this price line do not describe the "
                "same share, so no honest number can come from the pair")
    if t.endswith((".IL", ".XC")) or (("." in t) and t.split(".")[0][:1] == "1"
                                      and t.endswith(".MI")):
        return ("this is a secondary cross-listing — measured under its "
                "primary ticker if it files in the US")
    return None


def equity_vs_cap(equity: float | None, market_cap: float | None) -> str | None:
    """Why shares x price cannot be this company's equity, or None.

    An independent market-cap reading catches the unit mismatches the
    suffix rule cannot: if the two disagree by more than double, the
    share count and the price line are not about the same instrument,
    and the distance built on them would be arithmetic, not measurement.
    """
    if not equity or not market_cap or market_cap <= 0:
        return None
    ratio = equity / market_cap
    if ratio > 2.0 or ratio < 0.5:
        return (f"the share count and the price line disagree — they imply "
                f"{equity / 1e9:.0f}bn of equity against {market_cap / 1e9:.0f}bn "
                f"on the market screen, so they do not describe the same share")
    return None


NOT_MODELLED = ("Banks and insurers are not modelled here. Their balance "
                "sheets are the business — deposits and claims payable are "
                "how they operate, not debt coming due against too little "
                "to pay it — so this model's default point is meaningless "
                "for them, and a number would be worse than a refusal.")


def flat_day_share(closes) -> float | None:
    """The share of days whose close did not move at all.

    Separated out so the publisher and the model apply one definition of
    "this quote is not trading", and so it can be tested against real
    series rather than asserted.
    """
    c = [float(x) for x in (closes or []) if x and float(x) > 0]
    if len(c) < 3:
        return None
    rets = [math.log(c[i] / c[i - 1]) for i in range(1, len(c))]
    return sum(1 for r in rets if abs(r) < 1e-9) / len(rets)


def usable_volatility(vol: float | None, closes=None) -> str | None:
    """Why this volatility must not be used, or None if it may be.

    Returns the reason rather than a boolean, because a refusal that
    cannot say what it refused is indistinguishable from a bug.
    """
    if vol is None or vol <= 0:
        return "no volatility could be measured"
    if vol > VOL_MAX:
        return (f"{vol * 100:.0f}% annualised is not a measurement of risk — "
                f"a quote that jumps like that is one that is not trading")
    flat = flat_day_share(closes) if closes is not None else None
    if flat is not None and flat > FLAT_DAY_MAX:
        return (f"the price was unchanged on {flat * 100:.0f}% of days, so "
                f"this is a stale quote rather than a traded price")
    return None


def measured_volatility(closes) -> tuple[float | None, str | None]:
    """(annualised volatility, None) — or (None, the reason it is refused).

    The reason travels with the refusal because the two failure modes
    mean opposite things to a reader: "not enough history" is a young
    listing, "the quote does not trade" is a dead one, and the report
    used to blame the first for the second.
    """
    c = [float(x) for x in (closes or []) if x and float(x) > 0]
    if len(c) < MIN_OBS + 1:
        return None, f"share-price history ({MIN_OBS}+ days)"
    rets = [math.log(c[i] / c[i - 1]) for i in range(1, len(c))]
    n = len(rets)
    mean = sum(rets) / n
    var = sum((r - mean) ** 2 for r in rets) / (n - 1)
    sd = math.sqrt(var) * math.sqrt(252.0)
    if sd <= 1e-6:
        return None, "a flat price series, which is a data problem, not a riskless stock"
    bad = usable_volatility(sd, c)
    return (None, bad) if bad else (sd, None)


def equity_volatility(closes) -> float | None:
    """Annualised volatility of daily log returns. None if unmeasurable.

    "Unmeasurable" includes measurable-but-meaningless: see VOL_MAX and
    FLAT_DAY_MAX. Returning a number here that is arithmetic about a
    stale quote puts it on a page under the word "distress".
    """
    vol, _ = measured_volatility(closes)
    return vol


def _merton_residual(V: float, sV: float, equity: float, default_point: float,
                     equity_vol: float, rf: float, T: float
                     ) -> tuple[float, float] | None:
    """The two equations, as (f1, f2) that are zero at the true (V, sV)."""
    if sV <= 1e-9 or V <= 0:
        return None
    rt = sV * math.sqrt(T)
    d1 = (math.log(V / default_point) + (rf + sV * sV / 2) * T) / rt
    d2 = d1 - rt
    nd1, nd2 = _N(d1), _N(d2)
    f1 = V * nd1 - default_point * math.exp(-rf * T) * nd2 - equity
    f2 = nd1 * sV * V - equity_vol * equity
    return f1, f2


def _solve_merton_fixed_point(equity: float, default_point: float, equity_vol: float,
                              rf: float, T: float, V: float, sV: float, iters: int
                              ) -> tuple[float, float] | None:
    """Bharath & Shumway's direct recursion — fast, and right most of the time."""
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
    return None      # did not converge here — the fallback gets a turn


def _solve_merton_newton(equity: float, default_point: float, equity_vol: float,
                         rf: float, T: float, V: float, sV: float,
                         iters: int = 200) -> tuple[float, float] | None:
    """Damped 2-D Newton, tried only after the fixed point gives up.

    High leverage combined with equity volatility near VOL_MAX is where the
    fixed-point recursion above stops converging — but a real, well-behaved
    root still exists there (a highly-levered, genuinely volatile firm is
    exactly the distress case this report exists to catch). A numerical
    Jacobian and a halving step keep this from diverging the way the plain
    recursion does; it is tried second because it costs more per step and
    the fixed point is right on the overwhelming majority of real inputs.
    """
    h = 1e-6
    for _ in range(iters):
        f = _merton_residual(V, sV, equity, default_point, equity_vol, rf, T)
        if f is None:
            return None
        f1, f2 = f
        tol = 1e-9 * max(equity, 1.0)
        if abs(f1) < tol and abs(f2) < tol:
            return V, sV
        fV = _merton_residual(V * (1 + h), sV, equity, default_point, equity_vol, rf, T)
        fS = _merton_residual(V, sV + h, equity, default_point, equity_vol, rf, T)
        if fV is None or fS is None:
            return None
        dV_step = h * V
        a = (fV[0] - f1) / dV_step   # df1/dV
        c = (fV[1] - f2) / dV_step   # df2/dV
        b = (fS[0] - f1) / h         # df1/dsV
        d = (fS[1] - f2) / h         # df2/dsV
        det = a * d - b * c
        if abs(det) < 1e-14:
            return None
        delta_V = (f1 * d - f2 * b) / det
        delta_S = (a * f2 - c * f1) / det
        step = 1.0
        while True:
            V_new, sV_new = V - step * delta_V, sV - step * delta_S
            if V_new > 0 and sV_new > 1e-9:
                break
            step *= 0.5
            if step < 1e-6:
                return None
        V, sV = V_new, sV_new
    return None


def solve_merton(equity: float, default_point: float, equity_vol: float,
                 rf: float = 0.0375, T: float = HORIZON_YEARS,
                 iters: int = 300) -> tuple[float, float] | None:
    """Back out asset value and asset volatility from observable equity.

    Equity is a call option on the firm's assets struck at its debt, so
    two equations in two unknowns:
        E   = V*N(d1) - D*exp(-rT)*N(d2)
        sE*E = N(d1)*sV*V
    Tried first by fixed-point iteration (fast, right almost always); a
    damped Newton solve is tried second, only on the inputs that defeat
    the fixed point, before this gives up and returns None. Both failing
    means the inputs are inconsistent, and a number produced anyway would
    be arithmetic rather than measurement.
    """
    if not (equity > 0 and default_point > 0 and equity_vol and equity_vol > 0):
        return None
    V0 = equity + default_point
    sV0 = equity_vol * equity / (equity + default_point)
    got = _solve_merton_fixed_point(equity, default_point, equity_vol, rf, T, V0, sV0, iters)
    if got is not None:
        return got
    return _solve_merton_newton(equity, default_point, equity_vol, rf, T, V0, sV0)


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
    """ST + 0.5*LT, refusing to guess when a component is missing.

    Total below current is not a company with negative long-term debt, it
    is two figures that do not belong to the same balance sheet — the
    signature of a filer whose `Liabilities` tag stopped years before its
    `LiabilitiesCurrent` tag did. Clamping it to zero turned that
    contradiction into a confident, wrong default point, which is the
    fail-open this module exists to prevent. It is refused instead.
    """
    if current_liabilities is None or total_liabilities is None:
        return None
    if total_liabilities < current_liabilities:
        return None
    dp = current_liabilities + LONG_TERM_WEIGHT * (
        total_liabilities - current_liabilities)
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


def horizon_view(equity: float, default_point: float, equity_vol: float,
                 rf: float = 0.0375,
                 horizons: tuple = (0.5, 1.0, 2.0, 5.0)) -> list[dict]:
    """Distance to Default at a few different time horizons, not just one.

    Moody's shows a multi-year EDF term structure — but that is a default
    PROBABILITY at each horizon, which this module refuses to compute for
    the same reason it refuses one at all (see report()'s docstring). The
    honest analog stays in distance units at every horizon: how many
    standard deviations of asset value sit between the firm and default,
    asked over 6 months, a year, two years, five.

    Each horizon is solved INDEPENDENTLY — the same solve_merton() then
    distance_to_default() pair report() already calls at T=1, called
    again at each other T — because equity's value as an option on the
    firm's assets depends on the horizon it is priced against: the same
    observed equity implies a different (asset value, asset volatility)
    pair at a 6-month horizon than at a 5-year one. Holding the T=1
    solve's numbers fixed and only changing T in the distance formula
    would be cheaper, but it would not be answering the 6-month or
    5-year question — it would be re-describing the 1-year answer.

    Capped at 5 years on purpose. Asset volatility and the risk-free
    drift are both held constant over T here, and that assumption gets
    materially weaker the further out it is stretched — a number this
    project could not stand behind at 10 years would undercut the same
    honesty principle the no-probability rule exists to protect.

    Returns [{"years", "dd", "band"}, ...], one entry per horizon, in the
    order given. A horizon whose solve does not converge gets dd=None and
    band=None — present in the list, not silently dropped from it, the
    same rule credit.history() already follows for a single unsolvable
    point.
    """
    out = []
    for h in horizons:
        solved = solve_merton(equity, default_point, equity_vol, rf, T=h)
        dd = None
        if solved is not None:
            V, sV = solved
            dd = distance_to_default(V, sV, default_point, rf, T=h)
        out.append({"years": h, "dd": round(dd, 2) if dd is not None else None,
                    "band": band(dd)})
    return out


def report(ticker: str, equity: float | None, closes,
           current_liabilities: float | None, total_liabilities: float | None,
           rf: float = 0.0375, as_of: str | None = None,
           vol: float | None = None, vol_obs: int | None = None,
           currency_refused: str | None = None) -> dict:
    """A full assessment, or an explicit refusal naming what was missing.

    `vol` lets the caller supply a volatility measured over more history
    than `closes` contains. The scan publishes one per ticker computed
    from years of returns; `closes` is a 60-day window kept small enough
    to download. When a measured volatility is passed it is used and
    `closes` only has to be long enough to price the shares.

    `currency_refused` names a currency (from balance_sheet()'s
    `currency_only`) when this filer's balance-sheet tags exist but only
    in that currency — a more specific, more honest refusal than the
    generic "missing balance sheet", and never a converted number:
    converting at a guessed rate would produce a figure the model does
    not mean.
    """
    out: dict = {"ticker": (ticker or "").upper(), "as_of": as_of,
                 "missing": [], "dd": None, "band": None}
    if current_liabilities is None and total_liabilities is None and currency_refused:
        out["missing"].append("a balance sheet in USD")
        out["verdict"] = (f"Cannot assess — files in {currency_refused}; "
                          f"converting at a guessed rate would produce a "
                          f"number the model does not mean.")
        out["currency_refused"] = currency_refused
        return out
    if not equity or equity <= 0:
        out["missing"].append("market capitalisation")
    supplied = vol is not None and vol > 0
    if supplied:
        # A volatility handed in from the published book gets the same
        # examination as one measured here. It did not before, and that
        # is exactly how a 471% annualised figure — a stale OTC quote,
        # not a price — reached the page as a company "in distress".
        #
        # `closes` is deliberately NOT passed. It is the short window used
        # to price the shares, not the series this volatility was measured
        # from; judging one by the other would refuse a real company for
        # the shape of a window that had nothing to do with the number.
        # The series it DID come from is screened where it is computed.
        bad = usable_volatility(vol)
        if bad:
            out["missing"].append("a usable volatility")
            out["verdict"] = f"Cannot assess — {bad}."
            out["vol_refused"] = bad
            return out
    else:
        vol, why_not = measured_volatility(closes)
        vol_obs = None
        if vol is None and why_not and "history" not in why_not:
            # refused, not absent — the history is there, the quote is not
            # a price. Blaming "not enough history" for a stale quote sent
            # readers a reason about the wrong thing.
            out["missing"].append("a usable volatility")
            out["vol_refused"] = why_not
            out["verdict"] = f"Cannot assess — {why_not}."
            return out
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
    # How much price history the volatility rests on. KMV uses a year of
    # daily returns; the published price book carries 60 closes, which is
    # about a quarter, and a quarter of returns estimates an annualised
    # volatility with roughly a +/-9% standard error at 22% vol. That
    # propagates straight into the distance, so the report states the
    # sample it used rather than presenting every figure as equally solid.
    out["vol_obs"] = (vol_obs if supplied and vol_obs
                      else len([1 for x in (closes or []) if x and float(x) > 0]))
    out["vol_source"] = "published" if supplied else "price window"
    out["vol_thin"] = out["vol_obs"] < 200
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
    out["driven_by"] = driven_by(equity, dp, vol, dd, rf)
    out["verdict"] = (
        f"{out['dd']} standard deviations from its default point — "
        f"{out['band']}{_because(out['driven_by'])}."
        if dd is not None else "Cannot assess.")
    return out


# What actually made the distance short, in the fewest words that stay
# true. Kept next to the verdict because these two must never disagree.
_BECAUSE = {
    "swings": ", and that is the share price swinging rather than the debts",
    "debts": ", and that is what it owes",
    "both": ", from both what it owes and how hard the shares swing",
}


def _because(d: str | None) -> str:
    return _BECAUSE.get(d or "", "")


def history(closes, shares: float | None, default_point: float | None,
            vol: float | None, rf: float = 0.0375) -> list | None:
    """Distance to Default on each of the given days.

    The commercial report's most-read page is the metric over time — a
    company at 4.0 and falling is a different story from one at 4.0 and
    rising, and a single figure cannot tell them apart.

    It is computable from what is already free: the share count and the
    balance sheet are fixed between filings, so the distance moves with
    the market value of equity, which moves with the closing price. Each
    day is re-solved from that day's market capitalisation.

    The balance sheet is HELD CONSTANT across the window — it is the one
    filed at the end of it — so this shows how the market re-rated a fixed
    set of debts, not how the debts changed. The report has to say that
    where the reader can see it; the number would be misread otherwise.
    """
    if not closes or not shares or not default_point or not vol or vol <= 0:
        return None
    # One output element per input close, None where a day could not be
    # solved — NEVER silently shorter. The page pairs this list with a
    # list of dates, and a silent skip shifted every date after it: the
    # sparkline claimed "56 trading days to 2026-08-12" for a ticker
    # whose 56th value belonged to a different day entirely.
    out = []
    for c in closes:
        try:
            price = float(c)
        except (TypeError, ValueError):
            out.append(None)
            continue
        if price <= 0:
            out.append(None)
            continue
        solved = solve_merton(shares * price, default_point, vol, rf)
        d = (distance_to_default(solved[0], solved[1], default_point, rf)
             if solved else None)
        out.append(round(d, 3) if d is not None else None)
    return out if any(v is not None for v in out) else None


# A volatility to hold the model at when asking "would this company still
# look short of room if its shares moved like an ordinary one?". Set from
# the measured median across the published book (48%), not fitted.
REFERENCE_VOL = 0.48


def driven_by(equity: float | None, default_point: float | None,
              equity_vol: float | None, dd: float | None,
              rf: float = 0.0375) -> str | None:
    """Which input made this distance short: the debts, or the swings.

    The reason this exists, in one line from the published book:

        F     debts  78.1% of its value, shares swing 37%/yr -> watch it
        LITE  debts   5.9% of its value, shares swing 94%/yr -> watch it

    Both carry the same two words, and a reader takes "watch it" to mean
    the company might struggle to pay what it owes. For Ford that is what
    the number says. For Lumentum, which owes six percent of its market
    value, it is not — the distance is short because the share price is
    violent, which is a fact about the stock, not the balance sheet.

    Merton is a market-implied measure and mixing the two is exactly what
    it is for. But a label that cannot tell them apart is a label that
    reads as random, so the page has to name the driver.

    Computed, not thresholded: the same balance sheet is re-solved with
    the shares held at an ordinary volatility. If the company then has
    plenty of room, the volatility was the binding input.
    """
    if dd is None or not equity or not default_point or not equity_vol:
        return None
    if dd >= 4.0:
        return None                     # nothing to explain away
    solved = solve_merton(equity, default_point, REFERENCE_VOL, rf)
    if solved is None:
        return None
    at_ref = distance_to_default(solved[0], solved[1], default_point, rf)
    if at_ref is None:
        return None
    if at_ref >= 4.0 and at_ref > dd + 0.5:
        return "swings"
    if equity_vol <= REFERENCE_VOL:
        return "debts"
    return "both"


def restate(rep: dict | None, price: float | None,
            rf: float = 0.0375) -> dict | None:
    """The same balance sheet, re-solved against today's closing price.

    A published standing is only as current as the price it was solved
    from, and that made the whole book expensive: every scheduled run
    re-measured the same names to refresh a price it already had, spent
    its entire SEC budget doing it, and coverage stopped growing at the
    size of one run — 97 companies, all rebuilt every time, while a
    reader asking about the 98th got nothing.

    Nothing in a filing changes between quarters. What moves daily is the
    share price, and re-solving for that costs no network call at all. So
    the book stores the filing and the distance is restated here, which
    frees every run's SEC budget to measure companies it has never seen.

    Returns None when it cannot be restated, so a caller falls back to
    the stored figure rather than showing an invented one.
    """
    if not rep or rep.get("dd") is None:
        return None
    shares, dp = rep.get("shares"), rep.get("default_point")
    vol = rep.get("equity_vol")
    if not shares or not dp or not vol or vol <= 0:
        return None
    try:
        px = float(price)
    except (TypeError, ValueError):
        return None
    if px <= 0:
        return None
    equity = shares * px
    solved = solve_merton(equity, dp, vol, rf)
    if solved is None:
        return None
    dd = distance_to_default(solved[0], solved[1], dp, rf)
    if dd is None:
        return None
    dd = round(dd, 2)
    why = driven_by(equity, dp, vol, dd, rf)
    return dict(rep, dd=dd, band=band(dd), equity=equity,
                asset_value=solved[0], asset_vol=round(solved[1], 4),
                # dp / ASSET value, exactly as report() computes it. It was
                # dp / equity here, and the two disagreed by a factor of
                # four on Ford — the published book said 78% and the page
                # rendered 313%. A reader who checks one figure against
                # another and finds them contradicting stops trusting all
                # of them, and is right to.
                market_leverage=round(dp / solved[0], 4), driven_by=why,
                verdict=f"{dd} standard deviations from its default point "
                        f"— {band(dd)}{_because(why)}.",
                restated=True, stored_dd=rep["dd"])


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


def sic_major_group(sic) -> str | None:
    """The 2-digit SIC major group (e.g. "35" for "Electronic Computers"'s
    "3571") — a coarser sector bucket, used when a company's exact 4-digit
    code has too few other measured names to rank against. On the live
    book (828 entries, 389 measured) exact-SIC grouping gives ≥5 other
    measured peers to only 39% of measured names; the 2-digit group gets
    there for 83% — most 4-digit codes are simply too narrow a slice of a
    universe this size."""
    s = str(sic or "").strip()
    return s[:2] if len(s) >= 2 and s[:2].isdigit() else None


def safety_percentile(value: float | None, peers: list,
                      lower_is_safer: bool = False) -> int | None:
    """percentile(), generalised so the result always reads as "safer
    than X% of peers", whichever direction is actually safe for this
    metric.

    Distance to default is safer-when-higher, which is what percentile()
    already assumes. Leverage and asset volatility are the opposite —
    more of either is worse — so scoring them with percentile() unchanged
    would report a highly-levered company as ranking HIGH: good news,
    read backwards, for exactly the companies where getting it right
    matters most. lower_is_safer=True flips the comparison so "higher
    percentile always means safer" holds for any of the three metrics
    this report shows a percentile for.
    """
    if value is None:
        return None
    vals = [p for p in (peers or []) if p is not None]
    if len(vals) < 5:
        return None
    safer = sum(1 for v in vals if v > value) if lower_is_safer \
        else sum(1 for v in vals if v < value)
    return int(round(100.0 * safer / len(vals)))


def _sector_pool(measured: dict, ticker: str, sic, min_peers: int) -> tuple:
    """(level, key, mates) — the narrowest of (exact SIC, its 2-digit
    major group, the whole book) that has at least `min_peers` OTHER
    measured names. `mates` is the list of those other reports, `ticker`
    itself always excluded. Falls all the way to the whole book — the
    same floor percentile() itself already enforces — rather than ever
    reporting "not enough data" when a wider, still-meaningful pool
    exists."""
    major = sic_major_group(sic)
    if sic:
        exact = [r for t2, r in measured.items()
                if t2 != ticker and r.get("sic") == sic]
        if len(exact) >= min_peers:
            return "sic", sic, exact
    if major:
        maj = [r for t2, r in measured.items()
              if t2 != ticker and sic_major_group(r.get("sic")) == major]
        if len(maj) >= min_peers:
            return "sic_major", major, maj
    return "whole", None, [r for t2, r in measured.items() if t2 != ticker]


def peer_standings(book: dict, min_peers: int = 5) -> dict:
    """Whole-universe AND sector-relative standing for every measured
    name in `book`.

    Sector is tried at increasing width — exact SIC, then its 2-digit
    major group, then the whole book — stopping at the first level with
    enough other measured names to say anything (see _sector_pool()). A
    name whose exact industry is well covered gets a genuinely tight
    comparison; a rare one gets a wider net instead of no comparison at
    all. `sector_fallback` tells the reader which happened.

    Leverage and volatility percentiles are computed within THIS SAME
    pool, not a separately-chosen one — so a reader is never shown "safer
    than 60% of its sector on distance" beside "safer than 40% of the
    whole market on leverage" with no way to tell they describe different
    populations.

    Pure — no I/O, no network. `book` is any {ticker: report} mapping
    with `dd`, `sic`, `market_leverage`, `asset_vol` on each entry; a
    caller assembles that from wherever its own reports live.
    """
    measured = {t: r for t, r in (book or {}).items()
               if isinstance(r, dict) and r.get("dd") is not None}
    out = {}
    for t, r in measured.items():
        whole = [r2["dd"] for t2, r2 in measured.items() if t2 != t]
        level, key, mates = _sector_pool(measured, t, r.get("sic"), min_peers)
        sector_dds = [m["dd"] for m in mates]
        lev_pool = [m.get("market_leverage") for m in mates]
        vol_pool = [m.get("asset_vol") for m in mates]
        out[t] = {
            "peers_n": len(whole),
            "percentile": percentile(r["dd"], whole),
            "sector": key,
            "sector_level": level,
            "sector_fallback": level == "whole",
            "sector_peers_n": len(sector_dds),
            "sector_percentile": (percentile(r["dd"], sector_dds)
                                  if level != "whole" else None),
            "leverage_percentile": safety_percentile(
                r.get("market_leverage"), lev_pool, lower_is_safer=True),
            "volatility_percentile": safety_percentile(
                r.get("asset_vol"), vol_pool, lower_is_safer=True),
        }
    return out


def filter_sector(book: dict, sic, level: str = "sic_major") -> dict:
    """The subset of `book` sharing `sic`'s value at the given level.

    `level` must be the SAME "sector_level" peer_standings() returned for
    this ticker ("sic" for the exact code, "sic_major" for its 2-digit
    group) — passed explicitly rather than re-decided here, so "nearest
    companies measured" and the percentile prose can never disagree
    about which pool they are describing.
    """
    if level == "sic":
        key = str(sic or "").strip() or None
        if not key:
            return {}
        return {t: r for t, r in (book or {}).items()
               if isinstance(r, dict) and str(r.get("sic") or "").strip() == key}
    major = sic_major_group(sic)
    if not major:
        return {}
    return {t: r for t, r in (book or {}).items()
           if isinstance(r, dict) and sic_major_group(r.get("sic")) == major}


# --------------------- reading a filed balance sheet ---------------------
# Filers do not all tag the same way. Carnival reports no `Liabilities`
# line at all, so the total has to come from the accounting identity —
# assets equal liabilities plus equity — rather than being given up as
# unavailable. Ford tags it directly. Both must work, and a company that
# supports neither route has to be refused rather than approximated.
_FORMS = ("10-K", "10-Q", "20-F", "40-F")


# The market capitalisation is in dollars, so every figure it is measured
# against has to be. XBRL keys each tag's values by unit, and merging
# those units picks whichever currency happens to carry the later date:
# Enbridge files in CAD, and Toyota's yen rows outrank its dollar ones, so
# a 31.4 trillion JPY liability was being weighed against a USD market
# value. Only USD is read, and a filer who reports in anything else is
# refused rather than silently converted at an implied rate of 1.
UNIT = "USD"

# A balance sheet this old does not describe the company whose shares are
# being priced today. Same reasoning as the share count, and the same
# consequence for getting it wrong: T-Mobile last tagged `Liabilities` in
# 2013, and reading that against a 2026 market capitalisation put its
# debts at 9% of the business instead of 27%.
FILING_MAX_AGE_DAYS = 430


def _latest(facts: dict, tag: str, today: str | None = None,
            tax: str = "us-gaap") -> dict | None:
    """Most recent USD value for a tag, from a companyfacts dict.

    `tax` selects the taxonomy: "us-gaap" for domestic filers, "ifrs-full"
    for foreign private issuers (20-F) who report under IFRS instead —
    same shape, different element names for some concepts.
    """
    node = (facts.get("facts", {}).get(tax, {}) or {}).get(tag)
    if not node:
        return None
    today = today or date.today().isoformat()
    best = None
    for unit, rows in ((node.get("units") or {}).items()):
        if unit != UNIT:
            continue                      # see UNIT above
        for r in rows:
            if r.get("form") in _FORMS and r.get("val") is not None and r.get("end"):
                age = _days_old(r["end"], today)
                if age is not None and age > FILING_MAX_AGE_DAYS:
                    continue
                if best is None or r["end"] > best["end"]:
                    best = r
    return best


def _non_usd_currency(facts: dict, tag: str, tax: str) -> str | None:
    """If this tag has recent rows but none in USD, name the currency they
    ARE in. Used only to make a refusal specific ("files in GBP") instead
    of indistinguishable from data that was never filed at all."""
    node = (facts.get("facts", {}).get(tax, {}) or {}).get(tag)
    if not node:
        return None
    units = (node.get("units") or {})
    if any(units.get(UNIT) or []):
        return None
    for unit, rows in units.items():
        if unit != UNIT and rows:
            return unit
    return None


# IFRS Foundation taxonomy element names for the same five concepts
# us-gaap covers above. Borrowed from edgartools' (github.com/dgunning/
# edgartools, MIT) documented IFRS tag mapping — that package was not
# adopted as a dependency (too heavy for this instance's 512MB / short
# requirements.txt), but its knowledge of which ifrs-full concepts line
# up with which us-gaap ones is reused here, credited rather than
# rediscovered. "Liabilities" (the total) is spelled the same in both
# taxonomies; the others differ.
IFRS_BALANCE_TAGS = ("CurrentLiabilities", "NoncurrentLiabilities",
                     "Liabilities", "Equity", "EquityAndLiabilities")


def _balance_sheet_route(facts: dict, today: str | None, tax: str) -> dict | None:
    """One taxonomy's attempt at current+total liabilities, same-period-end
    discipline as the us-gaap route. Returns None if nothing lines up."""
    cur_tag = "LiabilitiesCurrent" if tax == "us-gaap" else "CurrentLiabilities"
    tot_tag = "Liabilities"   # spelled the same in both taxonomies
    eq_tags = (("StockholdersEquity",
               "StockholdersEquityIncludingPortionAttributableTo"
               "NoncontrollingInterest") if tax == "us-gaap" else ("Equity",))
    total_tag = ("LiabilitiesAndStockholdersEquity" if tax == "us-gaap"
                else "EquityAndLiabilities")

    # us-gaap keeps its original bare source strings unchanged (pinned
    # elsewhere, and the common case should not carry a taxonomy label
    # nobody reading a US filer's report needs to see); ifrs-full's are
    # prefixed since "Read as" on the page is the only place a reader
    # can tell which taxonomy answered.
    prefix = "" if tax == "us-gaap" else f"{tax}: "

    cur = _latest(facts, cur_tag, today, tax)
    tot = _latest(facts, tot_tag, today, tax)
    if cur and tot and cur["end"] == tot["end"]:
        return {"current_liabilities": float(cur["val"]),
                "total_liabilities": float(tot["val"]),
                "as_of": cur["end"], "source": f"{prefix}Liabilities",
                "taxonomy": tax}

    if tax != "us-gaap":
        # IFRS filers often tag the two halves but not a combined total —
        # the sum is still a same-period-end figure, not a guess.
        noncur = _latest(facts, "NoncurrentLiabilities", today, tax)
        if cur and noncur and cur["end"] == noncur["end"]:
            return {"current_liabilities": float(cur["val"]),
                    "total_liabilities": float(cur["val"]) + float(noncur["val"]),
                    "as_of": cur["end"],
                    "source": f"{prefix}current + noncurrent",
                    "taxonomy": tax}

    # assets = liabilities + equity, so liabilities = assets - equity
    lse = _latest(facts, total_tag, today, tax)
    eq = None
    for t in eq_tags:
        eq = _latest(facts, t, today, tax)
        if eq:
            break
    if lse and eq and lse["end"] == eq["end"]:
        total = float(lse["val"]) - float(eq["val"])
        if total > 0 and cur and cur["end"] == lse["end"]:
            return {"current_liabilities": float(cur["val"]),
                    "total_liabilities": total, "as_of": lse["end"],
                    "source": f"{prefix}assets minus equity",
                    "taxonomy": tax}
    return None


def balance_sheet(facts: dict, today: str | None = None) -> dict:
    """Current and total liabilities, as of ONE date, in ONE currency.

    Both halves must come from the same period end. They did not before,
    and the consequences were not subtle: T-Mobile's current liabilities
    from 2026-06-30 were combined with a total from 2013-03-31, and the
    report described a company carrying 9% market leverage when the
    contemporaneous figure is 27%. Hewlett Packard Enterprise moved a
    whole band, from "watch it" to "comfortable", the same way.

    So a mismatched pair is not patched up — the direct route is dropped
    and the accounting identity is tried instead, and if that cannot be
    formed at a single date either, the report refuses.

    Tries us-gaap first, then ifrs-full (a foreign private issuer filing
    a 20-F) — us-gaap wins when a filer tags both, so a dual-tagger does
    not flip which route answers between runs. Only USD is read from
    either taxonomy (see UNIT above); a filer whose recent tags exist
    only in another currency is refused with that currency named, via
    `currency_only`, rather than converted at a guessed rate.
    """
    out = {"current_liabilities": None, "total_liabilities": None,
           "as_of": None, "source": None, "taxonomy": None}
    for tax in ("us-gaap", "ifrs-full"):
        route = _balance_sheet_route(facts, today, tax)
        if route:
            out.update(route)
            return out

    # Nothing usable in either taxonomy. Report what the us-gaap route
    # found for disclosure (unchanged from before IFRS support), and
    # separately check whether either taxonomy's total-liabilities tag
    # exists but only in a foreign currency — that is a different fact
    # from "never filed" and deserves a different refusal.
    cur = _latest(facts, "LiabilitiesCurrent", today)
    tot = _latest(facts, "Liabilities", today)
    if cur:
        out["current_liabilities"] = float(cur["val"])
        out["as_of"] = cur["end"]
    if tot:
        out["total_as_of"] = tot["end"]
        out["total_unusable"] = float(tot["val"])
        out["mismatched"] = bool(cur and cur["end"] != tot["end"])

    for tax, tag in (("ifrs-full", "Liabilities"), ("us-gaap", "Liabilities")):
        ccy = _non_usd_currency(facts, tag, tax)
        if ccy:
            out["currency_only"] = ccy
            break
    return out


# --------------------- fetching, with the IO injected ---------------------
# The whole-filer endpoint (companyfacts) is 3.7MB for Apple. The
# per-concept endpoint is 19KB, so four small calls beat one large one by
# a factor of fifty, and the four are the only tags this model needs.
#
# The fetcher is injected rather than imported so the assembly logic can
# be tested without a network: every failure mode below — a missing tag, a
# refusing endpoint, mismatched period ends — is reachable offline.
# Shares outstanding lives in the `dei` taxonomy, not `us-gaap`. With it,
# market capitalisation is shares x the last close and the report works for
# any US filer — without it the model only covers whatever happens to be on
# today's board, which is not a product.
#
# Two tags, in this order, because neither is reliable alone:
#
#   dei:EntityCommonStockSharesOutstanding is the 10-K/10-Q cover page
#   count — the closest thing to "shares outstanding today". Coca-Cola
#   reports it currently (4.30bn, 2026-04-28). Ford STOPPED reporting it
#   in 2011 and the endpoint still cheerfully returns that number.
#
#   us-gaap:WeightedAverageNumberOfSharesOutstandingBasic is a period
#   average rather than a point-in-time count, so it lags a buyback by a
#   quarter, but Ford tags it currently (3.99bn, 2026-03-31) and it is
#   within 1% of the cover-page count wherever both exist.
#
# CommonStockSharesIssued is deliberately NOT here: it counts treasury
# shares. Coca-Cola issued 7.04bn against 4.30bn outstanding, so using it
# would overstate market capitalisation by 64% and quietly move every
# company that tags it into a safer band than it belongs in.
SHARES_TAGS = (("dei", "EntityCommonStockSharesOutstanding"),
               ("us-gaap", "WeightedAverageNumberOfSharesOutstandingBasic"))

# A share count is an input to today's market capitalisation, so an old
# one is not a weaker answer — it is a wrong one. Ford's stale tag would
# have priced the company off its 2011 share register. Annual filers
# report once a year and file up to ~90 days after year end, so anything
# inside ~14 months is a live number and anything beyond it is refused.
SHARES_MAX_AGE_DAYS = 430

BALANCE_TAGS = ("LiabilitiesCurrent", "Liabilities",
                "LiabilitiesAndStockholdersEquity", "StockholdersEquity",
                "StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest")


def fetch_balance_sheet(cik: int, get_json) -> dict:
    """Assemble the minimum balance sheet from SEC XBRL.

    get_json(url) -> dict, or raises.

    Two endpoints, cheap one first. companyconcept is 19KB per tag against
    3.7MB for the whole filer, so four small calls normally beat one large
    one by a factor of fifty. But they are not equivalent: for Ford,
    companyconcept returns ZERO rows for `Liabilities` while companyfacts
    returns 139 of them, most recent 2026-03-31. The cheap endpoint is
    silently incomplete for some filers, and trusting it alone meant those
    companies reported "cannot assess" while the data sat in the other
    endpoint.

    So the cheap path is an optimisation, not the source of truth: if it
    fails to produce a usable balance sheet, the ifrs-full concepts are
    tried the same cheap way (five more small calls — still far under the
    3.7MB whole-filer endpoint), then the authoritative endpoint before
    giving up. A tag that no route can supply is absent, and
    balance_sheet() refuses rather than guessing.
    """
    facts: dict = {"facts": {"us-gaap": {}}}
    fetched, failed = [], []
    for tag in BALANCE_TAGS:
        url = (f"https://data.sec.gov/api/xbrl/companyconcept/"
               f"CIK{int(cik):010d}/us-gaap/{tag}.json")
        try:
            d = get_json(url)
        except Exception:
            failed.append(tag)
            continue
        units = (d or {}).get("units") or {}
        if any(rows for rows in units.values()):
            facts["facts"]["us-gaap"][tag] = {"units": units}
            fetched.append(tag)
        else:
            failed.append(tag)          # present but empty — see above
    out = balance_sheet(facts)
    out["tags_fetched"] = fetched
    out["tags_failed"] = failed
    out["source_endpoint"] = "companyconcept"
    # "both halves present" is not the bar — they have to describe the
    # same balance sheet. A mismatched pair from the cheap endpoint is a
    # reason to pay for the full one, which may carry a matching date.
    if (out["total_liabilities"] is not None
            and out["current_liabilities"] is not None
            and out.get("source")):
        return out

    # us-gaap came up empty — try the ifrs-full concepts the same cheap
    # way before paying for companyfacts. A domestic filer simply gets
    # 404s here and this costs nothing but five quick misses.
    ifrs_fetched, ifrs_failed = [], []
    facts["facts"]["ifrs-full"] = {}
    for tag in IFRS_BALANCE_TAGS:
        url = (f"https://data.sec.gov/api/xbrl/companyconcept/"
               f"CIK{int(cik):010d}/ifrs-full/{tag}.json")
        try:
            d = get_json(url)
        except Exception:
            ifrs_failed.append(tag)
            continue
        units = (d or {}).get("units") or {}
        if any(rows for rows in units.values()):
            facts["facts"]["ifrs-full"][tag] = {"units": units}
            ifrs_fetched.append(tag)
        else:
            ifrs_failed.append(tag)
    if ifrs_fetched:
        out = balance_sheet(facts)
        out["tags_fetched"] = fetched + [f"ifrs-full:{t}" for t in ifrs_fetched]
        out["tags_failed"] = failed + [f"ifrs-full:{t}" for t in ifrs_failed]
        out["source_endpoint"] = "companyconcept"
        if (out["total_liabilities"] is not None
                and out["current_liabilities"] is not None
                and out.get("source")):
            return out

    try:
        cf = get_json(f"https://data.sec.gov/api/xbrl/companyfacts/"
                      f"CIK{int(cik):010d}.json")
    except Exception:
        return out
    full = balance_sheet(cf if isinstance(cf, dict) else {})
    full["tags_fetched"] = fetched + [f"ifrs-full:{t}" for t in ifrs_fetched]
    full["tags_failed"] = failed + [f"ifrs-full:{t}" for t in ifrs_failed]
    full["source_endpoint"] = "companyfacts"
    return full


def _newest(rows) -> dict | None:
    best = None
    for r in rows or []:
        if r.get("val") and r.get("end"):
            if best is None or r["end"] > best["end"]:
                best = r
    return best


def _days_old(end: str, today: str) -> int | None:
    """Age in days of an XBRL period end, both as YYYY-MM-DD."""
    try:
        a = date(*(int(p) for p in end.split("-")))
        b = date(*(int(p) for p in today.split("-")))
    except Exception:
        return None
    return (b - a).days


def shares_outstanding(cik: int, get_json, today: str | None = None) -> dict:
    """Current share count, or an explicit refusal.

    Returns {"shares", "as_of", "tag", "stale_as_of"}. `shares` is None
    unless a count was found AND it is recent enough to describe the
    company as it trades now; when a count was found but is too old,
    `stale_as_of` carries its date so the caller can say why it refused
    rather than reporting a bare absence.

    Both tags are tried on the cheap per-concept endpoint first and the
    whole-filer endpoint second, for the same reason the balance sheet
    does it: companyconcept returns ZERO rows for Coca-Cola's cover-page
    share count while companyfacts returns the current one.
    """
    today = today or date.today().isoformat()
    out = {"shares": None, "as_of": None, "tag": None, "stale_as_of": None}
    facts = None

    for tax, tag in SHARES_TAGS:
        best = None
        try:
            d = get_json(f"https://data.sec.gov/api/xbrl/companyconcept/"
                         f"CIK{int(cik):010d}/{tax}/{tag}.json")
            for rows in ((d or {}).get("units") or {}).values():
                cand = _newest(rows)
                if cand and (best is None or cand["end"] > best["end"]):
                    best = cand
        except Exception:
            pass

        if best is None:
            if facts is None:
                try:
                    facts = get_json(f"https://data.sec.gov/api/xbrl/companyfacts/"
                                     f"CIK{int(cik):010d}.json") or {}
                except Exception:
                    facts = {}
            node = ((facts.get("facts") or {}).get(tax) or {}).get(tag) or {}
            for rows in (node.get("units") or {}).values():
                cand = _newest(rows)
                if cand and (best is None or cand["end"] > best["end"]):
                    best = cand

        if best is None:
            continue
        age = _days_old(best["end"], today)
        if age is not None and age > SHARES_MAX_AGE_DAYS:
            # remember the first stale hit so the refusal can name a date,
            # but keep looking: Ford's cover-page tag froze in 2011 while
            # its weighted-average tag is current.
            out["stale_as_of"] = out["stale_as_of"] or best["end"]
            continue
        return {"shares": float(best["val"]), "as_of": best["end"],
                "tag": f"{tax}:{tag}", "stale_as_of": None}
    return out


def _add_days(iso_date: str, days: int) -> str:
    d = date(*(int(p) for p in iso_date.split("-"))) + timedelta(days=days)
    return d.isoformat()


def _add_business_days(iso_date: str, days: int) -> str:
    d = date(*(int(p) for p in iso_date.split("-")))
    added = 0
    while added < days:
        d += timedelta(days=1)
        if d.weekday() < 5:
            added += 1
    return d.isoformat()


# Exchange-initiated removal (17 CFR 240.12d2-2): the exchange, or the
# issuer on the exchange's behalf, notifies the SEC the listing is ending.
DELISTING_FORMS = {"25", "25-NSE"}
# Issuer-initiated deregistration: the company itself asks the SEC to end
# its reporting obligations, usually because it went private or merged.
DEREGISTRATION_FORMS = {"15", "15-12G", "15-15D"}


def delisting_filing(cik: int, get_json, today: str | None = None) -> dict | None:
    """The most recent EDGAR evidence that this filer's PRIMARY listing ended.

    get_json(url) -> dict, or raises. Returns None when there is no
    delisting-family filing on record, which for the overwhelming
    majority of companies is the right answer, not a gap.

    A Form 25/25-NSE or Form 15 family filing is real evidence, but on
    its own it claims more than it can support: American Electric Power
    (CIK 4904) has three of them on file — 2020, 2022, 2023 — and is a
    still-trading utility that filed a 10-K in 2026. A company can
    delist ONE class of security (a bond series, a warrant, a preferred
    share) while its common stock keeps trading, and the submissions
    endpoint names the FORM, not which security it covered. Treating
    presence alone as "this company delisted" would have put a real,
    currently-traded utility on the list.

    So this also checks for a 10-K or 10-Q filed after the newest
    delisting-family filing. A filer that keeps its periodic reports
    coming is still a going concern under SEC reporting, whatever it
    delisted — that is what separates AEP's history (three Form
    25-NSEs, and 10-Qs regardless) from Twitter's (a Form 25-NSE, a
    Form 15-12G nine days later, and no 10-K or 10-Q since).

    A first version of this stopped there and misread Electronic Arts,
    Brown-Forman and Philip Morris — three of the most ordinary
    still-trading blue chips there are — as delisted, because each had
    filed a Form 25-NSE only weeks earlier and no 10-Q had had TIME to
    follow yet. Absence of a filing that has not had a chance to exist
    is not evidence of anything; it is the clock, not the company. So
    `likely_primary_delisting` is False when a periodic report follows,
    None when under a year has passed since the filing and none has
    followed YET, and only True when a full year of silence has
    actually elapsed.
    """
    d = get_json(f"https://data.sec.gov/submissions/CIK{int(cik):010d}.json")
    recent = ((d or {}).get("filings") or {}).get("recent") or {}
    forms = recent.get("form") or []
    dates = recent.get("filingDate") or []
    relevant = DELISTING_FORMS | DEREGISTRATION_FORMS
    hits = sorted(((f, dt) for f, dt in zip(forms, dates) if f in relevant),
                 key=lambda h: h[1])
    if not hits:
        return None
    form, filed = hits[-1]
    effective = _add_business_days(filed, 10) if form in DELISTING_FORMS else filed
    today = today or date.today().isoformat()
    filed_since = any(f in ("10-K", "10-Q") and dt > filed
                      for f, dt in zip(forms, dates))
    if filed_since:
        likely = False
    elif today < _add_days(filed, 365):
        likely = None      # not enough time has passed to say either way
    else:
        likely = True
    return {"form": form, "filed": filed, "effective": effective,
           "likely_primary_delisting": likely}
