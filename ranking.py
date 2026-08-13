"""Which five names, and why — the decision the tool never made.

The site could tell you a company's distance to default, and separately
how violently its share moves, and separately when it reports. Three
fragments on three pages and no answer. This module makes the answer.

What it is allowed to rank on is fixed by what has actually been
measured in this repository, and two results do the fixing:

  - The pullback signal this project was built around lost to coin-flip
    entry through byte-identical exit code, twice. It is not an input.
  - Eleven price shapes looked tradeable over a year until the
    comparison group was drawn from the same volatility bucket on the
    same day. They were detecting that a 3% move selects violent stocks.

So nothing here forecasts direction. It ranks on survivability, on how
much of the typical move the spread eats, on whether a name adds
anything to what the reader already owns, and — only when a shape has
earned it by holding up on data it was not chosen on — on a measured
edge. That last component sits at zero until then, visibly, rather than
being quietly redistributed into the others.

That is not a lesser product. Position size and loss control are what
separate an account that compounds from one that does not, and neither
of them needs to know where a stock is going.
"""
from __future__ import annotations

import math

# ---- the hard filters. A name failing one is excluded, never scored ----
# Liquidity is the constraint that matters — you have to be able to get
# out. Real 30-day average dollar volume is published (liquidity.json,
# built from the same OHLCV the scan already downloads) and is gated on
# when a name has it. $50M/day is not invented for this page: it is the
# floor the loosest published preset (wide-net) already enforces, so
# /today never ranks a name the site's own screener would call illiquid.
# A name liquidity.json has no figure for (FX could not be established,
# or it never traded in the scanned window) falls back to the market-value
# floor below — flagged as a proxy, never treated as the same measurement.
MIN_DOLLAR_VOLUME = 50e6
MIN_MARKET_VALUE = 2e9
MIN_PRICE = 5.0            # below this the spread is a real share of the move
MAX_ANNUAL_VOL = 0.90      # above this a sane stop leaves a pointless position
MIN_DD = 2.0               # distance to default: below this the balance sheet
                           # is the risk and no chart matters
EARNINGS_BUFFER = 2        # sessions of clearance either side of the horizon

# The typical move has to be worth catching. This started life as a
# scoring component and was wrong there: rewarding a LARGE move against
# costs while also rewarding LOW volatility is rewarding two opposite
# things, and on real data the two nearly cancelled — a name scoring
# 18/20 for being calm and 2/20 for barely moving came out ranked first
# on the strength of neither. It is a threshold, not a preference. Below
# it the trade is a way to pay a broker; above it, being calmer is
# simply better, and the score can say so without contradicting itself.
MIN_MOVE_OVER_COST = 8.0

TRADING_DAYS = 252.0
DEFAULT_HORIZON = 10

# what a round trip costs, as a share of the position
ROUND_TRIP_COST_PCT = 0.20


def _pctile(values: dict) -> dict:
    """{key: 0..1} by rank. Ties share the average rank.

    Rank rather than raw value on purpose: these components are measured
    in different units — standard deviations, percent, correlation — and
    adding them raw would let whichever happens to have the widest
    numeric spread quietly dominate the score.
    """
    if not values:
        return {}
    items = sorted(values.items(), key=lambda kv: kv[1])
    n = len(items)
    if n == 1:
        return {items[0][0]: 1.0}
    out, i = {}, 0
    while i < n:
        j = i
        while j + 1 < n and items[j + 1][1] == items[i][1]:
            j += 1
        rank = (i + j) / 2.0
        for k in range(i, j + 1):
            out[items[k][0]] = rank / (n - 1)
        i = j + 1
    return out


def typical_move_pct(annual_vol: float | None,
                     horizon: int = DEFAULT_HORIZON) -> float | None:
    """One standard deviation of a `horizon`-session move, in percent.

    This is what a target would be, if this tool were willing to print a
    target. It is not, because a target implies a forecast that has been
    measured here and found absent — but the same arithmetic honestly
    labelled is what sizes the trade.
    """
    try:
        v = float(annual_vol)
    except (TypeError, ValueError):
        return None
    if v <= 0:
        return None
    return v * math.sqrt(horizon / TRADING_DAYS) * 100


