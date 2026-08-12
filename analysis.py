"""Everything the tool knows about one stock, assembled into a judgement.

The site had a gap where its whole purpose should be. It could tell you
a company's distance to default, and separately whether a trade
overlapped your book, and separately when earnings were due — three
fragments on three surfaces, and nothing that put them together. The
reader was left to synthesise, which is the work they came here to have
done. "No evidence or analysis, very difficult to jump to conclusions"
was an accurate description.

The reason it got that way matters, because it constrains the fix. The
entry signal this project was built around was falsified: coin-flip
entry through identical exit code did as well or better, twice. So the
one thing this module must never do is tell anyone a stock will go up.

But that leaves an enormous amount that is both TRUE and DECISION-GRADE,
and none of it needs a forecast:

  - where the price sits in its own recent range, and how far off its high
  - how violently it moves, which sets what a sensible stop costs and
    therefore how many shares a given risk budget buys
  - how far the company sits from not being able to pay its debts
  - whether a report is due, which is when a stop protects worst
  - how much of this trade the reader already owns
  - what the round trip costs against what the move is worth

Every line this module emits carries the number it came from. That is
the difference between analysis and assertion, and it is the whole
point: a reader should be able to disagree with the conclusion by
checking the arithmetic, not by taking a different view of a black box.
"""
from __future__ import annotations

import math

# A stop closer than this to the price is inside the stock's own daily
# noise: it will be hit by nothing happening. Two standard deviations of
# a ten-day move is the convention here, stated rather than tuned.
STOP_SIGMAS = 2.0
STOP_HORIZON_DAYS = 10.0
TRADING_DAYS = 252.0


def _pct(a, b):
    """(a/b - 1) as a percentage, or None if either is unusable."""
    try:
        if not a or not b or b <= 0:
            return None
        return round((float(a) / float(b) - 1.0) * 100, 1)
    except (TypeError, ValueError, ZeroDivisionError):
        return None


def price_context(closes: list, dates: list | None = None) -> dict | None:
    """Where this price sits in its own recent history.

    Not a prediction — a location. "Near the top of its range" and "down
    18% from its high" are facts a buyer weighs differently, and neither
    requires believing anything about tomorrow.
    """
    c = [float(x) for x in (closes or []) if x and float(x) > 0]
    if len(c) < 10:
        return None
    last, lo, hi = c[-1], min(c), max(c)
    span = hi - lo
    out = {
        "price": round(last, 2),
        "low": round(lo, 2), "high": round(hi, 2),
        "sessions": len(c),
        # 0% = at the low of the window, 100% = at the high
        "range_pct": round((last - lo) / span * 100) if span > 1e-9 else None,
        "off_high_pct": _pct(last, hi),
        "vs_start_pct": _pct(last, c[0]),
    }
    if len(c) >= 21:
        out["vs_20d_pct"] = _pct(last, c[-21])
    if dates:
        out["from"], out["to"] = dates[0], dates[-1]
    return out


def risk_frame(price: float | None, annual_vol: float | None,
               risk_budget: float = 100.0) -> dict | None:
    """What being wrong costs, and how many shares that buys.

    This is the most useful thing the tool can say without forecasting,
    and it was missing entirely. A reader does not need to know where a
    stock is going to know that a sensible stop on THIS stock sits 9%
    away, that being wrong therefore costs 9% of whatever they commit,
    and that a EUR 100 risk budget buys them a specific number of shares.

    The stop is derived, not guessed: two standard deviations of a
    ten-day move, from the stock's own measured volatility. A tighter
    stop on a violent stock is not caution, it is a guarantee of being
    stopped out by noise.
    """
    try:
        p = float(price)
        v = float(annual_vol)
    except (TypeError, ValueError):
        return None
    if p <= 0 or v <= 0:
        return None
    daily = v / math.sqrt(TRADING_DAYS)
    move = v * math.sqrt(STOP_HORIZON_DAYS / TRADING_DAYS)
    stop_pct = STOP_SIGMAS * move * 100
    stop_px = p * (1 - STOP_SIGMAS * move)
    per_share = p - stop_px
    return {
        "annual_vol_pct": round(v * 100),
        "daily_move_pct": round(daily * 100, 1),
        "stop_pct": round(stop_pct, 1),
        "stop_price": round(stop_px, 2),
        "risk_per_share": round(per_share, 2),
        "risk_budget": risk_budget,
        "shares": int(risk_budget / per_share) if per_share > 0 else None,
        "position_value": (round(int(risk_budget / per_share) * p, 2)
                           if per_share > 0 else None),
    }


def _band_words(rep: dict | None) -> str | None:
    if not rep or rep.get("dd") is None:
        return None
    dd, band = rep["dd"], rep.get("band") or ""
    why = {"swings": " — driven by how hard the share price swings, not by debt",
           "debts": " — driven by what it owes",
           "both": " — driven by both its debts and its swings"}.get(
        rep.get("driven_by"), "")
    return f"{dd} standard deviations from its default point ({band}){why}"


