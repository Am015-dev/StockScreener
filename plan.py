"""One name, one executable plan: in, out, how much, and when to give up.

A shortlist without a plan attached is a way to lose money slowly. The
reader needs the share count before they can act, and the share count
comes from the stop, and the stop comes from how much the share actually
moves — so all three are computed together here, from measured
volatility, and none of them is a guess.

There is deliberately no price target. A target implies a forecast, and
the one signal this project did test against a null lost to a coin flip
twice. "This share usually travels this far in ten sessions" is the same
arithmetic without the false claim, and it is the number that decides
whether the trade is worth its costs at all.
"""
from __future__ import annotations

import analysis
import credit
import ranking


def trade_plan(row: dict, risk_budget: float = 100.0,
               horizon: int = ranking.DEFAULT_HORIZON,
               currency: str = "$") -> dict:
    """The whole trade, from a scored row. Reuses analysis.risk_frame."""
    px = row.get("price")
    vol = row.get("annual_vol")
    rf = analysis.risk_frame(px, vol, risk_budget)
    if not rf:
        return {"ticker": row.get("ticker"), "usable": False,
                "why": "how much this share moves could not be measured, so "
                       "no stop and no share count can be worked out"}

    move = ranking.typical_move_pct(vol, horizon) or 0.0
    cost_share = (ranking.ROUND_TRIP_COST_PCT / move * 100) if move else None

    # Every trade_plan() field below used to be ONE fixed sentence with only
    # the numbers swapped in — literally the same wording for every pick,
    # every day. These branches say something different because something
    # about the NUMBERS is actually different, never because a die was
    # rolled: a calm name and a volatile one earn different stop sentences
    # because their measured volatility genuinely differs, not for variety's
    # own sake.
    calm = vol is not None and vol <= credit.REFERENCE_VOL
    comp = row.get("components") or {}
    best = max(comp, key=lambda k: comp[k]) if comp else None
    dte = row.get("days_to_earnings")
    strong_edge = cost_share is not None and cost_share <= 20  # move >= 5x costs

    if calm:
        stop_text = (f"{currency}{rf['stop_price']:.2f} — {rf['stop_pct']}% "
                     f"below, tighter than most stops this screen sets. This "
                     f"is a genuinely calm share — it moves about "
                     f"{rf['daily_move_pct']}% on an average day — so a close "
                     f"stop is what the name actually does, not a guess.")
    else:
        stop_text = (f"{currency}{rf['stop_price']:.2f} — {rf['stop_pct']}% "
                     f"below. This share swings harder than most that clear "
                     f"here — about {rf['daily_move_pct']}% on an average day "
                     f"— so the stop has to sit back that far, or ordinary "
                     f"noise takes you out.")

    if strong_edge:
        typical_move_text = (f"Usually travels about ±{move:.1f}% over "
                             f"{horizon} sessions — {cost_share and 100/cost_share:.0f}x "
                             f"what a round trip costs, so the friction is a "
                             f"rounding error against the move you're playing for.")
    else:
        typical_move_text = (f"Usually travels about ±{move:.1f}% over "
                             f"{horizon} sessions. That is the size of the "
                             f"move you are playing for — not a forecast, "
                             f"the measured spread.")

    if dte is not None and 0 <= dte - horizon <= 5:
        time_stop_text = (f"Out after {horizon} sessions — and a report "
                          f"falls due only {dte - horizon} sessions after "
                          f"that, so this deadline is also what keeps you "
                          f"from holding into it.")
    else:
        time_stop_text = (f"Out after {horizon} sessions whether or not it "
                          f"has moved. A position with no deadline is how a "
                          f"trade becomes an investment by accident.")

    if best == "credit headroom":
        wrong_if = (f"It closes below {currency}{rf['stop_price']:.2f}. That "
                    f"is the whole thesis — the room on its debts does not "
                    f"change day to day, so a stop hit here means the market "
                    f"re-pricing something about it, not noise.")
    elif best == "calm enough to size up":
        wrong_if = (f"It closes below {currency}{rf['stop_price']:.2f}. On a "
                    f"name picked specifically for being quiet, a stop hit "
                    f"this clean is a real signal, not the noise a stop this "
                    f"size is built to absorb.")
    else:
        wrong_if = (f"It closes below {currency}{rf['stop_price']:.2f}. That is "
                    f"the whole thesis; there is no version of this where you "
                    f"hold it lower and wait.")

    return {
        "ticker": row.get("ticker"),
        "name": row.get("name"),
        "usable": True,
        "entry": rf["stop_price"] and round(float(px), 2),
        "entry_text": f"Buy at market, or a limit at {currency}{float(px):.2f}.",
        "stop": rf["stop_price"],
        "stop_pct": rf["stop_pct"],
        "stop_text": stop_text,
        "shares": rf["shares"],
        "position_value": rf["position_value"],
        "risk_budget": risk_budget,
        "size_text": (f"{rf['shares']} shares — about "
                      f"{currency}{rf['position_value']:,.0f} committed, with "
                      f"{currency}{risk_budget:,.0f} of it at risk if the stop "
                      f"is hit."
                      if rf["shares"] else
                      "the stop is wider than the whole risk budget, so this "
                      "does not size into a position worth taking"),
        "typical_move_pct": round(move, 1),
        "typical_move_text": typical_move_text,
        "cost_share_pct": round(cost_share, 1) if cost_share else None,
        "cost_text": (f"A round trip costs about "
                      f"{ranking.ROUND_TRIP_COST_PCT}%, which is "
                      f"{cost_share:.0f}% of that typical move."
                      if cost_share else None),
        "time_stop": horizon,
        "time_stop_text": time_stop_text,
        "days_to_earnings": row.get("days_to_earnings"),
        "earnings_text": (
            (f"Reports in {row['days_to_earnings']} sessions."
             + (" (from Yahoo, not cross-checked against a second source.)"
                if row.get("earnings_single_source") else ""))
            if row.get("days_to_earnings") is not None else
            "Nothing scheduled in the next 45 sessions — checked against the "
            "published calendar for every trading day in that window."
            if row.get("cal_covered") else
            "No report date confirmed — check before entering."),
        "wrong_if": wrong_if,
        "flags": row.get("flags") or [],
    }


