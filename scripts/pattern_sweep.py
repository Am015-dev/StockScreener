"""Run the pattern library against real market history and publish it.

This runs on a GitHub Actions runner because the history it needs costs
one Polygon call per trading day at five calls a minute — half an hour
for a year, which is fine here and impossible in a web request.

It publishes `patterns.json` to the data branch whatever the outcome,
including — especially including — the outcome where nothing survives.
A discovery framework that only publishes when it finds something is a
machine for producing false positives, and this project has already
paid for one of those.
"""
import argparse
import datetime as dt
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import patterns                                                   # noqa: E402
import polygon_data as polygon                                    # noqa: E402


def trading_days_back(n: int) -> list:
    """Weekday dates, oldest first. Closed days answer empty and drop out."""
    out, d = [], dt.date.today()
    while len(out) < n and (dt.date.today() - d).days < n * 2 + 40:
        if d.weekday() < 5:
            out.append(d.isoformat())
        d -= dt.timedelta(days=1)
    out.reverse()
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--sessions", type=int, default=250,
                    help="trading days of history to test over")
    ap.add_argument("--tickers", type=int, default=500,
                    help="most-traded names to test on")
    ap.add_argument("--horizons", default="3,5,10")
    ap.add_argument("--seeds", type=int, default=300)
    ap.add_argument("--out", default="published/patterns.json")
    ap.add_argument("--history", default="/tmp/pattern_history.json")
    a = ap.parse_args()

    def log(m):
        print(m, flush=True)

    if not polygon.have_key():
        log("no POLYGON_API_KEY — nothing to sweep")
        return 0

    have = {}
    try:
        have = json.loads(Path(a.history).read_text())
        log(f"restored {len(have.get('series') or {})} series x "
            f"{len(have.get('dates') or [])} sessions of history")
    except Exception:
        pass

    want = trading_days_back(a.sessions)
    missing = [d for d in want if d not in (have.get("dates") or [])]
    log(f"{len(missing)} of {len(want)} sessions to fetch "
        f"(~{len(missing) * 12.5 / 60:.0f} min at the free-tier limit)")

    t0 = time.time()
    book = polygon.price_book(want, have=have, progress=log)
    Path(a.history).write_text(json.dumps(book))
    series = book.get("series") or {}
    dates = book.get("dates") or []
    horizons = [int(x) for x in a.horizons.split(",") if x.strip()]
    # The binding constraint is distinct calendar DAYS, not observations:
    # 51 sessions of warm-up, the forward horizon, and MIN_DAYS of firing
    # on top. Below that every pattern is refused and the run is wasted.
    need = patterns.sessions_needed(max(horizons or [patterns.DEFAULT_HORIZON]))
    if len(dates) < need:
        log(f"only {len(dates)} sessions; {need} are needed for a horizon of "
            f"{max(horizons)} ({patterns.MIN_DAYS} distinct firing days after "
            f"51 of warm-up). Nothing measurable — not publishing a result.")
        return 0

    # the most-traded names: a pattern that only appears in illiquid
    # tickers is not one anybody can act on
    last = {t: v[-1] for t, v in series.items() if v and v[-1]}
    bars = polygon.grouped_day(dates[-1], progress=log)
    common = polygon.common_stocks(progress=log)
    ranked = polygon.universe_by_liquidity(bars, common or None,
                                           cap=a.tickers, min_dollar_vol=5e6)
    tested = {t: series[t] for t in ranked if t in series}
    log(f"history: {len(tested)} tickers x {len(dates)} sessions "
        f"({time.time() - t0:.0f}s)")

    report = {"ran_at": time.time(), "sessions": len(dates),
              "from": dates[0], "to": dates[-1],
              "tickers": len(tested), "seeds": a.seeds,
              "cost_pct": patterns.ROUND_TRIP_COST_PCT,
              "min_days": patterns.MIN_DAYS,
              "horizons": {}}

    # One correction across every shape at every holding period. Doing it
    # per horizon would mean three holding periods bought three separate
    # lotteries at the price of one.
    all_res = patterns.sweep_many(tested, horizons, seeds=a.seeds, progress=log)
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
    survivors = [(h, k, v) for h, rows in report["horizons"].items()
                 for k, v in rows.items()
                 if v and v.get("survives")
                 and (v.get("after_costs_pct") or -1) > 0]
    report["total_tests"] = total
    report["tradeable"] = [{"horizon": h, "pattern": k, **v}
                           for h, k, v in survivors]

    out = Path(a.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report))
    log("")
    log("=" * 66)
    log(f"{total} pattern-horizon combinations tested over {len(dates)} "
        f"sessions and {len(tested)} tickers")
    if survivors:
        for h, k, v in survivors:
            log(f"  SURVIVED  {k} @ {h}d: {patterns.verdict(v)}")
    else:
        log("  NOTHING SURVIVED. No pattern in the library beat random entry")
        log("  on the same days by more than costs, after correcting for the")
        log("  number of patterns tried. That is the expected result, and")
        log("  publishing it is the point.")
    log("=" * 66)
    return 0


if __name__ == "__main__":
    sys.exit(main())
