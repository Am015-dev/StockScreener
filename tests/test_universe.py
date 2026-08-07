"""The scan universe must not be a 624-name hardcoded list.

Yahoo's screener API refuses datacenter IPs, so on Render the dynamic
universe never loaded and every scan fell back to a bundled list of 455 US
+ 169 EU names. The SEC publishes every US-listed registrant, ordered
largest first, in the same ticker format Yahoo uses. These tests pin the
parsing, the filtering, and the fallback chain — all offline.
"""
import json
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TMP = tempfile.mkdtemp(prefix="universe_")
os.environ["SCREENER_CACHE_DB"] = os.path.join(TMP, "cache.db")
os.environ["MARKET_DB"] = os.path.join(TMP, "market.db")
sys.path.insert(0, str(ROOT))

import cache_store
import screener
import universe_static

# ---- share-class vs preferred/warrant filtering ----
for keep in ("AAPL", "BRK-B", "BF-A", "MOG-A", "AKO-A"):
    assert screener._is_common_stock(keep), keep
for drop in ("CMS-PB", "ALL-PB", "SCE-PH", "XYZ-W", "ABC-U", "DEF-R"):
    assert not screener._is_common_stock(drop), drop
print("share-class filter OK: ordinary classes kept, preferred/warrants dropped")


class FakeResp:
    def __init__(self, payload, status=200):
        self._p, self.status_code = payload, status

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")

    def json(self):
        return self._p


PAYLOAD = {
    "fields": ["cik", "name", "ticker", "exchange"],
    "data": ([[i, f"Big Co {i}", f"BIG{i}", "Nasdaq"] for i in range(400)]
             + [[9000 + i, f"NYSE Co {i}", f"NYS{i}", "NYSE"] for i in range(300)]
             + [[1, "Berkshire", "BRK-B", "NYSE"],
                [2, "Preferred Co", "CMS-PB", "NYSE"],       # preferred: dropped
                [3, "Penny Co", "PNNY", "OTC"],              # OTC: dropped
                [4, "Nowhere Co", "NOWH", None],             # no exchange: dropped
                [5, "Dupe", "BIG0", "Nasdaq"]]),             # duplicate: dropped
}

calls = {"n": 0}


def fake_get(url, headers=None, timeout=None):
    calls["n"] += 1
    assert "sec.gov" in url, url
    ua = (headers or {}).get("User-Agent", "")
    # These are the SEC's real, measured rules. A User-Agent without a
    # contact address gets 403, and so does one whose address is on a
    # code-host domain — which is how this silently fell back to the
    # 624-name bundled list in production.
    assert "@" in ua, f"SEC requires a contact address, got {ua!r}"
    assert "github.com" not in ua, f"SEC refuses code-host addresses: {ua!r}"
    assert not ua.lower().startswith("mozilla"), f"SEC refuses browser UAs: {ua!r}"
    return FakeResp(PAYLOAD)


# the SHIPPED default must satisfy those rules, not just the test's stub
assert "@" in screener.SEC_UA, screener.SEC_UA
assert "github.com" not in screener.SEC_UA, screener.SEC_UA
assert not screener.SEC_UA.lower().startswith("mozilla"), screener.SEC_UA
print(f"shipped User-Agent satisfies the SEC's rules: {screener.SEC_UA!r}")


screener.requests.get = fake_get
syms = screener._sec_universe(progress=lambda m: None)

assert len(syms) == 701, len(syms)               # 400 + 300 + BRK-B
assert syms[0] == "BIG0" and "BRK-B" in syms
for bad in ("CMS-PB", "PNNY", "NOWH"):
    assert bad not in syms, bad
assert len(syms) == len(set(syms)), "duplicates must be removed"
assert syms.index("BIG0") < syms.index("NYS0"), "source order (largest first) kept"
print(f"SEC listing parsed: {len(syms)} tickers, order preserved, "
      f"OTC/preferred/duplicates excluded")

# ---- cached: a second call must not re-fetch ----
before = calls["n"]
again = screener._sec_universe(progress=lambda m: None)
assert calls["n"] == before, "the listing must be cached, not re-fetched per scan"
assert again == syms
print("listing cached on disk — one fetch per week, not one per scan")

# ---- a broken response must fall back, never poison the universe ----
cache_store.clear()
screener.requests.get = lambda *a, **k: FakeResp({"fields": [], "data": []})
assert screener._sec_universe(progress=lambda m: None) == [], \
    "an empty listing must yield nothing so the caller falls back"


def boom(*a, **k):
    raise ConnectionError("network down")


screener.requests.get = boom
assert screener._sec_universe(progress=lambda m: None) == []

# a truncated payload must be rejected rather than silently shrinking the scan
cache_store.clear()
screener.requests.get = lambda *a, **k: FakeResp(
    {"fields": ["cik", "name", "ticker", "exchange"],
     "data": [[1, "A", "AAA", "NYSE"]]})
assert screener._sec_universe(progress=lambda m: None) == [], \
    "a suspiciously short listing must be refused, not accepted"
print("failure modes OK: empty, unreachable and truncated listings all fall back")

# ---- stale-but-usable: a cached listing outlives a network outage ----
cache_store.clear()
screener.requests.get = fake_get
fresh = screener._sec_universe(progress=lambda m: None)
assert len(fresh) == 701
with cache_store._conn() as c:                     # a month old: past the
    c.execute("UPDATE kv SET ts = ts - ?", (30 * 86400,))   # 7-day TTL, still usable
screener.requests.get = boom
stale = screener._sec_universe(progress=lambda m: None)
assert stale == fresh, "a stale listing beats no universe at all"
print("stale listing still served when the SEC is unreachable")

# ---- the bundled list stays as the last resort, and is smaller ----
assert len(universe_static.US_CORE) < 700
assert len(screener.FALLBACK_UNIVERSE) > 20
print(f"bundled fallback still present ({len(universe_static.US_CORE)} US names) "
      f"but no longer the ceiling")

print("\nALL UNIVERSE TESTS PASSED")
