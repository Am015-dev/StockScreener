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


def _series_of(price_book: dict) -> dict:
    """The published book is {"dates": [...], "series": {...}}; older
    payloads were a bare {ticker: [closes]}. Read both, so a redeploy that
    lands before the next scan does not blank the check."""
    if isinstance(price_book, dict) and "series" in price_book:
        return price_book.get("series") or {}
    return price_book or {}


def _dates_of(price_book: dict) -> list | None:
    if isinstance(price_book, dict) and "series" in price_book:
        return price_book.get("dates")
    return None


def _corr_series(price_book: dict, tickers: list[str]) -> dict:
    series = _series_of(price_book)
    return {t: series[t] for t in tickers if series.get(t)}


def check(ticker: str, holdings: list[dict], price_book: dict,
          earnings: dict | None = None, cal_complete: bool = False,
          risk_eur: float | None = None, reward_eur: float | None = None,
          friction_pct: float | None = None,
          earn_window_days: int = 45, gate_days: int = 10,
          warming: bool = False, single_source: bool = False,
          held_by_investors: list[str] | None = None) -> dict:
    """Everything the reader cannot work out from a free screener.

    price_book: {ticker: [daily closes]} — published by the scheduled scan,
    so this runs with no network call and no rate limit.
    earnings:   {TICKER: days_to_report} from the same published calendar.
    """
    ticker = (ticker or "").strip().upper()
    held = [h.get("ticker", "").strip().upper()
            for h in (holdings or []) if h.get("ticker")]
    # The strongest possible answer to "do I already own this trade?" is
    # owning the identical ticker — and it used to be the one answer the
    # check could not give. The candidate was silently dropped from the
    # holdings before the comparison, so a reader holding 40 shares of
    # AAPL who checked AAPL was told "This is something you do not
    # already own". The tool's one promise, answered wrongly.
    self_pos = next((h for h in (holdings or [])
                     if (h.get("ticker") or "").strip().upper() == ticker),
                    None)
    held = [h for h in held if h and h != ticker]
    # How much money sits in each position. The bets figure is a property
    # of the portfolio, not of the ticker list, and the sentence built
    # from it talks about the reader's book — so where shares and a cost
    # are known, they are used, and where they are not the equal-weight
    # assumption is stated rather than implied.
    weights, priced = {}, 0
    for h in (holdings or []):
        t = (h.get("ticker") or "").strip().upper()
        if not t or t == ticker:
            continue
        try:
            sh, cost = float(h.get("shares") or 0), float(h.get("cost") or 0)
        except (TypeError, ValueError):
            sh = cost = 0
        if sh > 0 and cost > 0:
            weights[t] = sh * cost
            priced += 1
    weighted = priced == len(held) and priced > 0
    out: dict = {"ticker": ticker, "held": held, "findings": [], "verdict": "ok"}

    def add(level, headline, detail):
        out["findings"].append({"level": level, "headline": headline,
                                "detail": detail})
        order = {"ok": 0, "note": 1, "warn": 2, "block": 3}
        if order[level] > order[out["verdict"]]:
            out["verdict"] = level

    # ---- 1. earnings, verified or blocking ----
    days = (earnings or {}).get(ticker)
    single_note = (" This date came from Yahoo's per-ticker calendar alone — "
                   "no bulk calendar covers this listing to cross-check it."
                   if single_source else "")
    if days is not None:
        if days <= gate_days:
            add("block", f"Earnings in {days} days",
                "A report can gap the price straight through your stop-loss, so "
                "the stop will not protect you. Wait until it has been "
                f"published.{single_note}")
        else:
            add("ok", f"No earnings for {days} days",
                "Nothing scheduled can gap the price through your stop before "
                f"then.{single_note}")
    elif cal_complete:
        add("ok", f"No earnings due for at least {earn_window_days} days",
            "Checked against the published market calendar for every trading day "
            "in that window — this company appears on none of them.")
    elif warming:
        # Distinct from a failure. "Still loading" and "could not be read"
        # both block the pick, but only one of them is worth waiting out,
        # and telling a reader their data is broken when it is merely late
        # spends trust for nothing.
        add("block", "Earnings calendar is still loading",
            "The first check after the server restarts builds a 45-day calendar. "
            "Give it a minute and try again — this is not an error.")
    else:
        add("block", "Earnings date could not be verified",
            "The calendar could not be read in full, so absence proves nothing. "
            "This is refused rather than guessed.")

    # ---- 2. how much of this do you already own? ----
    if self_pos is not None:
        try:
            sh = float(self_pos.get("shares") or 0)
        except (TypeError, ValueError):
            sh = 0
        size = f" — {sh:g} shares" if sh > 0 else ""
        rest = (" The overlap figures below compare it against the rest of "
                "your book." if held else
                " It is also your only position, so there is nothing else "
                "to compare it against.")
        add("warn", "You already own this exact stock",
            f"{ticker} is in the holdings you gave{size}. Buying more makes "
            f"an existing bet bigger; nothing about it is new.{rest}")
    if not held and self_pos is None:
        add("note", "No holdings given",
            "Paste your positions to find out whether this is a new bet or one "
            "you already have.")
    elif not held:
        pass          # the self-holding warning above is the whole answer
    else:
        dates = _dates_of(price_book)
        series = _corr_series(price_book, held + [ticker])
        corr = concentration.correlation(series, dates=dates)
        # Holdings with no usable history were silently dropped from the
        # comparison while the sentences still counted all of them, so a
        # reader with eight positions of which three could be measured was
        # told "across 8 positions" — and one of the five unchecked ones
        # could be the same trade. Name them instead.
        measured = [h for h in held if h in getattr(corr, "columns", [])]
        unmeasured = [h for h in held if h not in measured]
        if (corr.empty or ticker not in corr.columns) and warming and not _series_of(price_book):
            add("warn", "Price history is still loading",
                "Overlap against your holdings needs the published price book, which "
                "arrives shortly after a restart. Try again in a minute.")
        elif corr.empty or ticker not in corr.columns:
            add("warn", "Overlap could not be measured",
                "Not enough shared price history. Treat this as if it overlaps "
                "with what you hold, not as if it were new.")
        else:
            pairs = sorted(
                ((float(corr.at[ticker, h]), h) for h in measured
                 if h != ticker),
                reverse=True)
            if unmeasured:
                shown = ", ".join(unmeasured[:6])
                more = f" and {len(unmeasured) - 6} more" if len(unmeasured) > 6 else ""
                add("warn",
                    f"{len(unmeasured)} of your {len(held)} positions could not be compared",
                    f"There is no published price history for {shown}{more}, so the "
                    f"overlap below is measured against the other "
                    f"{len(measured)} only. Treat the unlisted ones as if they "
                    f"overlap, not as if they were separate.")
            if pairs:
                top, partner = pairs[0]
                if top >= concentration.SAME_TRADE:
                    add("warn", f"You already own this trade, under another name",
                        f"{ticker} and {partner} have moved almost in lockstep for "
                        f"the last three months. Buying it makes your {partner} "
                        f"position bigger; it does not add a second one. One bad "
                        f"morning takes out both.")
                elif top >= 0.4:
                    share = int(round(top * 100))
                    add("note", f"Part of this is a bet you already have",
                        f"{ticker} and {partner} tend to move the same way — when "
                        f"one has a bad day the other usually does too, about "
                        f"{share} times out of a hundred. Buy less than you were "
                        f"going to, and count it as more of {partner} rather than "
                        f"as something new.")
                elif self_pos is None:
                    add("ok", "This is something you do not already own",
                        f"The closest thing in your book is {partner}, and the two "
                        f"move independently enough that a bad week for one is not "
                        f"a bad week for the other.")
                else:
                    add("note", "The rest of your book moves independently of it",
                        f"Aside from your existing {ticker} position, the closest "
                        f"thing you hold is {partner}, and the two move "
                        f"independently.")

            # ---- 3. bets before and after ----
            before = concentration.effective_bets(
                concentration.correlation(_corr_series(price_book, held),
                                          dates=dates))
            after = concentration.effective_bets(corr)
            if before is not None and after is not None:
                out["bets_before"] = round(before, 1)
                out["bets_after"] = round(after, 1)
                # the figure counts stocks, not euros — see
                # concentration.effective_bets for why it is not weighted
                basis = (" This counts each holding once, so it reflects which "
                         "stocks you own rather than how much of each.")
                gain = after - before
                if gain < 0.35:
                    add("warn",
                        f"This adds money at risk without spreading it wider",
                        f"Your {len(measured)} positions behave like about "
                        f"{before:.1f} separate ones today, and about {after:.1f} "
                        f"with this added — near enough no change, because it "
                        f"moves with something you already hold. It is more money "
                        f"on a bet you have, not a new one.{basis}")
                elif self_pos is None:
                    add("ok",
                        f"This genuinely spreads your money wider",
                        f"Your {len(measured)} positions behave like about "
                        f"{before:.1f} separate ones today, and about {after:.1f} "
                        f"with this added — it is a different bet, not a bigger "
                        f"version of one you already have.{basis}")
                # holding it already, "spreads your money wider" would
                # contradict the warning above on the same card; the
                # self-holding finding is the answer and nothing here
                # needs to argue with it

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

    # ---- 5. who else holds this (informational — never a decision) ----
    # Always "note" level: this never appears in blocks/warns below, so it
    # can never move the bottom line or the verdict. A 13F is long-only,
    # US-equity-only, and up to ~135 days stale by construction — see
    # KNOWN_ISSUES.md — so it answers "who has owned this", not "should I".
    if held_by_investors:
        names = sorted(held_by_investors)
        shown = ", ".join(names[:5])
        more = f", and {len(names) - 5} more" if len(names) > 5 else ""
        add("note",
            f"Held by {len(names)} tracked superinvestor"
            f"{'s' if len(names) != 1 else ''}",
            f"{shown}{more} — from SEC 13F filings, as of their last "
            f"reported quarter (up to ~135 days stale) and long-only. Not a "
            f"signal, not scored anywhere on this site. See /investors.")

    if risk_eur is not None:
        out["risk_eur"] = risk_eur
    out["n_findings"] = len(out["findings"])
    # ---- the answer, stated as one ----
    # A list of findings is evidence; it is not a decision. The reader's
    # question is "should anything here stop me?", and making them derive
    # the answer from four paragraphs is how a tool earns "nobody
    # understands a thing". The verdict already exists — say it first.
    blocks = [f["headline"] for f in out["findings"] if f["level"] == "block"]
    warns = [f["headline"] for f in out["findings"] if f["level"] == "warn"]
    if blocks:
        out["bottom_line"] = ("Do not buy this today: "
                              + "; ".join(b.lower() for b in blocks[:2]) + ".")
    elif warns:
        out["bottom_line"] = ("Think twice — "
                              + "; ".join(w.lower() for w in warns[:2]) + ".")
    else:
        out["bottom_line"] = "Nothing here argues against it."

    return out