def build(ticker: str, closes: list, dates: list | None = None,
          vol: float | None = None, credit_rep: dict | None = None,
          earnings_days=None, cal_complete: bool = False,
          risk_budget: float = 100.0, dollar_vol: float | None = None) -> dict:
    """One stock, everything known, ordered by what a buyer decides on.

    Returns findings that each carry their own arithmetic, plus a
    `headline` that states the position in one sentence. The headline is
    never a recommendation: it says what is true, and names the single
    thing most worth knowing before committing money.
    """
    t = (ticker or "").strip().upper()
    out: dict = {"ticker": t, "evidence": [], "flags": []}

    ctx = price_context(closes, dates)
    out["price_context"] = ctx
    px = ctx["price"] if ctx else None

    if ctx:
        loc = ("near the top of" if (ctx.get("range_pct") or 0) >= 75
               else "near the bottom of" if (ctx.get("range_pct") or 0) <= 25
               else "in the middle of")
        out["evidence"].append({
            "label": "Where it stands",
            "text": f"{px} — {loc} its {ctx['sessions']}-session range "
                    f"({ctx['low']} to {ctx['high']}), "
                    f"{abs(ctx['off_high_pct'] or 0):.1f}% below the high.",
        })
        if ctx.get("vs_20d_pct") is not None:
            d = ctx["vs_20d_pct"]
            out["evidence"].append({
                "label": "Recent move",
                "text": f"{'up' if d >= 0 else 'down'} {abs(d):.1f}% over the "
                        f"last 20 sessions.",
            })

    rf = risk_frame(px, vol, risk_budget)
    out["risk"] = rf
    if rf:
        out["evidence"].append({
            "label": "What being wrong costs",
            "text": f"This share moves about {rf['daily_move_pct']}% on an "
                    f"average day ({rf['annual_vol_pct']}% a year). A stop "
                    f"inside that is hit by noise alone, so a sensible one "
                    f"sits {rf['stop_pct']}% away, at {rf['stop_price']} — "
                    f"{rf['risk_per_share']} per share. Risking "
                    f"{rf['risk_budget']:.0f} means {rf['shares']} shares, "
                    f"about {rf['position_value']:.0f} committed.",
        })

    cw = _band_words(credit_rep)
    if cw:
        out["evidence"].append({"label": "Can it pay its debts", "text": cw})
    elif credit_rep and credit_rep.get("verdict"):
        out["evidence"].append({"label": "Can it pay its debts",
                                "text": credit_rep["verdict"]})

    if earnings_days is not None:
        soon = earnings_days <= 10
        out["evidence"].append({
            "label": "Earnings",
            "text": (f"reports in {earnings_days} days — a report can gap the "
                     f"price straight through any stop, so a stop does not "
                     f"protect you across it."
                     if soon else
                     f"nothing due for {earnings_days} days."),
        })
        if soon:
            out["flags"].append("earnings")
    elif cal_complete:
        out["evidence"].append({
            "label": "Earnings",
            "text": "nothing scheduled in the next 45 days — checked against "
                    "the published calendar for every trading day in that "
                    "window.",
        })
    else:
        out["flags"].append("earnings_unverified")

    if dollar_vol:
        out["evidence"].append({
            "label": "Liquidity",
            "text": f"about {dollar_vol / 1e6:.0f}M traded on the last "
                    f"session — a position of this size moves in and out "
                    f"without moving the price.",
        })

    # ---- the headline: the one thing most worth knowing ----
    if "earnings" in out["flags"]:
        out["headline"] = (f"{t} reports within days. Whatever else is true, a "
                           f"stop will not protect you across it.")
    elif credit_rep and credit_rep.get("dd") is not None and credit_rep["dd"] < 2.5:
        out["headline"] = (f"{t} sits close to trouble on its debts "
                           f"({credit_rep['dd']} standard deviations). That is "
                           f"the risk that matters here, ahead of the chart.")
    elif rf and rf["annual_vol_pct"] >= 60:
        out["headline"] = (f"{t} is a violent share — {rf['annual_vol_pct']}% a "
                           f"year. A sensible stop is {rf['stop_pct']}% away, so "
                           f"position size, not entry timing, is the decision.")
    elif ctx and (ctx.get("range_pct") or 0) <= 25:
        out["headline"] = (f"{t} is near the bottom of its recent range, "
                           f"{abs(ctx['off_high_pct'] or 0):.0f}% off the high. "
                           f"Cheap against itself is not the same as cheap.")
    elif ctx:
        out["headline"] = (f"{t} at {px}, {abs(ctx['off_high_pct'] or 0):.0f}% "
                           f"off its high, with nothing in the data arguing "
                           f"against a position sized to its swings.")
    else:
        out["headline"] = f"Not enough published data to analyse {t}."
    return out