def thesis(row: dict, horizon: int = ranking.DEFAULT_HORIZON,
           rank: int = 0) -> str:
    """One sentence on why this name is on the list, in the reader's words.

    Names the single thing that put it there, so a reader can disagree
    with the arithmetic rather than with a black box.

    `rank` is passed because superlatives have to be earned: the first
    draft told the reader that two different names each had "the most
    room on its debts of anything that cleared today", which is a plain
    falsehood generated by wording every card the same way.
    """
    t = row.get("ticker") or ""
    comp = row.get("components") or {}
    if not comp:
        return f"{t} cleared every filter."
    best = max(comp, key=lambda k: comp[k])
    move = row.get("typical_move_pct") or 0
    ratio = row.get("move_over_cost")
    dd = row.get("dd")
    top = rank == 0
    if best == "credit headroom" and dd is not None:
        lead = ("has the most room on its debts of anything that cleared today"
                if top else "has plenty of room on its debts")
        return (f"{t} {lead} — {dd:.1f} standard deviations from its default "
                f"point — and travels about {move:.0f}% in {horizon} sessions, "
                f"roughly {ratio:.0f} times what the round trip costs.")
    if best == "calm enough to size up":
        lead = ("is the calmest name that cleared today" if top else
                "is among the calmer names that cleared")
        return (f"{t} {lead}, so a sensible stop sits close and the same money "
                f"at risk buys a real position rather than a token one — while "
                f"still moving {move:.0f}% in {horizon} sessions.")
    if best == "adds to what you own":
        lead = ("moves least like what you already hold" if top else
                "moves unlike most of what you already hold")
        return (f"{t} {lead}, so it adds risk you are not already carrying "
                f"rather than doubling a bet you have made.")
    if best == "confirmed pattern":
        return (f"{t} is showing a shape that held up on data it was not "
                f"chosen on — the only forward-looking reason on this page.")
    return f"{t} cleared every filter and ranks {row.get('score', 0):.0f}."


def _unique_min(items: list[tuple[int, float]]) -> int | None:
    """Index holding the smallest value, or None if nothing clears or two
    picks tie for it — a tie is not a standout, it is a coincidence."""
    if not items:
        return None
    m = min(v for _, v in items)
    holders = [j for j, v in items if v == m]
    return holders[0] if len(holders) == 1 else None


def standout(picks: list[dict], i: int) -> str | None:
    """What is genuinely most true about this pick among today's five.

    `thesis()` already names the score component that put a pick on the
    list; this looks at three different, real, cross-pick facts —
    stop tightness, cost-to-move ratio, earnings timing — that thesis
    never touches, so the two never repeat each other. A pick that is
    not the extreme on any of the three gets nothing, on purpose: not
    every pick is standout at something, and inventing one would be
    exactly the kind of filler this line exists to replace.
    """
    if len(picks) < 2:
        return None
    this = picks[i]

    stops = [(j, p["stop_pct"]) for j, p in enumerate(picks)
             if p.get("stop_pct") is not None]
    if _unique_min(stops) == i:
        others = [v for j, v in stops if j != i]
        avg_other = sum(others) / len(others)
        return (f"Tightest stop of today's five — {this['stop_pct']}% "
                f"below, against {avg_other:.1f}% average for the rest.")

    costs = [(j, p["cost_share_pct"]) for j, p in enumerate(picks)
             if p.get("cost_share_pct") is not None]
    if _unique_min(costs) == i:
        return (f"Best edge of today's five — the round trip eats just "
                f"{this['cost_share_pct']:.0f}% of the move it's sized "
                f"for, the smallest of any pick today.")

    dtes = [(j, p["days_to_earnings"]) for j, p in enumerate(picks)
            if p.get("days_to_earnings") is not None]
    if len(dtes) == 1 and dtes[0][0] == i:
        return (f"The only one of today's five with a report on the "
                f"calendar — {this['days_to_earnings']} sessions out.")

    return None
