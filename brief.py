"""One card, one decision — the whole product on one screen.

The screener page asked a reader to understand RSI, reward:risk, support
distance and a twenty-one column table before they could do anything. It
also carried a Run button that started a crawl, which is where the
timeouts and the stale downloads came from.

This is the replacement. The server scans on a schedule; this renders
what was already decided. It answers three questions and stops: what do I
do today, what did the tool refuse to show me and why, and has any of
this ever worked. If nothing qualifies it says so, which is a complete
answer and not a failure.

Nothing here computes anything about the market. It reads finished state
and turns it into sentences — so a bug in this file can make the page
wrong, but it cannot make a pick wrong.
"""
from __future__ import annotations

import datetime as _dt

# Reasons, in the reader's words, keyed by the phrase the scan uses.
_BLOCK_WORDS = (
    ("no earnings calendar covers", "no earnings calendar covers the listing"),
    ("earnings date can't be verified", "earnings date could not be verified"),
    ("profitability/size/sector", "company fundamentals could not be verified"),
    ("same company as", "duplicate listing of a company already shown"),
    ("sector", "your sector allocation is full"),
    ("cash", "not enough free cash"),
)


def _plain_block_reason(why: str) -> str:
    low = (why or "").lower()
    for needle, words in _BLOCK_WORDS:
        if needle in low:
            return words
    return (why or "blocked").split("—")[0].strip()[:70] or "blocked"


def _money(x, cur="$"):
    if x is None:
        return None
    return f"{cur}{x:,.2f}" if abs(x) < 1000 else f"{cur}{x:,.0f}"


def _why_sentence(row: dict) -> str:
    """The setup in one sentence a non-trader can check."""
    bits = []
    price, support = _f(row.get("price")), _f(row.get("support"))
    if price and support:
        bits.append(f"pulled back to {((price / support) - 1) * 100:.1f}% above a "
                    f"price level buyers defended before")
    vr = _f(row.get("vol_ratio"))
    if vr is not None:
        bits.append("on quiet selling volume" if vr < 1.0
                    else f"on heavier volume than usual ({vr:.1f}×)")
    an = row.get("analyst")
    if an:
        bits.append(f"analysts rate it {an}")
    return ", ".join(bits) if bits else "setup details unavailable"


def _f(x):
    """A number, or None. Rows reach here from a CSV, a published payload
    and a browser-mirrored snapshot, so a field can be a string, a NaN or
    absent — and build() promises never to raise."""
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    return None if v != v else v          # NaN


def _pct_above(price, support):
    """How far above support a price sits, or None if either is unusable."""
    try:
        if not price or not support:
            return None
        return round((float(price) / float(support) - 1) * 100, 1)
    except (TypeError, ValueError, ZeroDivisionError):
        return None


