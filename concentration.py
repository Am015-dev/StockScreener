"""How many bets are actually in a list of picks?

Every screener on the internet hands you a ranked list and lets you
believe the rows are separate decisions. They are usually not. A scan
that surfaces twenty-five names in a single tape often surfaces the same
trade twenty-five times — the setup that fires on one semiconductor
fires on its whole supply chain, and they will be stopped out together
on the same morning.

This is not a theoretical worry; it is the finding that killed this
project's own strategy. The five-year replay produced a profit factor of
1.62 when every signal was taken on its own, and 0.62 through a
five-position portfolio. Nothing about the individual trades changed.
The entire difference was that signals arrive in clusters, and a book
that is full when the good ones arrive takes the correlated leftovers.

So the number a reader needs is not "how many picks" but "how many
independent bets", and the ranking they need is not the standalone score
but the marginal contribution of each pick given what they already own
and what sits above it on the list. That requires knowing their book and
the correlation structure. A free screener knows neither.

Three things are computed here:

  effective_bets()  Meucci's effective number of bets — the entropy of
                    the correlation matrix's eigenvalue spectrum. Twenty
                    five uncorrelated picks score 25. Twenty five
                    perfectly correlated picks score 1.
  clusters()        which picks are the same trade, named so a reader
                    can see it at a glance.
  marginal()        expected R per pick after subtracting the part of it
                    already owned, ranked by that instead of by score.

Everything here fails closed. A correlation computed from too little
overlapping history is reported as unknown, never as zero — assuming
independence is precisely the error this module exists to correct.
"""
from __future__ import annotations

import math

import numpy as np
import pandas as pd

# Daily closes to correlate. Sixty sessions is a quarter of trading: long
# enough that a single news day cannot manufacture a correlation, short
# enough to reflect the regime the picks were found in.
CORR_DAYS = 60
# Below this many overlapping observations a correlation is noise. Two
# series sharing fifteen days can show 0.9 by accident.
MIN_OVERLAP = 40
# Above this, two picks are the same trade for position-sizing purposes.
# Chosen at the level where a common stop-out day becomes the norm rather
# than the exception, not from an optimisation.
SAME_TRADE = 0.70


def returns_frame(series_by_ticker: dict, days: int = CORR_DAYS) -> pd.DataFrame:
    """Daily percentage returns, one column per ticker, most recent `days`.

    Accepts anything indexable — a pandas Series of closes, or the plain
    list the published payload carries as `spark`.
    """
    cols = {}
    for t, closes in (series_by_ticker or {}).items():
        if closes is None:
            continue
        s = pd.Series(list(closes), dtype="float64").dropna()
        if len(s) < MIN_OVERLAP + 1:
            continue
        s = s.iloc[-(days + 1):]
        r = s.pct_change().dropna()
        if len(r) >= MIN_OVERLAP and float(r.std()) > 0:
            cols[t] = r.reset_index(drop=True)
    if not cols:
        return pd.DataFrame()
    return pd.DataFrame(cols)


def correlation(series_by_ticker: dict, days: int = CORR_DAYS) -> pd.DataFrame:
    """Pairwise correlation of daily returns. Empty frame if unknowable."""
    rf = returns_frame(series_by_ticker, days)
    if rf.shape[1] < 2:
        return pd.DataFrame()
    return rf.corr()


def effective_bets(corr: pd.DataFrame) -> float | None:
    """Meucci's effective number of bets.

    The correlation matrix's eigenvalues describe how many genuinely
    distinct directions the book is exposed to. Their normalised spectrum
    is a probability distribution; its perplexity — exp(entropy) — is the
    count of independent bets. N uncorrelated assets give N. N copies of
    one asset give 1.

    Returns None when there is not enough data to say, because "we could
    not measure the concentration" and "there is no concentration" must
    never render as the same number.
    """
    if corr is None or corr.empty or corr.shape[0] < 2:
        return None
    m = corr.to_numpy(dtype="float64")
    if not np.all(np.isfinite(m)):
        return None
    vals = np.linalg.eigvalsh((m + m.T) / 2.0)     # symmetrise; guard drift
    vals = np.clip(vals, 0.0, None)
    total = float(vals.sum())
    if total <= 0:
        return None
    p = vals / total
    p = p[p > 1e-12]
    return float(math.exp(float(-(p * np.log(p)).sum())))


def clusters(corr: pd.DataFrame, order: list[str],
             threshold: float = SAME_TRADE) -> list[dict]:
    """Group picks that are the same trade.

    Greedy single-seed grouping in the caller's ranking order: the
    highest-ranked ungrouped pick seeds a cluster and absorbs everything
    still ungrouped that correlates with it at or above the threshold.
    Chosen over a dendrogram deliberately — a reader can check this one
    ("these move with MGM") and cannot check a linkage tree.
    """
    if corr is None or corr.empty:
        return []
    out, taken = [], set()
    for seed in order:
        if seed in taken or seed not in corr.columns:
            continue
        members, corrs = [seed], []
        for other in order:
            if other == seed or other in taken or other not in corr.columns:
                continue
            c = corr.at[seed, other]
            if pd.notna(c) and float(c) >= threshold:
                members.append(other)
                corrs.append(float(c))
        taken.update(members)
        out.append({"seed": seed, "tickers": members, "n": len(members),
                    "mean_corr": round(float(np.mean(corrs)), 2) if corrs else None})
    return out


