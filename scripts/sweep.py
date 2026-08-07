"""Run several rule sets through the 5-year simulation and compare them.

This belongs on a CI runner rather than the web instance. Each unseen
configuration needs its own five-year download, which takes minutes on a
512MB box that is also trying to serve pages — a five-config sweep simply
does not finish there.

Output is a markdown table (for the job summary) plus JSON, so the verdict
against the tradeable bar is visible without reading a log.
"""
import argparse
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import backtest
import screener

# Each entry isolates ONE change from the entry above it, so the table
# attributes the effect rather than showing a pile of different rule sets.
GATES = {"require_market_uptrend": True, "min_rs_3m": 0.001}
CONFIGS = {
    "0-raw":            {"cost_pct": 0, "require_market_uptrend": False,
                         "min_rs_3m": 0},
    "1-gates":          dict(GATES, cost_pct=0),
    "2-costs":          dict(GATES, cost_pct=0.20),
    "3-atr-stop":       dict(GATES, cost_pct=0.20, stop_mode="atr",
                             stop_atr_mult=1.5),
    "4-15bar-exit":     dict(GATES, cost_pct=0.20, stop_mode="atr",
                             stop_atr_mult=1.5, max_hold_bars=15),
    "5-liquidity":      dict(GATES, cost_pct=0.20, stop_mode="atr",
                             stop_atr_mult=1.5, max_hold_bars=15,
                             min_price=5.0, min_share_vol=500000),
}

# The bar this has to clear before the strategy is worth trading at all.
PF_BAR, SORTINO_BAR = 1.5, 1.0


def verdict(m: dict, spy_mdd) -> tuple[bool, str]:
    pf = (m.get("portfolio") or {}).get("profit_factor")
    mdd = (m.get("portfolio") or {}).get("mdd_pct")
    sortino = (m.get("portfolio") or {}).get("sortino")
    fails = []
    if pf is None or pf < PF_BAR:
        fails.append(f"PF {pf} < {PF_BAR}")
    if mdd is not None and spy_mdd is not None and mdd > spy_mdd:
        fails.append(f"drawdown -{mdd}% worse than SPY -{spy_mdd}%")
    if sortino is None or sortino < SORTINO_BAR:
        fails.append(f"Sortino {sortino} < {SORTINO_BAR}")
    return (not fails), "; ".join(fails)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--universe-max", type=int, default=1200)
    ap.add_argument("--configs", default="")
    ap.add_argument("--out", default="published/sweep.json")
    args = ap.parse_args()

    names = ([n.strip() for n in args.configs.split(",") if n.strip()]
             or list(CONFIGS))

    # one universe for every configuration, so the comparison is like-for-like
    base = screener.clean_params({"universe_max": args.universe_max})
    universe = screener.build_universe(base, progress=print)
    print(f"universe: {len(universe)} tickers", flush=True)

    rows = []
    for name in names:
        if name not in CONFIGS:
            print(f"unknown config {name!r}", file=sys.stderr)
            continue
        p = screener.clean_params(dict(CONFIGS[name],
                                       universe_max=args.universe_max))
        t0 = time.time()
        try:
            res = backtest.run_backtest(p, None, universe, progress=print)
        except Exception as e:
            print(f"[{name}] FAILED: {type(e).__name__}: {e}", file=sys.stderr)
            rows.append({"config": name, "error": f"{type(e).__name__}: {e}"})
            continue
        pf_view = res.get("portfolio") or {}
        spy = res.get("spy") or {}
        ok, why = verdict(res, spy.get("mdd_pct"))
        rows.append({
            "config": name, "rules": CONFIGS[name],
            "trades": res.get("n"), "took": pf_view.get("n"),
            "profit_factor": pf_view.get("profit_factor"),
            "win_rate_pct": pf_view.get("win_rate_pct"),
            "mdd_pct": pf_view.get("mdd_pct"),
            "sortino": pf_view.get("sortino"),
            "return_pct": pf_view.get("return_pct"),
            "spy_return_pct": spy.get("return_pct"),
            "spy_mdd_pct": spy.get("mdd_pct"),
            "clears_bar": ok, "fails": why,
            "seconds": round(time.time() - t0),
        })
        print(f"[{name}] PF={pf_view.get('profit_factor')} "
              f"win={pf_view.get('win_rate_pct')}% "
              f"MDD=-{pf_view.get('mdd_pct')}% "
              f"ret={pf_view.get('return_pct')}% "
              f"{'CLEARS' if ok else 'FAILS'}", flush=True)

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({"generated_at": time.time(),
                               "universe_size": len(universe),
                               "pf_bar": PF_BAR, "sortino_bar": SORTINO_BAR,
                               "results": rows}, default=str))

    # markdown for the job summary — the verdict should be readable without
    # opening a log
    md = ["| config | trades | taken | PF | win | max DD | Sortino | return | vs SPY | verdict |",
          "|---|---|---|---|---|---|---|---|---|---|"]
    for r in rows:
        if r.get("error"):
            md.append(f"| {r['config']} | — | — | — | — | — | — | — | — | ⚠ {r['error']} |")
            continue
        md.append(
            f"| {r['config']} | {r['trades']} | {r['took']} | "
            f"**{r['profit_factor']}** | {r['win_rate_pct']}% | "
            f"-{r['mdd_pct']}% | {r['sortino']} | {r['return_pct']}% | "
            f"{r['spy_return_pct']}% / -{r['spy_mdd_pct']}% | "
            f"{'✅ clears' if r['clears_bar'] else '❌ ' + r['fails']} |")
    table = "\n".join(md)
    print("\n" + table)
    Path(args.out).with_suffix(".md").write_text(table)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