def build(state: dict, market: dict | None = None,
          fresh: dict | None = None) -> dict:
    """Turn finished scan state into the card. Never raises."""
    results = list(state.get("results") or [])
    pending = list(state.get("pending") or [])
    journal = state.get("journal") or {}
    bt = state.get("backtest") or {}
    port = (bt.get("portfolio") or {})

    ts = state.get("results_ts")
    when = _dt.datetime.fromtimestamp(ts, _dt.timezone.utc) if ts else None

    # The top of the board, already ordered by what it adds rather than by
    # score. Deliberately NOT called a recommendation anywhere: the pattern
    # has not been profitable in the replay, so presenting it as something
    # to do is both dishonest and — once money changes hands — a different
    # regulatory category. It is a watchlist entry with its working shown.
    pick = results[0] if results else None
    action = None
    if pick:
        shares = _f(pick.get("shares")) or 0
        price = _f(pick.get("price")) or 0
        stop = _f(pick.get("stop"))
        target = _f(pick.get("resistance"))
        risk = _f(pick.get("risk_EUR"))
        rr = _f(pick.get("RR"))
        reward = round(risk * rr, 2) if (risk is not None and rr) else None
        # R is units of planned risk. It is the natural unit for a trader
        # and meaningless to everyone else, and the card never defined it —
        # "+0.229R" was the single most novel number on the page and also
        # the least readable. Multiplying by the euro risk turns it into
        # the only unit that needs no explanation.
        er, mpc = _f(pick.get("edge_r")), _f(pick.get("mpc_r"))
        edge_eur = round(er * risk, 2) if (er is not None and risk) else None
        adds_eur = round(mpc * risk, 2) if (mpc is not None and risk) else None
        action = {
            "ticker": pick.get("ticker"),
            "name": pick.get("name") or pick.get("ticker"),
            "shares": round(shares, 2),
            "price": price,
            "cost": _money(round(shares * price, 2)),
            "stop": stop,
            "risk_eur": risk,
            "target": target,
            "reward_eur": reward,
            "earnings": pick.get("earnings_in"),
            # ">45d" is a confirmation, not a missing value — say which
            "earnings_clear": str(pick.get("earnings_in") or "").startswith(">"),
            "why": _why_sentence(pick),
            "edge_eur": edge_eur,
            "edge_n": pick.get("edge_n"),
            "adds_eur": adds_eur,
            "friction_pct": _f(pick.get("friction_pct")),
            "friction_eur": (round(reward * _f(pick.get("friction_pct")) / 100, 2)
                             if reward and _f(pick.get("friction_pct")) else None),
            "sector": pick.get("sector"),
        }
        # The card states an entry, a stop and a euro risk as plain facts,
        # and the template formats them as numbers. A pick missing any of
        # the three cannot be described without inventing one of them, and
        # a half-described trade is worse than no card: it reads as
        # complete. Withhold it the same way an unverifiable pick is
        # withheld, rather than rendering "None" or failing the whole page.
        if (stop is None or risk is None or not price
                or target is None or reward is None):
            action = None

    # Blocked, grouped by the rule that stopped them — never a bare count.
    groups: dict[str, list[str]] = {}
    for r in pending:
        groups.setdefault(_plain_block_reason(r.get("why_not")), []).append(
            r.get("ticker") or "?")
    blocked = [{"reason": k, "n": len(v), "tickers": v}
               for k, v in sorted(groups.items(), key=lambda kv: -len(kv[1]))]

    # Everything else that qualified but is not today's single action.
    also = [{"ticker": r.get("ticker"), "name": r.get("name"),
             "adds_eur": (round(_f(r.get("mpc_r")) * _f(r.get("risk_EUR")), 2)
                          if _f(r.get("mpc_r")) is not None
                          and _f(r.get("risk_EUR")) else None)}
            for r in results[1:6]]

    # The single most useful thing the concentration work knows, said as
    # an instruction rather than a statistic: if you took the whole list,
    # these names are one position wearing several tickers.
    conc = state.get("concentration") or {}
    one_trade = None
    bc = conc.get("biggest_cluster")
    if bc and bc.get("n", 0) >= 2:
        one_trade = {"tickers": bc["tickers"], "n": bc["n"]}

    record = None
    if journal.get("n_resolved"):
        record = {
            "n": journal.get("n_resolved"),
            "win_rate": journal.get("hit_rate_pct"),
            "avg_r": journal.get("avg_r"),
            "total_r": journal.get("total_r"),
            "recent": (journal.get("recent") or [])[:1],
        }
    elif journal.get("n_total"):
        record = {"n": 0, "logged": journal.get("n_total"), "win_rate": None,
                  "avg_r": None, "total_r": None, "recent": []}

    # The card told a reader to buy a named stock and, four inches lower,
    # not to put real money behind any of it. Both sentences were true and
    # together they were useless: a suggestion you are simultaneously
    # warned off is not a decision, it is a shrug with a ticker attached.
    #
    # So the headline follows the evidence. Until the replay clears the
    # bar this is explicitly a PAPER trade that the site is tracking in
    # public; the moment it clears, the same card becomes an instruction.
    # One state, one voice, no footnote reversing the headline.
    tradeable = bool(port.get("profit_factor") and port["profit_factor"] >= 1.5
                     and (port.get("sortino") or -9) >= 1.0)

    return {
        "tradeable": tradeable,
        "one_trade": one_trade,
        "date": when.strftime("%A %-d %B") if when else None,
        "asof": when.strftime("%H:%M UTC") if when else None,
        "market_label": (market or {}).get("label"),
        "fresh_phrase": (fresh or {}).get("phrase"),
        "stale": bool((fresh or {}).get("stale")),
        "action": action,
        "n_qualified": len(results),
        "watchlist": [
            {"ticker": r.get("ticker"), "name": r.get("name"),
             "price": _f(r.get("price")), "stop": _f(r.get("stop")),
             "target": _f(r.get("resistance")), "rsi": _f(r.get("RSI")),
             # both halves, not just the divisor: a row carrying a support
             # level and no price took the whole page down with a KeyError,
             # and a restored browser snapshot is enough to produce one
             "support_dist": _pct_above(r.get("price"), r.get("support")),
             "earnings": r.get("earnings_in"),
             "analyst": r.get("analyst")}
            for r in results[:8]],
        "also": also,
        "blocked": blocked,
        "n_blocked": len(pending),
        "scanned": state.get("universe_size"),
        "record": record,
        # The bar the strategy has to clear before any of this is tradeable.
        # It is on the card, not buried, because it is currently failing.
        "bar": {"profit_factor": port.get("profit_factor"), "passes": tradeable},
        "concentration": state.get("concentration") or {},
    }
