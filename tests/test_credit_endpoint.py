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


def fake_sec(url):
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

# ---- refusals stay refusals -----------------------------------------
A._cik_for = lambda t: None
foreign = client.post("/credit", json={"ticker": "NESN.SW"}).get_json()
assert foreign["dd"] is None and "US" in foreign["verdict"]
blank = client.post("/credit", json={"ticker": "  "})
assert blank.status_code == 400
print("a non-US filer and an empty ticker are refused, not guessed at")

print("\nALL CREDIT-ENDPOINT TESTS PASSED")
