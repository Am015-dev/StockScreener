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
    return {
        "ticker": row.get("ticker"),
        "name": row.get("name"),
        "usable": True,
        "entry": rf["stop_price"] and round(float(px), 2),
        "entry_text": f"Buy at market, or a limit at {currency}{float(px):.2f}.",
        "stop": rf["stop_price"],
        "stop_pct": rf["stop_pct"],
        "stop_text": (f"{currency}{rf['stop_price']:.2f} — {rf['stop_pct']}% "
                      f"below. Anything closer and ordinary noise takes you "
                      f"out; this share moves about "
                      f"{rf['daily_move_pct']}% on an average day."),
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
        "typical_move_text": (f"Usually travels about ±{move:.1f}% over "
                              f"{horizon} sessions. That is the size of the "
                              f"move you are playing for — not a forecast, "
                              f"the measured spread."),
        "cost_share_pct": round(cost_share, 1) if cost_share else None,
        "cost_text": (f"A round trip costs about "
                      f"{ranking.ROUND_TRIP_COST_PCT}%, which is "
                      f"{cost_share:.0f}% of that typical move."
                      if cost_share else None),
        "time_stop": horizon,
        "time_stop_text": (f"Out after {horizon} sessions whether or not it "
                           f"has moved. A position with no deadline is how a "
                           f"trade becomes an investment by accident."),
        "days_to_earnings": row.get("days_to_earnings"),
        "earnings_text": (f"Reports in {row['days_to_earnings']} sessions."
                          if row.get("days_to_earnings") is not None else
                          "No report date confirmed — check before entering."),
        "wrong_if": (f"It closes below {currency}{rf['stop_price']:.2f}. That is "
                     f"the whole thesis; there is no version of this where you "
                     f"hold it lower and wait."),
        "flags": row.get("flags") or [],
    }


def thesis(row: dict, horizon: int = ranking.DEFAULT_HORIZON) -> str:
    """One sentence on why this name is on the list, in the reader's words.

    Names the single thing that put it there, so a reader can disagree
    with the arithmetic rather than with a black box.
    """
    t = row.get("ticker") or ""
    comp = row.get("components") or {}
    if not comp:
        return f"{t} cleared every filter."
    best = max(comp, key=lambda k: comp[k])
    move = row.get("typical_move_pct") or 0
    dd = row.get("dd")
    if best == "credit headroom" and dd is not None:
        return (f"{t} has the most room on its debts of anything that cleared "
                f"today — {dd:.1f} standard deviations from its default point "
                f"— and moves about {move:.0f}% in {horizon} sessions.")
    if best == "how much it moves":
        return (f"{t} is among the calmest names that cleared, so a sensible "
                f"stop is close and the same money at risk buys a real "
                f"position rather than a token one.")
    if best == "move against costs":
        return (f"{t} typically travels {move:.0f}% over {horizon} sessions "
                f"against a {ranking.ROUND_TRIP_COST_PCT}% round trip — the "
                f"move is worth far more than the cost of catching it.")
    if best == "adds to what you own":
        return (f"{t} moves least like what you already hold, so it adds risk "
                f"you are not already carrying rather than doubling a bet.")
    if best == "confirmed pattern":
        return (f"{t} is showing a shape that held up on data it was not "
                f"chosen on — the only forward-looking reason on this page.")
    return f"{t} cleared every filter and ranks {row.get('score', 0):.0f}/100."
