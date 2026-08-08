"""Does the pullback signal carry any information at all?

Every number this project has published about its strategy answers the
wrong question. "Profit factor 0.62" says the rules lose money; it does
not say whether the RULES are the reason. A coin flip managed with the
same stop, the same target and the same holding period would also produce
a number, and if that number is the same, the pullback logic is
decoration and no amount of tuning will help.

So: a permutation test.

  Real  — enter on the pullback signal.
  Null  — enter on a coin flip, on the same stocks over the same days,
          then manage the trade with byte-for-byte identical code: same
          ATR stop, same target, same hold cap, same costs, same
          one-open-trade-per-ticker rule.

The null runs many times with different seeds, producing a distribution
of "what a monkey would have got". The real signal's expectancy is then
placed in that distribution. If it sits comfortably inside, the signal is
dead — not weak, dead — and the honest response is to stop mentioning it.

This is the test that should have been run before any of the tuning.
"""
import argparse
import json
import statistics
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import backtest
import screener


def _run(p, data, tickers, bench):
    trades: list[dict] = []
    scanned = backtest._simulate_block(p, data, tickers, trades, bench)
    return trades, scanned


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--universe-max", type=int, default=400)
    ap.add_argument("--seeds", type=int, default=25)
    ap.add_argument("--out", default="null_test.json")
    a = ap.parse_args()

    def log(m):
        print(m, flush=True)

    p = screener.clean_params({"universe_max": a.universe_max})
    log(f"universe: building up to {a.universe_max} tickers")
    universe = screener.build_universe(p, log)
    log(f"universe: {len(universe)} tickers")

    bench = backtest._benchmarks(log)
    data = backtest._fetch_chunk(universe, log)
    if data is None:
        log("no price data — cannot run the test")
        return 75
    have = [t for t in universe if t in getattr(data.columns, "levels", [[]])[0]]
    log(f"price history for {len(have)} tickers")

    # ---- the real signal ----
    t0 = time.time()
    real, scanned = _run(dict(p), data, have, bench)
    real_agg = backtest._aggregate(real, scanned)
    n_real = len(real)
    log(f"REAL: {n_real} trades over {scanned} stocks, "
        f"avg {real_agg['avg_r']:+.4f}R, win {real_agg['win_rate_pct']}%, "
        f"PF {real_agg['profit_factor']} [{time.time()-t0:.0f}s]")
    if n_real < 30:
        log("too few real trades to test against")
        return 1

    # ---- calibrate the coin so the null draws a comparable sample ----
    probe = dict(p, _null_rate=0.01, _null_seed=999)
    probe_trades, _ = _run(probe, data, have, bench)
    if not probe_trades:
        log("null probe produced no trades — cannot calibrate")
        return 1
    rate = max(1e-5, min(0.9, 0.01 * n_real / len(probe_trades)))
    log(f"null entry rate calibrated to {rate:.5f} "
        f"(probe gave {len(probe_trades)} at 0.01)")

    # ---- the monkeys ----
    null_avg, null_pf, null_wr, null_n = [], [], [], []
    for seed in range(a.seeds):
        tr, sc = _run(dict(p, _null_rate=rate, _null_seed=seed), data, have, bench)
        if len(tr) < 20:
            continue
        agg = backtest._aggregate(tr, sc)
        null_avg.append(agg["avg_r"])
        null_wr.append(agg["win_rate_pct"])
        if agg["profit_factor"] is not None:
            null_pf.append(agg["profit_factor"])
        null_n.append(len(tr))
        log(f"  null seed {seed:>2}: {len(tr):>5} trades  avg {agg['avg_r']:+.4f}R  "
            f"win {agg['win_rate_pct']}%  PF {agg['profit_factor']}")

    if len(null_avg) < 5:
        log("not enough null runs completed")
        return 1

    mean = statistics.mean(null_avg)
    sd = statistics.pstdev(null_avg) or 1e-9
    beat = sum(1 for x in null_avg if x >= real_agg["avg_r"])
    p_value = (beat + 1) / (len(null_avg) + 1)
    z = (real_agg["avg_r"] - mean) / sd

    verdict = ("the signal carries no information a coin flip does not"
               if p_value > 0.10 else
               "the signal beats random entry on this sample")

    log("")
    log("=" * 66)
    log(f"REAL signal     avg {real_agg['avg_r']:+.4f}R over {n_real} trades")
    log(f"RANDOM entry    avg {mean:+.4f}R  (sd {sd:.4f}, "
        f"{len(null_avg)} runs, ~{int(statistics.mean(null_n))} trades each)")
    log(f"z = {z:+.2f}    p = {p_value:.3f}    "
        f"({beat} of {len(null_avg)} coin flips did as well or better)")
    log(f"VERDICT: {verdict}")
    log("=" * 66)

    out = {
        "ran_at": time.time(),
        "universe": len(have), "scanned": scanned,
        "real": {"n": n_real, "avg_r": real_agg["avg_r"],
                 "win_rate_pct": real_agg["win_rate_pct"],
                 "profit_factor": real_agg["profit_factor"]},
        "null": {"runs": len(null_avg), "mean_avg_r": round(mean, 5),
                 "sd_avg_r": round(sd, 5),
                 "mean_win_rate": round(statistics.mean(null_wr), 1),
                 "mean_pf": round(statistics.mean(null_pf), 3) if null_pf else None,
                 "mean_trades": int(statistics.mean(null_n)),
                 "entry_rate": rate},
        "z": round(z, 3), "p_value": round(p_value, 4),
        "signal_alive": bool(p_value <= 0.10),
        "verdict": verdict,
    }
    Path(a.out).write_text(json.dumps(out, indent=1, default=str))
    log(f"written to {a.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
