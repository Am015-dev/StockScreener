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

PUBLISH_KEYS = ("results", "top_picks", "rejection_summary", "near_misses",
                "params_used", "near_board", "relax_hints", "pending",
                "breadth", "health", "universe_size", "scanned", "elapsed_s",
                "results_ts")


def run_preset(name: str, overrides: dict, universe_max: int) -> dict:
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
    return payload


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="published")
    ap.add_argument("--universe-max", type=int, default=1500)
    ap.add_argument("--presets", default="")
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
        try:
            payload = run_preset(name, PRESETS[name], args.universe_max)
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
                      "universe_size": payload["universe_size"]})
        print(f"[{name}] published {len(payload['results'])} picks "
              f"from {payload['universe_size']} stocks")

    (out / "index.json").write_text(json.dumps({
        "generated_at": time.time(), "presets": index, "failures": failures,
    }, default=str))

    if not index:
        print("every preset failed — publishing nothing", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
