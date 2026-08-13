"""Run the pattern library against real market history and publish it.

This runs on a GitHub Actions runner because it needs a year of daily
bars for several hundred companies — about ten minutes of polite
fetching, which is fine here and impossible in a web request. The
history is cached on the data branch, so later runs fetch only what they
do not already hold.

No API key is required. The Polygon path is used only if a key happens
to be present, and only to choose which companies to look at; the
history itself comes from the chart endpoint, one request per name.

It publishes `patterns.json` whatever the outcome, including —
especially including — the outcome where nothing survives. A discovery
framework that only publishes when it finds something is a machine for
producing false positives, and this project has already paid for one of
those.
"""
import argparse
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import history                                                   # noqa: E402
import patterns                                                  # noqa: E402
import universe_static                                           # noqa: E402


def candidates(cap: int, log) -> list:
    """Which companies to fetch history for, before ranking them.

    A wider pool than the sweep needs, because the universe is ranked at
    the START of the window and the ranking has to have somewhere to move
    to. Polygon is used when a key exists because it can see the whole
    market; the built-in list is the fallback and needs nothing.
    """
    try:
        import datetime as dt
        import polygon_data as polygon
        if polygon.have_key():
            bars, d = {}, dt.date.today() - dt.timedelta(days=1)
            # walk back to a day the market actually answered for: a
            # weekend or a holiday returns empty, which is not an error
            for _ in range(6):
                if d.weekday() < 5:
                    bars = polygon.grouped_day(d.isoformat(), progress=log)
                    if bars:
                        break
                d -= dt.timedelta(days=1)
            common = polygon.common_stocks(progress=log)
            got = polygon.universe_by_liquidity(bars, common or None,
                                                cap=cap, min_dollar_vol=5e6)
            if got:
                log(f"{len(got)} candidates from the whole traded market")
                return got
    except Exception as e:                                    # noqa: BLE001
        log(f"polygon candidate list unavailable ({type(e).__name__}), "
            f"falling back to the built-in universe")
    got = list(dict.fromkeys(universe_static.US_CORE))[:cap]
    log(f"{len(got)} candidates from the built-in universe")
    return got


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--tickers", type=int, default=400,
                    help="how many names to test, after ranking")
    ap.add_argument("--candidates", type=int, default=800,
                    help="how many to fetch history for, before ranking")
    # Two years, because the search gets the first half and the
    # confirmation gets the second. One year would leave each half with
    # about sixty scoreable days: enough to run, too thin to believe.
    ap.add_argument("--span", default="2y",
                    help="how much history to ask for (1y, 2y, 5y)")
    ap.add_argument("--horizons", default="3,5,10")
    ap.add_argument("--seeds", type=int, default=300)
    ap.add_argument("--out", default="published/patterns.json")
    ap.add_argument("--history", default="/tmp/pattern_history.json")
    a = ap.parse_args()

    def log(m):
        print(m, flush=True)

    have = {}
    try:
        have = json.loads(Path(a.history).read_text())
        log(f"restored history for {len(have.get('raw') or {})} companies")
    except Exception:
        pass

    t0 = time.time()
    names = candidates(a.candidates, log)
    book = history.year_book(names, span=a.span, have=have, progress=log)
    Path(a.history).write_text(json.dumps(book))
    dates = book.get("dates") or []
    log(f"history: {len(book.get('series') or {})} companies x {len(dates)} "
        f"sessions ({time.time() - t0:.0f}s)")

    horizons = [int(x) for x in a.horizons.split(",") if x.strip()]
    # The binding constraint is distinct calendar DAYS, not observations:
    # 51 sessions of warm-up, the forward horizon, and MIN_DAYS of firing
    # on top. Below that every pattern is refused and the run is wasted.
    # ...and TWICE that, because the history is split: the shapes are
    # searched for in the first half and confirmed on the second, so each
    # half has to stand on its own.
    need = 2 * patterns.sessions_needed(max(horizons
                                            or [patterns.DEFAULT_HORIZON]))
    if len(dates) < need:
        log(f"only {len(dates)} sessions; {need} are needed for a horizon of "
            f"{max(horizons)} — {patterns.MIN_DAYS} distinct firing days after "
            f"51 of warm-up, in EACH half of a split history. Nothing "
            f"measurable — not publishing a result.")
        return 0

    # Ranked at the START of the window. Ranking on the last day would be
    # a universe chosen with knowledge of how the window turned out.
    ranked = history.universe_at_start(book, cap=a.tickers)
    tested = {t: book["series"][t] for t in ranked}
    log(f"testing {len(tested)} companies, ranked by dollar volume over the "
        f"first 20 sessions of the window")

    report = {"ran_at": time.time(), "sessions": len(dates),
              "from": dates[0], "to": dates[-1],
              "tickers": len(tested), "seeds": a.seeds,
              "cost_pct": patterns.ROUND_TRIP_COST_PCT,
              "min_days": patterns.MIN_DAYS,
              "universe": "ranked by dollar volume over the first 20 sessions "
                          "of the window, not the last",
              # The actual names, not just the count — published so the
              # survivorship-bias caveat below can be checked against what
              # this run actually tested instead of stated only in prose.
              # A name absent from this list at the window's start and
              # absent from a later run's list may simply be a company
              # that fell out of the volume ranking; credit.delisting_filing()
              # against each CIK is what turns "absent" into "confirmed
              # delisted" versus "still trades, just smaller now".
              "universe_tickers": sorted(tested.keys()),
              "horizons": {}}

    # Search the first half, confirm on the second. One correction across
    # every shape at every holding period within the search — doing it per
    # horizon would mean three holding periods bought three separate
    # lotteries at the price of one — and then a single pre-specified
    # re-test of each survivor on data it was not chosen on.
    all_res = patterns.sweep_with_holdout(tested, horizons, seeds=a.seeds,
                                          progress=log)
    for h in horizons:
        res = all_res[h]
        report["horizons"][str(h)] = res
        alive = [k for k, v in res.items() if v and v.get("survives")]
        tradeable = [k for k in alive
                     if (res[k].get("after_costs_pct") or -1) > 0]
        log(f"  horizon {h}: {len(res)} tested, {len(alive)} survived the "
            f"correction, {len(tradeable)} clear costs")

    total = sum(len(v) for v in report["horizons"].values())
    # `v` is None for a shape too rare to measure — those rows are kept in
    # the report on purpose, so the page can say so rather than hide them
    # Surviving the search is not enough to be called tradeable. It has to
    # have held up on the half of the history it was not chosen on.
    survivors = [(h, k, v) for h, rows in report["horizons"].items()
                 for k, v in rows.items()
                 if v and v.get("survives") and v.get("confirmed")
                 and (v.get("after_costs_pct") or -1) > 0]
    found_only = [(h, k, v) for h, rows in report["horizons"].items()
                  for k, v in rows.items()
                  if v and v.get("survives") and not v.get("confirmed")]
    report["found_but_unconfirmed"] = [
        {"horizon": h, "pattern": k, "edge_pct": v.get("edge_pct"),
         "p": v.get("p"), "holdout": v.get("holdout"),
         "note": v.get("holdout_note")} for h, k, v in found_only]
    report["total_tests"] = total
    report["tradeable"] = [{"horizon": h, "pattern": k, **v}
                           for h, k, v in survivors]

    out = Path(a.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report))
    log("")
    log("=" * 66)
    log(f"{total} pattern-horizon combinations tested over {len(dates)} "
        f"sessions and {len(tested)} companies")
    if survivors:
        for h, k, v in survivors:
            log(f"  CONFIRMED  {k} @ {h}d: {patterns.verdict(v)}")
    else:
        log("  NOTHING CONFIRMED. No pattern in the library beat a random")
        log("  stock that was moving just as much, on the same days, by more")
        log("  than costs — and then held up on the half of the history it")
        log("  was not chosen on. That is the expected result, and publishing")
        log("  it is the point.")
    for h, k, v in found_only:
        log(f"  found, NOT confirmed  {k} @ {h}d: "
            f"{v.get('edge_pct', 0):+.3f}% while it was being looked for; "
            f"{v.get('holdout_note') or 'no holdout'}")
    log("=" * 66)
    return 0


if __name__ == "__main__":
    sys.exit(main())