def filters(c: dict, horizon: int = DEFAULT_HORIZON) -> tuple:
    """(passes, reason, flags) for one candidate.

    The reason is written to be read by a person, because it is
    published. A screen that shows only what passed is not evidence of
    anything, and the excluded list is where most of the information is.

    Earnings coverage is read per-candidate (`cal_covered`,
    `earnings_single_source`), not from a global flag — a global "the US
    calendar is complete" must never read as clearance for a name that
    calendar was never asked about.
    """
    flags = []
    px = c.get("price")
    mv = c.get("market_value")
    adv = c.get("adv_usd")
    vol = c.get("annual_vol")
    dd = c.get("dd")
    dte = c.get("days_to_earnings")

    if not px or px < MIN_PRICE:
        return False, f"trades at {px or 0:.2f}, under the {MIN_PRICE:.0f} floor " \
                      f"where the spread is a real share of the move", flags
    if adv is not None:
        # measured, not proxied — the case liquidity.json exists to reach
        if adv < MIN_DOLLAR_VOLUME:
            return False, f"trades about ${adv / 1e6:.0f}M a day — under the " \
                          f"${MIN_DOLLAR_VOLUME / 1e6:.0f}M floor, below which " \
                          f"getting out at size stops being reliable", flags
    elif not mv:
        return False, "how big the company is could not be established, so " \
                      "there is no way to tell whether it can be traded at " \
                      "size", flags
    elif mv < MIN_MARKET_VALUE:
        return False, f"worth about {mv / 1e9:.1f}B — under the " \
                      f"{MIN_MARKET_VALUE / 1e9:.0f}B floor, below which " \
                      f"getting out at size stops being reliable", flags
    else:
        # unmeasured must never pass as if it were measured — the market
        # value floor is a stand-in, and the reader is told so
        flags.append("liquidity proxied by market value — volume not "
                      "published for this name")
    if vol is None:
        return False, "how much it moves could not be measured, and that is " \
                      "what sets the stop and the share count", flags
    if vol > MAX_ANNUAL_VOL:
        return False, f"moves {vol * 100:.0f}% a year — a sane stop would be so " \
                      f"far away the position stops being worth holding", flags
    move = typical_move_pct(vol, horizon) or 0.0
    ratio = move / ROUND_TRIP_COST_PCT if move else 0.0
    if ratio < MIN_MOVE_OVER_COST:
        return False, f"usually moves only {move:.1f}% in {horizon} sessions — " \
                      f"about {ratio:.0f}x what the round trip costs, too " \
                      f"little of the move left over to be worth taking", flags
    if dte is not None and dte <= horizon + EARNINGS_BUFFER:
        return False, f"reports in {dte} days, inside the {horizon}-session " \
                      f"hold — a report gaps straight through a stop", flags
    # Absence from a COMPLETE calendar is the all-clear, not a gap — but
    # only for a name that calendar actually covers. The US bulk calendar
    # is built by walking every trading day in the next 45; a US name
    # that never appears in it has nothing scheduled. A EU name's absence
    # proves nothing (there is no EU bulk calendar), however complete the
    # US one is — treating cal_complete as a global all-clear here is
    # exactly the failure that put "no report due" on European names
    # nobody had actually checked.
    if dte is None and not c.get("cal_covered"):
        flags.append("earnings date unverified")
    elif dte is not None and c.get("earnings_single_source"):
        flags.append("earnings date from one source (Yahoo) — not cross-checked")

    if c.get("is_financial"):
        # the Merton model reads a bank's balance sheet as a company's and
        # gets it wrong; absence of a number here is not a red flag
        flags.append("credit not modelled for financials")
    elif dd is None:
        # never silently treated as safe
        flags.append("credit not measured")
    elif dd < MIN_DD:
        return False, f"sits {dd:.2f} standard deviations from its default " \
                      f"point — the balance sheet is the risk here, not the " \
                      f"chart", flags
    return True, "", flags


