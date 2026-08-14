"""Weekly: fetch 13F holdings for a curated list of managers, map their
CUSIPs to tickers via the SEC's fails-to-deliver files, and publish the
roll-up as investors13f.json.

This is a SEPARATE workflow from scheduled_scan.py's hourly run, on
purpose. 13F data moves in bursts around the four ~45-day filing
deadlines (mid-Feb/May/Aug/Nov), not daily — a weekly cadence loses
nothing a reader would notice, and running it here means it never
competes with the daily scan's time budget. It also deliberately writes
ONLY investors13f.json, never index.json: two independently-scheduled
workflows both read-modify-write-force-push the same shared index file
would race each other on any week they happened to overlap, and nothing
in this feature needs a shared index entry to work — app.py reads
investors13f.json's own `as_of` field directly, the same way it already
reads liquidity.json's and vol.json's own metadata without an index.json
detour.
"""
import argparse
import json
import os
import re
import sys
import time

import requests

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import screener                                                  # noqa: E402
import superinvestors as si                                      # noqa: E402

FTD_INDEX_URL = "https://www.sec.gov/data-research/sec-markets-data/fails-deliver-data"


def _get_json(url: str, timeout: float = 20):
    r = requests.get(url, headers={"User-Agent": screener.SEC_UA}, timeout=timeout)
    if r.status_code != 200:
        raise RuntimeError(f"SEC {r.status_code}: {url}")
    return r.json()


def _get_text(url: str, timeout: float = 20):
    r = requests.get(url, headers={"User-Agent": screener.SEC_UA}, timeout=timeout)
    if r.status_code != 200:
        raise RuntimeError(f"SEC {r.status_code}: {url}")
    return r.text


def _get_bytes(url: str, timeout: float = 30):
    r = requests.get(url, headers={"User-Agent": screener.SEC_UA}, timeout=timeout)
    if r.status_code != 200:
        raise RuntimeError(f"SEC {r.status_code}: {url}")
    return r.content


def _latest_ftd_files(n: int = 3) -> list[bytes]:
    """The n most recent half-month fails-to-deliver zip files, newest
    first, as the index page lists them.

    One file alone maps most, not all, of a real filer's CUSIPs (~93%
    measured against Berkshire Hathaway's book); three consecutive
    half-months reached 100% in that same measurement — see
    superinvestors.cusip_map's docstring for the numbers.
    """
    r = requests.get(FTD_INDEX_URL, headers={"User-Agent": screener.SEC_UA},
                     timeout=20)
    r.raise_for_status()
    hrefs = re.findall(
        r'href="(/files/data/(?:other/)?fails-deliver-data/'
        r'cnsfails\d{6}[ab]\.zip)"', r.text)
    seen, ordered = set(), []
    for h in hrefs:
        if h not in seen:
            seen.add(h)
            ordered.append(h)
    return [_get_bytes(f"https://www.sec.gov{h}") for h in ordered[:n]]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="published")
    args = ap.parse_args()
    os.makedirs(args.out, exist_ok=True)

    print(f"fetching {len(si.MANAGERS)} managers' 13F filings...")
    results = []
    for i, mgr in enumerate(si.MANAGERS):
        try:
            res = si.fetch_manager(mgr, _get_json, _get_text)
        except Exception as e:                                  # noqa: BLE001
            res = {"cik": mgr["cik"], "name": mgr["name"],
                  "error": f"unexpected: {type(e).__name__}"}
        tag = f"[{i + 1}/{len(si.MANAGERS)}]"
        if "error" in res:
            print(f"  {tag} {mgr['name']}: {res['error']}")
        else:
            newest = res["filings"][0] if res["filings"] else {}
            n = len(newest.get("positions") or {})
            print(f"  {tag} {mgr['name']}: {n} positions as of "
                  f"{newest.get('period')}")
        results.append(res)
        time.sleep(0.12)          # SEC asks for 10/second

    ok = [r for r in results if "error" not in r]
    print(f"{len(ok)}/{len(si.MANAGERS)} managers fetched cleanly")

    print("fetching fails-to-deliver files for CUSIP -> ticker mapping...")
    mapping: dict = {}
    try:
        ftd_texts = [si.unzip_ftd(b) for b in _latest_ftd_files(3)]
        mapping = si.cusip_map(ftd_texts)
        print(f"  {len(mapping)} CUSIPs mapped from {len(ftd_texts)} files")
    except Exception as e:                                       # noqa: BLE001
        print(f"  FTD mapping failed ({type(e).__name__}: {e}) — "
              f"publishing by issuer name only this run")

    rolled = si.book(results, mapping)
    n_unmapped = sum(1 for k in rolled["tickers"] if k.startswith("(unmapped) "))
    print(f"{len(rolled['managers'])} managers, {len(rolled['tickers'])} "
          f"tickers ({n_unmapped} unmapped, shown by issuer name only)")

    payload = {
        "as_of": time.strftime("%Y-%m-%d", time.gmtime()),
        "n_managers_tracked": len(si.MANAGERS),
        "n_managers_ok": len(ok),
        "skipped": [{"name": r["name"], "cik": r["cik"], "reason": r["error"]}
                   for r in results if "error" in r],
        "managers": rolled["managers"],
        "tickers": rolled["tickers"],
    }
    out_path = os.path.join(args.out, "investors13f.json")
    with open(out_path, "w") as f:
        json.dump(payload, f)
    print(f"wrote {out_path}")


if __name__ == "__main__":
    main()
