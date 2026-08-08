"""The credit report as the site actually serves it.

tests/test_credit.py covers the model and the filings. This covers the
part that sits between them and the page, where two bugs already lived:

  - the peer ranking was stored inside each cached report. The first
    company measured has no peers, so its ranking was cached as
    "unavailable" and served that way for the next 24 hours — and since
    the ranking is the one number here a proprietary model has no
    advantage over, that quietly removed the best thing in the report.

  - nothing populated the cache except people asking. A ranking needs
    five other measured names, so it required five strangers to have
    looked up five other companies first and was absent in practice.

No network: the SEC fetch and the price book are both injected.
"""
import os
import sys
import time
import tempfile
import math
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TMP = tempfile.mkdtemp(prefix="credit_ep_")
os.environ["MARKET_DB"] = os.path.join(TMP, "market.db")
os.environ["JOURNAL_DB"] = os.path.join(TMP, "journal.db")
os.environ["SCREENER_CACHE_DB"] = os.path.join(TMP, "cache.db")
os.environ["RESULTS_CSV"] = os.path.join(TMP, "results.csv")
os.environ["SKIP_WARM"] = "1"
sys.path.insert(0, str(ROOT))

import app as A


# ---- a fake SEC and a fake price book -------------------------------
# Twelve companies with the same price path and the same balance sheet
# shape, differing only in leverage, so the expected ORDER of distances
# is known in advance and the ranking can be checked against it.
def path(p0=100.0, n=300, vol=0.22, seed=11):
    x, out, s = seed, [], vol / math.sqrt(252)
    for _ in range(n):
        x = (1103515245 * x + 12345) % (2 ** 31); u1 = (x + 1) / 2 ** 31
        x = (1103515245 * x + 12345) % (2 ** 31); u2 = (x + 1) / 2 ** 31
        p0 *= math.exp(s * math.sqrt(-2 * math.log(u1)) * math.cos(2 * math.pi * u2))
        out.append(round(p0, 4))
    return out


PRICES = path()
# ticker -> total liabilities, in units of the (fixed) market value
LEVERAGE = {"AAA": 0.05, "BBB": 0.10, "CCC": 0.20, "DDD": 0.30, "EEE": 0.40,
            "FFF": 0.55, "GGG": 0.70, "HHH": 0.90, "III": 1.20, "JJJ": 1.60,
            "KKK": 2.20, "LLL": 3.00}
SHARES = 1_000_000_000                     # x last close ~= 100bn market value
CIK = {t: i + 1 for i, t in enumerate(LEVERAGE)}
BY_CIK = {v: k for k, v in CIK.items()}
sec_calls = []


def fake_sec(url, timeout=15):
    sec_calls.append(url)
    cik = int(url.split("CIK")[1][:10])
    t = BY_CIK[cik]
    lev = LEVERAGE[t] * SHARES * PRICES[-1]
    if url.endswith("Liabilities.json"):
        return {"units": {"USD": [{"form": "10-Q", "end": "2026-06-30",
                                   "val": lev}]}}
    if url.endswith("LiabilitiesCurrent.json"):
        return {"units": {"USD": [{"form": "10-Q", "end": "2026-06-30",
                                   "val": lev * 0.4}]}}
    if url.endswith("EntityCommonStockSharesOutstanding.json"):
        return {"units": {"shares": [{"form": "10-Q", "end": "2026-07-31",
                                      "val": SHARES}]}}
    return {"units": {}}


A._sec_json = fake_sec
A._cik_for = lambda t: CIK.get((t or "").upper())
A._price_book = lambda fetch=False: {t: PRICES for t in LEVERAGE}
A._state["results"] = [{"ticker": t} for t in LEVERAGE]

client = A.app.test_client()


# ---- a cold cache still answers, it just cannot rank yet -------------
first = client.post("/credit", json={"ticker": "FFF"}).get_json()
assert first["dd"] is not None, first
assert first["percentile"] is None, \
    "with no other name measured there is no ranking to report"
assert first["peers_n"] == 0
print("cold cache: the distance is reported, the ranking honestly is not")

# ---- the warmer measures the board ----------------------------------
A._warm_credit()
assert len([u for u in sec_calls if "companyconcept" in u]) > 0
print(f"the warmer measured the board in {len(LEVERAGE)} companies' filings")

# ---- and now the FIRST name measured must rank, not stay unavailable -
again = client.post("/credit", json={"ticker": "FFF"}).get_json()
assert again["cached"] is True, "the second read must come from cache"
assert again["peers_n"] == len(LEVERAGE) - 1, again
assert again["percentile"] is not None, \
    "a ranking cached before its peers existed would be wrong forever"