def score(candidates: list, holdings: list | None = None,
          patterns_report: dict | None = None,
          risk_budget: float = 100.0,
          horizon: int = DEFAULT_HORIZON,
          corr_by_ticker: dict | None = None) -> dict:
    """Rank what survives the filters. Pure — no I/O, no network.

    Returns every candidate, ranked, each carrying the arithmetic that
    put it where it is, plus everything excluded and the reason. The
    caller decides how many to show.
    """
    universe = len(candidates or [])
    passed, excluded = [], []
    for c in (candidates or []):
        ok, why, flags = filters(c, horizon)
        row = dict(c, flags=flags)
        if ok:
            passed.append(row)
        else:
            excluded.append({"ticker": c.get("ticker"), "why": why,
                             "name": c.get("name")})

    # ---- the two components that only score when they have been earned ----
    confirmed = _confirmed_shapes(patterns_report)
    active = bool(confirmed)

    # `corr_by_ticker` is None whenever no holdings were supplied — which,
    # today, is every request, because nothing upstream of this call ever
    # passes a portfolio in. It used to fall back to fit_v = 1.0 for
    # everyone in that case, which is a CONSTANT: identical for every row,
    # so it never changes the ranking, only inflates every score by the
    # same 20 points while a bar on the page implied it was discriminating
    # between names. That is the same shape of error the pattern component
    # already guards against — a score dressed as a measurement with
    # nothing behind it — so it gets the same treatment: zero, and
    # excluded from the total, until a real portfolio is wired in. An
    # EMPTY dict is different from None: it means holdings were supplied
    # and none of them correlate, which is a real (and good) measurement,
    # so only None disables the component.
    fit_active = corr_by_ticker is not None

    credit_v, vol_v, fit_v, edge_v = {}, {}, {}, {}
    for r in passed:
        t = r["ticker"]
        if r.get("dd") is not None:
            credit_v[t] = r["dd"]
        # Lower volatility ranks higher: the same money at risk buys a
        # bigger, and therefore more meaningful, position. This is only
        # safe to say because MIN_MOVE_OVER_COST has already thrown out
        # the names that are so quiet the move is not worth catching —
        # without that filter this preference would be pushing towards
        # trades that cannot pay for themselves.
        vol_v[t] = -float(r["annual_vol"])
        if fit_active:
            mx = corr_by_ticker.get(t)
            fit_v[t] = 1.0 - abs(mx) if mx is not None else 1.0
        edge_v[t] = _edge_for(r, confirmed) if active else 0.0

    credit_p, vol_p = _pctile(credit_v), _pctile(vol_v)
    fit_p = _pctile(fit_v) if fit_active else {}
    edge_p = _pctile(edge_v) if active and len(set(edge_v.values())) > 1 else {}

    for r in passed:
        t = r["ticker"]
        # a name with no credit measurement gets the middle of the range
        # rather than the top: unknown is not the same as excellent
        credit_pts = 30.0 * credit_p.get(t, 0.5)
        vol_pts = 30.0 * vol_p.get(t, 0.5)
        fit_pts = 20.0 * fit_p.get(t, 1.0) if fit_active else 0.0
        edge_pts = 20.0 * edge_p.get(t, 0.0) if active else 0.0
        move = typical_move_pct(r["annual_vol"], horizon) or 0.0
        r["components"] = {
            "credit headroom": round(credit_pts, 1),
            "calm enough to size up": round(vol_pts, 1),
            "adds to what you own": round(fit_pts, 1),
            "confirmed pattern": round(edge_pts, 1),
        }
        r["component_max"] = {"credit headroom": 30, "calm enough to size up": 30,
                              "adds to what you own": 20, "confirmed pattern": 20}
        r["score"] = round(credit_pts + vol_pts + fit_pts + edge_pts, 1)
        r["typical_move_pct"] = round(move, 1)
        r["move_over_cost"] = round(move / ROUND_TRIP_COST_PCT, 1) if move else None

    # ticker as the tie-break so the same inputs always give the same
    # order — a list that reshuffles on refresh cannot be acted on
    passed.sort(key=lambda r: (-r["score"], r["ticker"]))
    excluded.sort(key=lambda r: r["ticker"] or "")
    return {
        "ranked": passed,
        "excluded": excluded,
        "universe": universe,
        "passed_filters": len(passed),
        "pattern_component_active": active,
        "portfolio_component_active": fit_active,
        "confirmed_shapes": [c["pattern"] for c in confirmed],
        "horizon": horizon,
        "risk_budget": risk_budget,
        "max_points_available": 60.0 + (20.0 if active else 0.0)
                                       + (20.0 if fit_active else 0.0),
    }


def _confirmed_shapes(report: dict | None) -> list:
    """Shapes that held up on data they were not chosen on, and pay after costs.

    Anything less than that does not get to move a name up the list. A
    shape that survived the search and vanished out of sample is exactly
    the failure this whole apparatus exists to catch.
    """
    out = []
    for row in ((report or {}).get("tradeable") or []):
        if row.get("confirmed") and (row.get("after_costs_pct") or 0) > 0:
            out.append(row)
    return out


def _edge_for(row: dict, confirmed: list) -> float:
    """How much confirmed edge is firing on this name right now."""
    firing = set(row.get("patterns_today") or [])
    best = 0.0
    for c in confirmed:
        if c.get("pattern") in firing:
            hold = c.get("holdout") or {}
            best = max(best, float(hold.get("after_costs_pct")
                                   or c.get("after_costs_pct") or 0.0))
    return best
