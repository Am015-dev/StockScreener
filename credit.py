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


def report(ticker: str, equity: float | None, closes,
           current_liabilities: float | None, total_liabilities: float | None,
           rf: float = 0.0375, as_of: str | None = None,
           vol: float | None = None, vol_obs: int | None = None) -> dict:
    """A full assessment, or an explicit refusal naming what was missing.

    `vol` lets the caller supply a volatility measured over more history
    than `closes` contains. The scan publishes one per ticker computed
    from years of returns; `closes` is a 60-day window kept small enough
    to download. When a measured volatility is passed it is used and
    `closes` only has to be long enough to price the shares.
    """
    out: dict = {"ticker": (ticker or "").upper(), "as_of": as_of,
                 "missing": [], "dd": None, "band": None}
    if not equity or equity <= 0:
        out["missing"].append("market capitalisation")
    supplied = vol is not None and vol > 0
    if not supplied:
        vol = equity_volatility(closes)
        vol_obs = None
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
    out["verdict"] = (
        f"{out['dd']} standard deviations from its default point — "
        f"{out['band']}." if dd is not None else "Cannot assess.")
    return out


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
    out = []
    for c in closes:
        try:
            price = float(c)
        except (TypeError, ValueError):
            continue
        if price <= 0:
            continue
        solved = solve_merton(shares * price, default_point, vol, rf)
        if solved is None:
            continue
        d = distance_to_default(solved[0], solved[1], default_point, rf)
        if d is not None:
            out.append(round(d, 3))
    return out or None


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
    return dict(rep, dd=dd, band=band(dd), equity=equity,
                asset_value=solved[0], asset_vol=round(solved[1], 4),
                market_leverage=round(dp / equity, 4),
                verdict=f"{dd} standard deviations from its default point "
                        f"— {band(dd)}.",
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


def _latest(facts: dict, tag: str, today: str | None = None) -> dict | None:
    """Most recent USD value for a us-gaap tag, from a companyfacts dict."""
    node = (facts.get("facts", {}).get("us-gaap", {}) or {}).get(tag)
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
    """
    out = {"current_liabilities": None, "total_liabilities": None,
           "as_of": None, "source": None}
    cur = _latest(facts, "LiabilitiesCurrent", today)
    tot = _latest(facts, "Liabilities", today)

    if cur and tot and cur["end"] == tot["end"]:
        out.update(current_liabilities=float(cur["val"]),
                   total_liabilities=float(tot["val"]),
                   as_of=cur["end"], source="Liabilities")
        return out

    # assets = liabilities + equity, so liabilities = assets - equity
    lse = _latest(facts, "LiabilitiesAndStockholdersEquity", today)
    eq = (_latest(facts, "StockholdersEquity", today)
          or _latest(facts,
                     "StockholdersEquityIncludingPortionAttributableTo"
                     "NoncontrollingInterest", today))
    if lse and eq and lse["end"] == eq["end"]:
        total = float(lse["val"]) - float(eq["val"])
        if total > 0 and cur and cur["end"] == lse["end"]:
            out.update(current_liabilities=float(cur["val"]),
                       total_liabilities=total, as_of=lse["end"],
                       source="assets minus equity")
            return out

    # Nothing lines up. Whatever was found is reported for disclosure, but
    # a total from a different period end is NOT returned as the total —
    # leaving it in the field the model reads is exactly how the two got
    # combined in the first place.
    if cur:
        out["current_liabilities"] = float(cur["val"])
        out["as_of"] = cur["end"]
    if tot:
        out["total_as_of"] = tot["end"]
        out["total_unusable"] = float(tot["val"])
        out["mismatched"] = bool(cur and cur["end"] != tot["end"])
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
    # "both halves present" is not the bar — they have to describe the
    # same balance sheet. A mismatched pair from the cheap endpoint is a
    # reason to pay for the full one, which may carry a matching date.
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