def marginal(rows: list[dict], corr: pd.DataFrame, edge_of,
             held: list[str] | None = None) -> list[dict]:
    """Rank by marginal contribution instead of standalone score.

    For each pick, in the caller's current order:

      edge_r    expected R for this setup, from `edge_of(ticker)`, which
                returns (expected_r, sample_size, source) or None.
      overlap   the highest correlation to anything ALREADY committed —
                a position the reader holds, or a pick ranked above this
                one. Those are the bets already made; this is the part of
                this pick that is not new.
      indep     1 - overlap. The fraction of this pick that is a genuinely
                separate bet.
      mpc_r     edge_r * indep. What the pick is expected to add, after
                removing what is already owned.

    A pick whose edge is unverified gets mpc_r None and is NOT ranked.
    Scoring an unmeasured edge as though it were the average is the same
    fail-open this project has spent its life removing: it would let a
    setup with no track record outrank one with a measured positive
    expectancy, purely for being uncorrelated.
    """
    held = [h for h in (held or []) if h]
    committed = list(held)
    out = []
    for r in rows:
        t = r.get("ticker")
        row = dict(r)
        e = edge_of(t) if t else None
        overlap, partner = None, None
        if corr is not None and not corr.empty and t in corr.columns:
            for c in committed:
                if c not in corr.columns or c == t:
                    continue
                v = corr.at[t, c]
                if pd.isna(v):
                    continue
                if overlap is None or float(v) > overlap:
                    overlap, partner = float(v), c
        # Three states, and collapsing any two of them is a bug:
        #   nothing committed yet   -> genuinely independent (indep 1.0)
        #   measured against all    -> use the measurement
        #   could not be measured   -> conservative, never "independent"
        # The first row of a list with no holdings is in the first state;
        # a ticker missing from the correlation matrix is in the third.
        # They look identical (overlap is None) and mean opposite things.
        measurable = (corr is not None and not corr.empty and t in corr.columns
                      and any(c in corr.columns for c in committed if c != t))
        nothing_committed = not committed
        row["corr_max"] = None if overlap is None else round(overlap, 2)
        row["corr_with"] = partner
        row["corr_known"] = nothing_committed or measurable
        row["held_overlap"] = bool(partner in held) if partner else False
        if e is None:
            row["edge_r"] = None
            row["edge_n"] = None
            row["edge_source"] = "unverified"
            row["indep"] = None
            row["mpc_r"] = None
        else:
            er, n, source = e
            if overlap is not None:
                ov = max(0.0, overlap)
            elif nothing_committed:
                ov = 0.0          # first pick: there is nothing to overlap WITH
            elif measurable:
                ov = 0.0          # measured against everything committed, no match
            else:
                ov = SAME_TRADE   # unmeasurable — never reads as independent
            indep = max(0.0, 1.0 - ov)
            row["edge_r"] = round(float(er), 3)
            row["edge_n"] = int(n)
            row["edge_source"] = source
            row["indep"] = round(indep, 2)
            row["mpc_r"] = round(float(er) * indep, 3)
            # A measured LOSING record is not a weak buy, it is evidence
            # against the trade. The technical score cannot see it: score
            # rates the shape of the setup, this rates what happened last
            # time the shape appeared on this stock under these rules.
            row["edge_negative"] = float(er) < 0
        out.append(row)
        if t:
            committed.append(t)

    ranked = sorted(
        [r for r in out if r.get("mpc_r") is not None],
        key=lambda r: r["mpc_r"], reverse=True)
    for i, r in enumerate(ranked, 1):
        r["mpc_rank"] = i
    # rank movement is the whole point — a pick that is 3rd by score and
    # 11th by contribution is telling the reader something they cannot get
    # anywhere else
    by_score = {r.get("ticker"): i for i, r in enumerate(out, 1)}
    for r in out:
        r["score_rank"] = by_score.get(r.get("ticker"))
        if r.get("mpc_rank") and r.get("score_rank"):
            r["rank_delta"] = r["score_rank"] - r["mpc_rank"]
    return out


def order_key(row: dict) -> tuple:
    """Sort key that stops a measured loser being presented as the best pick.

    The technical score rates the shape of a setup. It cannot see that the
    last fifteen times that shape appeared on this stock, under these
    exact rules, the trade lost money. Ranking by score alone put a name
    with a measured -0.15R record at the top of the board with a score of
    77 — the same fail-open this project keeps finding in new clothes.

    Three tiers, and the middle one matters:
      1. measured positive contribution, best first
      2. no measurement yet, by technical score
      3. measured negative record, least bad first

    An unmeasured setup is NOT demoted below a losing one. Absence of
    evidence and evidence of loss are different things, and collapsing
    them would punish every new name for being new.
    """
    mpc = row.get("mpc_r")
    score = row.get("score") or 0
    if mpc is None:
        return (1, 0.0, -score)
    if mpc >= 0:
        return (0, -mpc, -score)
    return (2, -mpc, -score)


def summarise(rows: list[dict], corr: pd.DataFrame,
              groups: list[dict]) -> dict:
    """The headline: how many bets are really here."""
    enb = effective_bets(corr)
    n = len([r for r in rows if r.get("ticker")])
    measured = 0 if corr is None or corr.empty else int(corr.shape[0])
    biggest = max(groups, key=lambda g: g["n"]) if groups else None
    unverified = len([r for r in rows if r.get("edge_source") == "unverified"])
    losing = len([r for r in rows if r.get("edge_negative")])
    return {
        "n_picks": n,
        "n_measured": measured,
        "effective_bets": None if enb is None else round(enb, 1),
        "n_clusters": len(groups) or None,
        "biggest_cluster": None if not biggest or biggest["n"] < 2 else {
            "seed": biggest["seed"], "n": biggest["n"],
            "tickers": biggest["tickers"], "mean_corr": biggest["mean_corr"]},
        "unverified_edge": unverified,
        "losing_edge": losing,
        "corr_days": CORR_DAYS,
        "same_trade_threshold": SAME_TRADE,
    }
