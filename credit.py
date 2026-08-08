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
from datetime import date
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
    # How much price history the volatility rests on. KMV uses a year of
    # daily returns; the published price book carries 60 closes, which is
    # about a quarter, and a quarter of returns estimates an annualised
    # volatility with roughly a +/-9% standard error at 22% vol. That
    # propagates straight into the distance, so the report states the
    # sample it used rather than presenting every figure as equally solid.
    out["vol_obs"] = len([1 for x in (closes or []) if x and float(x) > 0])
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
    fails to produce a usable balance sheet, the authoritative endpoint is
    fetched before giving up. A tag that neither route can supply is
    absent, and balance_sheet() refuses rather than guessing.
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
    if out["total_liabilities"] is not None and out["current_liabilities"] is not None:
        return out

    try:
        cf = get_json(f"https://data.sec.gov/api/xbrl/companyfacts/"
                      f"CIK{int(cik):010d}.json")
    except Exception:
        return out
    full = balance_sheet(cf if isinstance(cf, dict) else {})
    full["tags_fetched"] = fetched
    full["tags_failed"] = failed
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
