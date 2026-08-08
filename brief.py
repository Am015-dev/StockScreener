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
    price, support = row.get("price"), row.get("support")
    if price and support:
        bits.append(f"pulled back to {((price / support) - 1) * 100:.1f}% above a "
                    f"price level buyers defended before")
    vr = row.get("vol_ratio")
    if vr is not None:
        bits.append("on quiet selling volume" if vr < 1.0
                    else f"on heavier volume than usual ({vr:.1f}×)")
    an = row.get("analyst")
    if an:
        bits.append(f"analysts rate it {an}")
    return ", ".join(bits) if bits else "setup details unavailable"


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

    # The pick is the top of the board, which is already ordered by what it
    # adds rather than by score — and a measured loser can never be there.
    pick = results[0] if results else None
    action = None
    if pick:
        shares = pick.get("shares") or 0
        price = pick.get("price") or 0
        stop = pick.get("stop")
        target = pick.get("resistance")
        risk = pick.get("risk_EUR")
        rr = pick.get("RR")
        reward = round(risk * rr, 2) if (risk is not None and rr) else None
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
            "edge_r": pick.get("edge_r"),
            "edge_n": pick.get("edge_n"),
            "adds_r": pick.get("mpc_r"),
            "friction_pct": pick.get("friction_pct"),
            "sector": pick.get("sector"),
        }

    # Blocked, grouped by the rule that stopped them — never a bare count.
    groups: dict[str, list[str]] = {}
    for r in pending:
        groups.setdefault(_plain_block_reason(r.get("why_not")), []).append(
            r.get("ticker") or "?")
    blocked = [{"reason": k, "n": len(v), "tickers": v}
               for k, v in sorted(groups.items(), key=lambda kv: -len(kv[1]))]

    # Everything else that qualified but is not today's single action.
    also = [{"ticker": r.get("ticker"), "adds_r": r.get("mpc_r"),
             "name": r.get("name")} for r in results[1:6]]

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

    return {
        "date": when.strftime("%A %-d %B") if when else None,
        "asof": when.strftime("%H:%M UTC") if when else None,
        "market_label": (market or {}).get("label"),
        "fresh_phrase": (fresh or {}).get("phrase"),
        "stale": bool((fresh or {}).get("stale")),
        "action": action,
        "n_qualified": len(results),
        "also": also,
        "blocked": blocked,
        "n_blocked": len(pending),
        "scanned": state.get("universe_size"),
        "record": record,
        # The bar the strategy has to clear before any of this is tradeable.
        # It is on the card, not buried, because it is currently failing.
        "bar": {
            "profit_factor": port.get("profit_factor"),
            "passes": bool(port.get("profit_factor") and
                           port["profit_factor"] >= 1.5 and
                           (port.get("sortino") or -9) >= 1.0),
        },
        "concentration": state.get("concentration") or {},
    }
