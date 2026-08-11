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
import pandas as _pd

# The presets the site publishes. "balanced" is the shipped default; the
# others exist because a single strict rule set often returns nothing on a
# strong tape, which reads as a broken site rather than an honest "no
# setups today".
PRESETS = {
    "balanced": {},
    # RSI 62 and a 12% entry distance were both outside the methodology —
    # the audit found exactly these rows on the live site (MEDP, GLEN, GSK
    # at RSI 62-67; STM, GFS, NOK 8-12% above support). Loosened where it
    # is legitimate to loosen — the reward:risk floor and the noise gate —
    # not where it changes what the tool claims to find.
    "relaxed": {"min_rr": 2.2, "rsi_high": 58, "min_stop_atr": 0.7,
                "max_support_dist_pct": 5.0},
    # "wide-net" previously set strict_gates False and pushed RSI to 68 and
    # the support distance to 15%, to stop the page looking empty. That was
    # the wrong trade twice over: it published picks whose earnings dates
    # were never verified (fail-open, the exact defect this project set out
    # to remove), and it listed momentum names in a tool that tells the
    # reader it finds pullbacks. Looser, but inside the methodology and
    # still fail-closed.
    "wide-net": {"min_rr": 2.0, "rsi_low": 25, "rsi_high": 58,
                 "min_stop_atr": 0.5,
                 "min_dollar_vol_m": 50.0,
                 "max_support_dist_pct": 5.0},
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
                "breadth", "concentration", "health", "universe_size",
                "scanned", "elapsed_s",
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


def volatility_book(max_tickers: int = 2000, min_obs: int = 120) -> dict:
    """Annualised volatility of daily log returns, over ALL history held.

    The price book carries 60 closes because 60 closes x 1,500 tickers is
    already a 600KB download for a 512MB instance, and it exists to answer
    correlation questions where a quarter is enough.

    The credit model is a different matter. Merton takes an equity
    volatility and KMV estimates it from a year of daily returns; a
    quarter estimates a 22% annualised volatility with roughly a +/-9%
    standard error, and that error goes straight into the distance to
    default. Four times the history would be four times the file — but
    the volatility itself is ONE NUMBER per ticker. The scan already has
    the full frame in memory, so it is computed here, once, and published
    as a few tens of kilobytes.

    Returned per ticker: the volatility, the number of returns behind it,
    and the last date, so the consumer can say how firm the figure is
    instead of implying they are all equally firm.
    """
    import math
    out: dict = {}
    try:
        data = screener._cache.get("ohlc")
        if data is None:
            return out
        available = set(getattr(data.columns, "levels", [[]])[0])
        ordered = [t for t in (screener._cache.get("universe") or [])
                   if t in available]
        for t in available:
            if t not in ordered:
                ordered.append(t)
        for t in ordered[:max_tickers]:
            try:
                closes = data[t]["Close"].dropna()
                c = [float(x) for x in closes.tolist() if x and float(x) > 0]
                if len(c) < min_obs + 1:
                    continue
                rets = [math.log(c[i] / c[i - 1]) for i in range(1, len(c))]
                n = len(rets)
                mean = sum(rets) / n
                var = sum((r - mean) ** 2 for r in rets) / (n - 1)
                sd = math.sqrt(var) * math.sqrt(252.0)
                if sd <= 1e-6:
                    continue          # a flat series is not a zero-risk stock
                last = closes.index[-1]
                out[t] = {"vol": round(sd, 5), "obs": n,
                          "as_of": str(getattr(last, "date", lambda: last)())}
            except Exception:
                continue
    except Exception:
        pass
    return out


# A published standing is only as fresh as the price inside it — the
# distance is a market value against a balance sheet — so an entry more
# than a couple of days old is not worth serving.
CREDIT_MAX_AGE_S = 2 * 86400


def credit_book(tickers, prices: dict, vols: dict, prev: dict | None = None,
                max_names: int = 120, budget_s: float = 420.0,
                now: float | None = None) -> dict:
    """Distance to Default, computed HERE and accumulated across runs.

    The web instance cannot do this. The SEC rate-limits by IP and it is
    refusing the Render host outright — every call from it times out,
    while the same request from another address answers in 0.3 seconds.
    That is the same reason the scan itself moved to a runner: Yahoo
    throttles per IP and a runner gets a fresh one. The rule was already
    written down for prices; it applies to filings for the same reason,
    and the site was quietly breaking it on every request.

    Measuring the whole liquid universe in one run would take a quarter of
    an hour of SEC calls, so each run refreshes what it can inside a
    budget — today's board first, then whatever has gone longest without
    being measured — and merges the rest forward from the last published
    book. Coverage grows; no single run is expensive; and nothing older
    than CREDIT_MAX_AGE_S is carried, because the price inside it has
    moved on.
    """
    import credit
    now = now or time.time()
    out: dict = {}
    for t, r in (prev or {}).items():
        if now - float(r.get("built") or 0) <= CREDIT_MAX_AGE_S:
            out[t] = r

    try:
        cik_of = _sec_ciks()
    except Exception as e:
        print(f"credit book: no CIK map ({type(e).__name__}) — keeping "
              f"{len(out)} carried forward", file=sys.stderr)
        return out

    series = (prices or {}).get("series") or {}
    board = [t.upper() for t in tickers]
    # the board first, then the least recently measured, then names never
    # measured at all — so coverage widens while nothing goes stale
    rest = [t for t in series if t.upper() not in board]
    rest.sort(key=lambda t: float((out.get(t) or {}).get("built") or 0))
    t0, misses, done = time.time(), 0, 0

    for t in board + rest:
        if done >= max_names or time.time() - t0 > budget_s:
            break
        cik = cik_of.get(t.upper())
        closes = [c for c in (series.get(t) or []) if c]
        if not cik or not closes:
            continue
        try:
            bs = credit.fetch_balance_sheet(cik, _sec_get)
            sh = credit.shares_outstanding(cik, _sec_get)
            equity = sh["shares"] * float(closes[-1]) if sh.get("shares") else None
            v = (vols or {}).get(t) or {}
            rep = credit.report(t, equity, closes,
                                bs["current_liabilities"], bs["total_liabilities"],
                                as_of=bs.get("as_of"),
                                vol=v.get("vol"), vol_obs=v.get("obs"))
            rep["source"] = bs.get("source")
            rep["shares"] = sh.get("shares")
            rep["shares_as_of"] = sh.get("as_of")
            rep["shares_tag"] = sh.get("tag")
            rep["built"] = now
            if rep.get("dd") is not None:
                out[t] = rep
                misses = 0
            else:
                misses += 1
            done += 1
        except Exception:
            misses += 1
        if misses >= 5:
            print(f"credit book: the SEC refused {misses} in a row after "
                  f"{done} — stopping", file=sys.stderr)
            break
        time.sleep(0.12)          # SEC asks for 10/second

    # the ranking only means something across the whole set, so it is
    # recomputed over everything carried plus everything measured
    dds = sorted(r["dd"] for r in out.values() if r.get("dd") is not None)
    for r in out.values():
        if r.get("dd") is None:
            continue
        below = sum(1 for d in dds if d < r["dd"])
        r["peers_n"] = len(dds) - 1
        r["percentile"] = (int(round(100.0 * below / (len(dds) - 1)))
                           if len(dds) >= 6 else None)
    print(f"credit book: measured {done} this run, {len(out)} carried in total")
    return out


def _sec_get(url: str, timeout: float = 20):
    import requests
    r = requests.get(url, headers={"User-Agent": screener.SEC_UA}, timeout=timeout)
    if r.status_code != 200:
        raise RuntimeError(f"SEC {r.status_code}")
    return r.json()


def _sec_ciks() -> dict:
    d = _sec_get(screener.SEC_TICKERS_URL, timeout=25)
    fields = [f.lower() for f in d.get("fields", [])]
    ti, ci = fields.index("ticker"), fields.index("cik")
    return {row[ti].upper(): int(row[ci]) for row in d.get("data", [])}


def price_book(max_tickers: int = 2000, days: int = 60) -> dict:
    """Last `days` closes per ticker, from the frame the scan already
    downloaded.

    This is what makes the pre-trade check free to run: the web instance
    answers "how correlated is this with what I hold" out of a published
    file instead of calling Yahoo per ticker, which is both rate-limited
    and impossible from a datacenter IP.

    The ordering matters and cost a live bug. The first version walked
    `data.columns.levels[0]`, which pandas returns SORTED, and truncated
    it — so a cap of 1,200 over a 1,500-name scan did not drop the 300
    least liquid stocks, it dropped everything after roughly the letter T.
    WM, WCN and XPO were all in the published board and all missing from
    the book, and the check answered "overlap could not be measured" for
    exactly the names at the end of the alphabet. The scan's own universe
    is ordered by size, so it is used when available and the cap now sits
    above the scanned universe rather than inside it.
    """
    out: dict = {}
    try:
        data = screener._cache.get("ohlc")
        if data is None:
            return {}
        available = set(getattr(data.columns, "levels", [[]])[0])
        ordered = [t for t in (screener._cache.get("universe") or [])
                   if t in available]
        for t in available:            # anything the universe list missed
            if t not in ordered:
                ordered.append(t)
        # ONE calendar, shared by every series. The previous version took
        # each ticker's own last 60 non-null closes, so column position
        # meant a different day for a stock that did not trade every day
        # the frame covers — and the consumer correlated by position. A
        # London listing skipping three UK holidays was enough to turn a
        # correlation of 1.00 into 0.49. Here the index is fixed first and
        # every ticker is sampled against it, nulls included, so position
        # IS the date by construction.
        idx = data.index[-days:]
        for t in ordered[:max_tickers]:
            try:
                closes = data[t]["Close"].reindex(idx)
                if int(closes.notna().sum()) < days // 2:
                    continue           # too gappy on this calendar to publish
                out[t] = [None if _pd.isna(x) else round(float(x), 2)
                          for x in closes]
            except Exception:
                continue
    except Exception:
        return {}
    if not out:
        return {}
    return {"dates": [str(getattr(d, "date", lambda: d)()) for d in idx],
            "series": out}


def _assert_publishable(name: str, p: dict) -> None:
    """Refuse to publish a rule set that would show unverified picks.

    A preset is a recommendation with this project's name on it. Shipping
    one that fails open is worse than shipping nothing, because the reader
    cannot tell the difference from the page."""
    if not p.get("strict_gates"):
        raise ValueError(
            f"preset {name!r} disables strict_gates — a published preset may "
            f"never show picks whose safety gates could not be verified")
    if p.get("methodology_clamped"):
        raise ValueError(
            f"preset {name!r} exceeds the methodology bounds "
            f"({'; '.join(p['methodology_clamped'])}) — a pullback screener "
            f"must not publish momentum entries")


def run_preset(name: str, overrides: dict, universe_max: int,
               with_simulation: bool = True) -> dict:
    p = screener.clean_params(dict(overrides, universe_max=universe_max))
    _assert_publishable(name, p)
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
    journal_rows = []
    try:
        journal.record_picks(rows)
        journal.update_outcomes(lambda t: None)
        # Publish the log itself, not just today's picks. Render wipes the
        # disk on every deploy, so a track record that lives only on the
        # web instance is empty within hours of being written — the live
        # site was showing "0 picks recorded" while the runner held a full
        # history. Shipping it with the results makes it survive.
        # Capped: this ships inside every preset file, so an uncapped export
        # would grow the payload without bound (~270 bytes a row, three
        # presets). The most recent 1,500 is roughly a year of daily picks
        # and keeps each file well under a megabyte. The runner's own
        # journal.db keeps the complete history and round-trips through the
        # data branch, so nothing is lost — only what travels is trimmed.
        journal_rows = journal.export_all()[-1500:]
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
        "concentration": res.get("concentration"),
        "journal_rows": journal_rows,
        "journal": journal.snapshot() if journal_rows else None,
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

    # The price book: 60 daily closes per scanned ticker, written once so
    # the web instance can answer "how correlated is this with what I
    # already hold" without a single Yahoo call. Per-ticker requests are
    # rate-limited and fail outright from a datacenter IP, which is why
    # the check could not exist without this file.
    book = {}
    if index:
        try:
            book = price_book()
            if book:
                (out / "prices.json").write_text(json.dumps(book))
                kb = (out / "prices.json").stat().st_size / 1024
                print(f"published price book: {len(book['series'])} tickers x "
                      f"{len(book['dates'])} shared dates ({kb:.0f} KB)")
        except Exception as e:
            print(f"price book skipped: {type(e).__name__}: {e}", file=sys.stderr)

    # One volatility per ticker, from all the history the scan holds — the
    # credit model's distance is only as firm as this number, and 60
    # closes is not enough to make it firm.
    vols = {}
    if index:
        try:
            vols = volatility_book()
            if vols:
                (out / "vol.json").write_text(json.dumps(vols))
                kb = (out / "vol.json").stat().st_size / 1024
                med = sorted(v["obs"] for v in vols.values())[len(vols) // 2]
                print(f"published volatility book: {len(vols)} tickers, "
                      f"median {med} returns each ({kb:.0f} KB)")
        except Exception as e:
            print(f"volatility book skipped: {type(e).__name__}: {e}",
                  file=sys.stderr)

    # Credit standings for the board, built here because the SEC refuses
    # the web instance's address outright.
    creds = {}
    if index and book:
        try:
            board = []
            for pre in index:
                try:
                    payload = json.loads((out / f"{pre['preset']}.json").read_text())
                except Exception:
                    continue
                for r in (payload.get("results") or []):
                    t = (r.get("ticker") or "").upper()
                    if t and t not in board:
                        board.append(t)
            prev = {}
            try:
                prev = json.loads(Path("/tmp/prev_credit.json").read_text())
            except Exception:
                pass
            creds = credit_book(board, book, vols, prev=prev)
            if creds:
                (out / "credit.json").write_text(json.dumps(creds))
                kb = (out / "credit.json").stat().st_size / 1024
                print(f"published credit book: {len(creds)} of {len(board)} "
                      f"board names measured ({kb:.0f} KB)")
        except Exception as e:
            print(f"credit book skipped: {type(e).__name__}: {e}", file=sys.stderr)

    (out / "index.json").write_text(json.dumps({
        "generated_at": time.time(), "presets": index, "failures": failures,
        "price_book": ({"n": len(book["series"]), "days": 60}
                       if book else None),
        "vol_book": {"n": len(vols)} if vols else None,
        "credit_book": {"n": len(creds)} if creds else None,
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