assert again["dd"] == first["dd"], "the distance itself must not have moved"
print("the first company measured ranks correctly once its peers exist")

# ---- the ranking has to be a ranking, not a number-shaped decoration -
ranks = {}
for t in LEVERAGE:
    d = client.post("/credit", json={"ticker": t}).get_json()
    ranks[t] = (d["dd"], d["percentile"])

order = sorted(LEVERAGE, key=lambda t: LEVERAGE[t])          # least levered first
dds = [ranks[t][0] for t in order]
assert dds == sorted(dds, reverse=True), \
    f"more debt must mean less distance from default: {list(zip(order, dds))}"
assert ranks[order[0]][1] == 100 and ranks[order[-1]][1] == 0, ranks
print("more debt ranks lower, every time, across the whole board")

# ---- a name off the board is still measurable, just unranked ---------
A._state["results"] = []
solo = client.post("/credit", json={"ticker": "AAA"}).get_json()
assert solo["dd"] is not None and solo["percentile"] is None
print("a company that is not on today's board is measured but not ranked")

# ---- the published volatility is preferred over the 60-day window ----
# The price book carries 60 closes, which prices the shares but estimates
# an annualised volatility badly. The scan publishes one volatility per
# ticker from years of returns; the endpoint must use it when present.
A._state["results"] = [{"ticker": t} for t in LEVERAGE]
A.cache_store.put("credit:GGG", None)
window = A._credit_for("GGG")
assert window["vol_source"] == "price window", window

A._vol_book = lambda fetch=False: {"GGG": {"vol": 0.62, "obs": 1_240,
                                           "as_of": "2026-08-07"}}
A.cache_store.put("credit:GGG", None)
published = A._credit_for("GGG")
assert published["vol_source"] == "published", published
assert published["equity_vol"] == 0.62 and published["vol_obs"] == 1240
assert published["vol_thin"] is False
assert published["dd"] < window["dd"], \
    "a higher volatility must shorten the distance, not be quietly ignored"
print(f"the published volatility reaches the report: {window['dd']} on 60 closes "
      f"becomes {published['dd']} on 1,240 returns")

# a ticker absent from the published book still answers off the window
A.cache_store.put("credit:HHH", None)
missing = A._credit_for("HHH")
assert missing["dd"] is not None and missing["vol_source"] == "price window"
print("a ticker missing from the volatility book still answers, and says so")
A._vol_book = lambda fetch=False: {}

# ---- the report is bounded in total, not per SEC call ---------------
# One report makes up to eight sequential SEC calls. A 15-second timeout
# on each is a two-minute timeout on the report, which is what production
# did: Carnival held a worker thread for 120 seconds and returned nothing.
A._state["results"] = [{"ticker": t} for t in LEVERAGE]
A._cik_for = lambda t: CIK.get((t or "").upper())

slow_calls = []


def slow_sec(url, timeout=15):
    slow_calls.append((url, timeout))
    time.sleep(min(timeout, 1.5))            # every call crawls
    raise RuntimeError("SEC timed out")


A._sec_json = slow_sec
A.cache_store.put("credit:CCC", None)        # make sure it is not served warm
t0 = time.time()
slow = A._credit_for("CCC", budget_s=4.0)
elapsed = time.time() - t0
assert elapsed < 10, f"a 4s budget must bound the report, took {elapsed:.1f}s"
assert slow["dd"] is None
assert max(t for _, t in slow_calls) <= 8.0, \
    "no single call may be given more time than the whole report has"
print(f"a 4-second budget returns in {elapsed:.1f}s instead of running eight "
      f"timeouts back to back")

# and a timeout must not be worded, or cached, as a company that files nothing
assert "did not answer in time" in slow["verdict"], slow["verdict"]
hit, _ = A.cache_store.fetch("credit:CCC", A.CREDIT_TTL)
A._sec_json = fake_sec
retry = A._credit_for("CCC")
assert retry["dd"] is not None, \
    "a timed-out report must not be cached — the retry has to be able to work"
print("a timed-out report says so, and does not poison the cache for 24 hours")

# ---- refusals stay refusals -----------------------------------------
A._cik_for = lambda t: None
foreign = client.post("/credit", json={"ticker": "NESN.SW"}).get_json()
assert foreign["dd"] is None and "US" in foreign["verdict"]
blank = client.post("/credit", json={"ticker": "  "})
assert blank.status_code == 400
print("a non-US filer and an empty ticker are refused, not guessed at")

print("\nALL CREDIT-ENDPOINT TESTS PASSED")
