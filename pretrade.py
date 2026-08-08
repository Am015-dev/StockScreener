"""Check a trade the reader is about to make — theirs, not ours.

The picks this project generates lose money: the five-year replay through
a realistic book returns a profit factor of 0.62 against the 1.5 required,
and not one published pick has resolved yet. Selling those picks would be
selling something the site itself tells people not to use.

But three of the things built to police those picks do not depend on the
strategy working at all, and none of them is available free anywhere:

  - an earnings date VERIFIED against a published calendar, where "could
    not verify" blocks rather than shrugs;
  - how much of a trade the reader is already holding, measured as return
    correlation against their actual book rather than guessed from sector
    labels;
  - how many genuinely separate bets their book contains, before and
    after the trade.

A screener answers "what should I buy". This answers "I am about to buy
this — what am I not seeing", which is a question the reader brings their
own idea to. It needs no track record to be worth something, because it
makes no prediction: every output is a fact about data they cannot
assemble themselves.

Nothing here forecasts. It measures.
"""
from __future__ import annotations

import concentration


def _corr_series(price_book: dict, tickers: list[str]) -> dict:
    return {t: price_book[t] for t in tickers if price_book.get(t)}


def check(ticker: str, holdings: list[dict], price_book: dict,
          earnings: dict | None = None, cal_complete: bool = False,
          risk_eur: float | None = None, reward_eur: float | None = None,
          friction_pct: float | None = None,
          earn_window_days: int = 45, gate_days: int = 10) -> dict:
    """Everything the reader cannot work out from a free screener.

    price_book: {ticker: [daily closes]} — published by the scheduled scan,
    so this runs with no network call and no rate limit.
    earnings:   {TICKER: days_to_report} from the same published calendar.
    """
    ticker = (ticker or "").strip().upper()
    held = [h.get("ticker", "").strip().upper()
            for h in (holdings or []) if h.get("ticker")]
    held = [h for h in held if h and h != ticker]
    out: dict = {"ticker": ticker, "held": held, "findings": [], "verdict": "ok"}

    def add(level, headline, detail):
        out["findings"].append({"level": level, "headline": headline,
                                "detail": detail})
        order = {"ok": 0, "note": 1, "warn": 2, "block": 3}
        if order[level] > order[out["verdict"]]:
            out["verdict"] = level

    # ---- 1. earnings, verified or blocking ----
    days = (earnings or {}).get(ticker)
    if days is not None:
        if days <= gate_days:
            add("block", f"Earnings in {days} days",
                "A report can gap the price straight through your stop-loss, so "
                "the stop will not protect you. Wait until it has been published.")
        else:
            add("ok", f"No earnings for {days} days",
                "Nothing scheduled can gap the price through your stop before then.")
    elif cal_complete:
        add("ok", f"No earnings due for at least {earn_window_days} days",
            "Checked against the published market calendar for every trading day "
            "in that window — this company appears on none of them.")
    else:
        add("block", "Earnings date could not be verified",
            "The calendar could not be read in full, so absence proves nothing. "
            "This is refused rather than guessed.")

    # ---- 2. how much of this do you already own? ----
    if not held:
        add("note", "No holdings given",
            "Paste your positions to find out whether this is a new bet or one "
            "you already have.")
    else:
        series = _corr_series(price_book, held + [ticker])
        corr = concentration.correlation(series)
        if corr.empty or ticker not in corr.columns:
            add("warn", "Overlap could not be measured",
                "Not enough shared price history. Treat this as if it overlaps "
                "with what you hold, not as if it were new.")
        else:
            pairs = sorted(
                ((float(corr.at[ticker, h]), h) for h in held
                 if h in corr.columns and h != ticker),
                reverse=True)
            if pairs:
                top, partner = pairs[0]
                pct = int(round(max(0.0, top) * 100))
                if top >= concentration.SAME_TRADE:
                    add("warn", f"You already own {pct}% of this trade",
                        f"{ticker} and {partner} have moved together {pct}% of the "
                        f"time over the last {concentration.CORR_DAYS} trading days. "
                        f"Buying it doubles that position rather than adding a new "
                        f"one — and one bad morning takes out both.")
                elif top >= 0.4:
                    add("note", f"Partly overlaps {partner} ({pct}%)",
                        f"Some of this bet is one you already hold. Size it as an "
                        f"addition to {partner}, not as a separate position.")
                else:
                    add("ok", "This is a genuinely new bet",
                        f"Its closest match in your book is {partner} at {pct}% — "
                        f"low enough that they can fail independently.")

            # ---- 3. bets before and after ----
            before = concentration.effective_bets(
                concentration.correlation(_corr_series(price_book, held)))
            after = concentration.effective_bets(corr)
            if before is not None and after is not None:
                out["bets_before"] = round(before, 1)
                out["bets_after"] = round(after, 1)
                gain = after - before
                if gain < 0.35:
                    add("warn",
                        f"Your book stays at about {after:.1f} real bets",
                        f"You hold {len(held)} positions worth about {before:.1f} "
                        f"independent bets. Adding this makes it {after:.1f} — you "
                        f"take on more money at risk without spreading it further.")
                else:
                    add("ok",
                        f"Real bets rise from {before:.1f} to {after:.1f}",
                        f"Across {len(held)} positions, this genuinely widens the "
                        f"book rather than thickening a bet you already have.")

    # ---- 4. do the costs eat it? ----
    if friction_pct is not None and reward_eur:
        eaten = round(reward_eur * friction_pct / 100, 2)
        if friction_pct >= 15:
            add("warn", f"Costs take €{eaten:.2f} of a €{reward_eur:.2f} win",
                f"Commission and spread are {friction_pct:.0f}% of the profit you "
                f"are aiming for. At this trade size the fees are a material part "
                f"of the outcome.")
        else:
            add("ok", f"Costs take €{eaten:.2f} of a €{reward_eur:.2f} win",
                f"{friction_pct:.0f}% of the target profit — not decisive.")

    if risk_eur is not None:
        out["risk_eur"] = risk_eur
    out["n_findings"] = len(out["findings"])
    return out
