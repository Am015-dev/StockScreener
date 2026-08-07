"""Run the preset filter sets and publish the results as JSON.

This runs on a GitHub Actions runner, not on the web instance. That matters
for three reasons:

  - the scan's cost (downloading and analysing thousands of stocks) stops
    competing with the 512MB web instance that has to serve pages;
  - every run gets a fresh runner IP, so Yahoo's per-IP throttling — which
    is what blinds fundamentals after a redeploy — resets each time;
  - visitors never trigger a scan. The site reads finished results, so a
    page load costs one file read instead of a five-minute download.

Output is one JSON file per preset, keyed by the same filter hash the app
uses for its stored scans, so the site can serve whichever preset matches
the filters a visitor is looking at.
"""
import argparse
import json
import os
import sys
import time
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import backtest
import db
import journal
import screener

# The presets the site publishes. "balanced" is the shipped default; the
# others exist because a single strict rule set often returns nothing on a
# strong tape, which reads as a broken site rather than an honest "no
# setups today".
PRESETS = {
    "balanced": {},
    "relaxed": {"min_rr": 2.2, "rsi_high": 62, "min_stop_atr": 0.7,
                "max_support_dist_pct": 12.0},
    "wide-net": {"min_rr": 2.0, "rsi_low": 25, "rsi_high": 68,
                 "min_stop_atr": 0.5, "max_support_dist_pct": 15.0,
                 "min_dollar_vol_m": 50.0, "strict_gates": False},
}

# Exit codes. The distinction matters: "Yahoo throttled this runner" is an
# expected, self-healing condition that recurs and must not page anyone,
# while "the code is broken" must. Reporting both as failure trains the
# reader to ignore the alert, which is worse than no alert.
EXIT_OK, EXIT_BROKEN, EXIT_UPSTREAM = 0, 1, 75

# Failures whose text matches these are the data source being unavailable,
# not a defect here.
# Seconds to wait before the single retry. Overridable so tests do not pay
# for it — a suite that sleeps is a suite that gets skipped.
RETRY_WAIT_S = float(os.environ.get("SCAN_RETRY_WAIT_S", "60"))

UPSTREAM_MARKERS = ("rate-limit", "rate limit", "too many requests",
                    "throttl", "every price download failed",
                    "could not download", "timed out", "connection",
                    "temporarily unavailable", "503", "502", "429")


def _is_upstream(err: str) -> bool:
    low = err.lower()
    return any(m in low for m in UPSTREAM_MARKERS)


PUBLISH_KEYS = ("results", "top_picks", "rejection_summary", "near_misses",
                "params_used", "near_board", "relax_hints", "pending",
                "breadth", "health", "universe_size", "scanned", "elapsed_s",
                "results_ts")


def simulate(p: dict, universe: list, progress) -> dict | None:
    """The 5-year simulation for these exact rules, published alongside the
    picks. Without it the analytics half of the page stays blank until a
    visitor presses a button — and on a fresh instance nobody ever has."""
    try:
        res = backtest.run_backtest(p, None, universe, progress=progress)
        if not res.get("n"):
            return None
        res.pop("curve", None)          # the portfolio curve is the one shown
        return res
    except Exception as e:
        progress(f"simulation skipped: {type(e).__name__}: {e}")
        return None


def run_preset(name: str, overrides: dict, universe_max: int,
               with_simulation: bool = True) -> dict:
    p = screener.clean_params(dict(overrides, universe_max=universe_max))
    log: list[str] = []

    def progress(m):
        line = f"[{name}] {m}"
        print(line, flush=True)
        log.append(str(m))

    t0 = time.time()
    res = screener.run_screener(p, progress=progress)
    df = res["df"]
    rows = df.astype(object).where(df.notna(), None).to_dict("records")

    # the track record only means anything if picks are logged as they are
    # published, so the scheduled run keeps it up to date too
    try:
        journal.record_picks(rows)
        journal.update_outcomes(lambda t: None)
    except Exception as e:
        progress(f"journal update skipped: {e}")

    payload = {
        "preset": name,
        "results": rows,
        "top_picks": rows[:3],
        "near_board": res.get("near") or [],
        "relax_hints": res.get("relax_hints") or {},
        "pending": res.get("pending") or [],
        "breadth": res.get("breadth"),
        "health": res.get("health"),
        "params_used": p,
        "universe_size": res.get("universe_size"),
        "scanned": res.get("universe_size"),
        "elapsed_s": round(time.time() - t0),
        "results_ts": time.time(),
        "rejection_summary": [
            {"reason": r, "count": c} for r, c in
            Counter(v.split(" (")[0]
                    for v in (res.get("rejections") or {}).values()).most_common(20)],
        "log": log[-40:],
        "scan_hash": db.scan_hash(p),
    }
    if with_simulation:
        bt = simulate(p, list(screener._cache.get("universe") or []), progress)
        if bt:
            payload["backtest"] = bt
            payload["bt_rules"] = {k: p.get(k) for k in db.TECH_PARAMS}
            pf = (bt.get("portfolio") or {}).get("profit_factor")
            progress(f"simulation: {bt['n']} trades, portfolio profit factor {pf}")
    return payload


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="published")
    ap.add_argument("--universe-max", type=int, default=1500)
    ap.add_argument("--presets", default="")
    ap.add_argument("--no-simulation", action="store_true",
                    help="publish picks only (much faster)")
    args = ap.parse_args()

    names = ([n.strip() for n in args.presets.split(",") if n.strip()]
             or list(PRESETS))
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)

    index, failures = [], []
    for name in names:
        if name not in PRESETS:
            print(f"unknown preset {name!r}, skipping", file=sys.stderr)
            continue
        payload, last_err = None, None
        for attempt in (1, 2):
            try:
                payload = run_preset(name, PRESETS[name], args.universe_max,
                                     with_simulation=not args.no_simulation)
                break
            except Exception as e:
                last_err = e
                if attempt == 1 and _is_upstream(f"{type(e).__name__}: {e}"):
                    print(f"[{name}] upstream failure, retrying once in "
                          f"{RETRY_WAIT_S:g}s: {e}", file=sys.stderr)
                    time.sleep(RETRY_WAIT_S)
                    continue
                break
        try:
            if payload is None:
                raise last_err
        except Exception as e:
            # one preset failing must not lose the others: publish what
            # worked and report the rest as failures rather than silently
            # shipping a partial index that looks complete
            print(f"[{name}] FAILED: {type(e).__name__}: {e}", file=sys.stderr)
            failures.append({"preset": name, "error": f"{type(e).__name__}: {e}"})
            continue
        (out / f"{name}.json").write_text(json.dumps(payload, default=str))
        index.append({"preset": name, "scan_hash": payload["scan_hash"],
                      "results_ts": payload["results_ts"],
                      "n_results": len(payload["results"]),
                      "universe_size": payload["universe_size"],
                      "has_simulation": bool(payload.get("backtest"))})
        print(f"[{name}] published {len(payload['results'])} picks "
              f"from {payload['universe_size']} stocks")

    (out / "index.json").write_text(json.dumps({
        "generated_at": time.time(), "presets": index, "failures": failures,
    }, default=str))

    if not index:
        upstream = failures and all(_is_upstream(f["error"]) for f in failures)
        if upstream:
            print("every preset failed because the data source was "
                  "unavailable — nothing published, and this is not a defect. "
                  "The next scheduled run will retry.", file=sys.stderr)
            return EXIT_UPSTREAM
        print("every preset failed — publishing nothing", file=sys.stderr)
        return EXIT_BROKEN
    return EXIT_OK


if __name__ == "__main__":
    raise SystemExit(main())
