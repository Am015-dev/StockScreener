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


def build_candidates(series: dict, vols: dict, creds: dict, liq: dict,
                     earn: dict, cal_complete: bool,
                     earn_single_source: set | None = None,
                     inv13f_tickers: dict | None = None,
                     region_of=None) -> list[dict]:
    """Every liquid name with enough history to be ranked, from the books
    already measured — no network call.

    Pure and callable from two places on purpose: the live app builds
    this from its own in-memory caches on every request, and the
    scheduled scan builds it from the exact same books it just measured
    (no re-fetch, no re-publish) so it can work out what today's five
    would actually be and record that for tomorrow's cooldown check —
    see `select_daily_five()`. Moved out of app.py's `_today_candidates`
    for that second caller; app.py now wraps this with its own caches.
    """
    earn_single_source = earn_single_source or set()
    inv13f_tickers = inv13f_tickers or {}
    region_of = region_of or (lambda t: "US")
    out = []
    for t, closes in series.items():
        c = [x for x in (closes or []) if x]
        if len(c) < 20:
            continue
        rep = creds.get(t) or {}
        px = c[-1]
        # `equity` in a credit report is shares x price — the company's
        # market value. Kept as the fallback liquidity proxy for names
        # liquidity.json has no figure for (FX could not be established,
        # or the name never traded in the window) — filters() flags that
        # fallback, never treats it as a measurement of the same thing.
        mv = rep.get("equity")
        # vol.json holds {vol, obs, as_of} per name, not a bare float —
        # and a thin estimate is worse than none, because it sets both
        # the stop and the share count
        v = vols.get(t) or {}
        av = v.get("vol") if isinstance(v, dict) else v
        if isinstance(v, dict) and (v.get("obs") or 0) < 60:
            av = None
        adv = (liq.get(t) or {}).get("adv_usd")
        out.append({
            "ticker": t,
            "name": rep.get("name") or t,
            "price": px,
            "market_value": mv if (mv and mv > 0) else None,
            "adv_usd": adv if (adv and adv > 0) else None,
            "annual_vol": av,
            "dd": rep.get("dd"),
            "is_financial": bool(rep.get("missing") == "financial"
                                 or rep.get("not_modelled") == "financial"),
            "days_to_earnings": (earn or {}).get(t),
            # absence-from-calendar is only the all-clear for a name the
            # (complete) US bulk calendar actually covers — an EU name's
            # absence proves nothing, however complete that calendar is
            "cal_covered": bool(cal_complete) and region_of(t) == "US",
            "earnings_single_source": t in earn_single_source,
            "sector": rep.get("sector"),
            # Purely informational — never read by filters() or score().
            "held_by_investors": list((inv13f_tickers.get(t) or {}).get("holders") or []),
        })
    return out


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


# A name shown this recently is "cooling" — deprioritized behind fresh
# names unless its score has genuinely moved. Two of the four score
# components are structurally zero for an anonymous visitor (no
# holdings supplied, so "adds to what you own" never activates; nothing
# has yet survived this project's own pattern holdout, so "confirmed
# pattern" never has either — see /patterns) so ranking on the other
# two alone, both slow-moving fundamentals, mechanically floats the same
# handful of ultra-stable names to the top for weeks at a time. Five
# trading sessions is one trading week — long enough that a name is
# genuinely gone for a while, short enough that a real change in its
# credit or its volatility surfaces within the same week it happens.
COOLDOWN_SESSIONS = 5
# A move of at least this fraction of the day's active point scale
# waives the cooldown. 15% of a typical 60-point active scale is 9
# points — reachable only by a real shift in measured credit standing or
# volatility, not by the ordinary day-to-day wobble in either number.
COOLDOWN_MATERIAL_FRACTION = 0.15
# 5 trading sessions is at most ~9 calendar days (a week plus a holiday
# weekend on each side); 14 is a deliberately generous ceiling on top of
# that. Without it, a single stale entry left over from a gap in the
# published history (a scan outage, or history that has only just
# started being recorded) reads as "the most recent session" purely
# because nothing more recent exists to outrank it — caught by testing
# an isolated month-old entry, not by reasoning about it in advance.
COOLDOWN_MAX_CALENDAR_GAP_DAYS = 14


def select_daily_five(ranked: list[dict], history: dict | None, today: str,
                      max_points_available: float, n: int = 5,
                      cooldown_sessions: int = COOLDOWN_SESSIONS,
                      material_fraction: float = COOLDOWN_MATERIAL_FRACTION
                      ) -> list[dict]:
    """The top N, with a real cooldown against showing the same names
    every session.

    `history` is `{"history": [{"date": "YYYY-MM-DD",
    "picks": {ticker: {"score": ...}}}, ...]}` — the actual picks shown
    on each of the last several sessions, published by the scheduled
    scan (see `scripts/scheduled_scan.py`). A ticker that appears in the
    most recent `cooldown_sessions` distinct sessions before `today` is
    pushed behind every fresh name UNLESS its score has moved by at
    least `material_fraction` of `max_points_available` since the
    session it was last shown — a real, measured change, never a
    guess. If fewer than `n` names are fresh, the list is filled from
    the cooling names with the least stale first, and never comes up
    short: a real name shown with a note beats an incomplete list.

    With no history (a fresh deployment, or the file not yet
    published), every name is fresh and this returns exactly what
    `ranked[:n]` would — the cooldown can only ever change WHICH names
    fill the list, never fail to fill it.
    """
    if not today:
        return ranked[:n]
    try:
        import datetime as _dt
        today_d = _dt.date.fromisoformat(today)
    except (TypeError, ValueError):
        return ranked[:n]
    entries = sorted((history or {}).get("history") or [],
                     key=lambda e: e.get("date") or "")
    candidate_dates = sorted({e["date"] for e in entries
                              if e.get("date") and e["date"] < today},
                             reverse=True)
    recent_dates = []
    for d in candidate_dates:
        try:
            gap = (today_d - _dt.date.fromisoformat(d)).days
        except (TypeError, ValueError):
            continue
        if gap <= COOLDOWN_MAX_CALENDAR_GAP_DAYS:
            recent_dates.append(d)
        if len(recent_dates) == cooldown_sessions:
            break
    recent_set = set(recent_dates)
    if not recent_set:
        return ranked[:n]

    # oldest-first iteration means a later occurrence overwrites an
    # earlier one, so this ends up holding each ticker's score at its
    # MOST RECENT appearance in the cooldown window — the one that
    # matters for "has it changed since it was last shown"
    last_score: dict = {}
    for e in entries:
        if e.get("date") not in recent_set:
            continue
        for t, info in (e.get("picks") or {}).items():
            last_score[t] = (info or {}).get("score")

    threshold = material_fraction * (max_points_available or 0)
    eligible, cooling = [], []
    for row in ranked:
        t = row.get("ticker")
        if t in last_score:
            prev, cur = last_score[t], row.get("score")
            if prev is not None and cur is not None and abs(cur - prev) >= threshold:
                eligible.append(dict(row, cooldown_waived=True))
            else:
                cooling.append(row)
        else:
            eligible.append(row)

    picks = eligible[:n]
    if len(picks) < n:
        need = n - len(picks)
        picks += [dict(row, cooldown_backfill=True) for row in cooling[:need]]
    picks.sort(key=lambda r: -(r.get("score") or 0))
    return picks


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
