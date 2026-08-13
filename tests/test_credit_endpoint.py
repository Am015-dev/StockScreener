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
# The deploy branch SHIPS published/*.json, so a CI checkout has real
# scan results on disk that the app adopts at import — overriding
# whatever this test set up. Point the lookup somewhere empty.
os.environ["PUBLISHED_DIR"] = os.path.join(TMP, "no_published")
os.makedirs(os.environ["PUBLISHED_DIR"], exist_ok=True)
sys.path.insert(0, str(ROOT))

import app as A

REAL_CIK_FOR = A._cik_for   # earlier sections stub this out


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
# The share-count age gate is measured against the real clock, so a
# fixture with an absolute date is a test that fails on a future Tuesday
# with nothing having changed. The date moves with the test.
import datetime as _dt
FILED = (_dt.date.today() - _dt.timedelta(days=20)).isoformat()
PERIOD = (_dt.date.today() - _dt.timedelta(days=45)).isoformat()
CIK = {t: i + 1 for i, t in enumerate(LEVERAGE)}
BY_CIK = {v: k for k, v in CIK.items()}
sec_calls = []


def fake_sec(url, timeout=15):
    sec_calls.append(url)
    cik = int(url.split("CIK")[1][:10])
    t = BY_CIK[cik]
    lev = LEVERAGE[t] * SHARES * PRICES[-1]
    if url.endswith("Liabilities.json"):
        return {"units": {"USD": [{"form": "10-Q", "end": PERIOD,
                                   "val": lev}]}}
    if url.endswith("LiabilitiesCurrent.json"):
        return {"units": {"USD": [{"form": "10-Q", "end": PERIOD,
                                   "val": lev * 0.4}]}}
    if url.endswith("EntityCommonStockSharesOutstanding.json"):
        return {"units": {"shares": [{"form": "10-Q", "end": FILED,
                                      "val": SHARES}]}}
    return {"units": {}}


A._sec_json = fake_sec
A._cik_for = lambda t, timeout=8.0: CIK.get((t or "").upper())
A._price_book = lambda fetch=False: {t: PRICES for t in LEVERAGE}
A._state["results"] = [{"ticker": t} for t in LEVERAGE]
# Split the twelve into two sectors of six — enough for the exact-SIC
# level to activate on its own (5 OTHER measured names, the same floor
# percentile() enforces everywhere else). Mocked here for the same reason
# _sec_json is: without it, every _credit_for() call would make a real,
# unmocked network request to resolve each fake CIK's identity.
SIC = {t: ("1000" if i < 6 else "2000") for i, t in enumerate(LEVERAGE)}
A._company_identity = lambda cik, timeout=10.0: {
    "sic": SIC.get(BY_CIK.get(cik), None), "sic_desc": None, "name": None}

client = A.app.test_client()


# ---- a cold cache still answers, it just cannot rank yet -------------
first = client.post("/credit", json={"ticker": "FFF"}).get_json()
assert first["dd"] is not None, first
assert first["percentile"] is None, \
    "with no other name measured there is no ranking to report"
assert first["peers_n"] == 0
print("cold cache: the distance is reported, the ranking honestly is not")

# ---- a live-fetched report carries its own share count -------------
# credit.history() needs the share count to re-solve the model at each
# past close. A report resolved through _credit_for's live SEC-fetch
# path (nothing published yet) fetched this exact number to price the
# report — equity = shares x last close — and then threw it away,
# silently switching the chart off for every ticker not already on a
# scan's board.
live_iii = A._credit_for("III")
assert live_iii["shares"] == SHARES, \
    f"the fetched share count must survive onto the report, got {live_iii.get('shares')}"
iii_page = client.get("/credit/III").get_data(as_text=True)
assert "<polyline" in iii_page, \
    "a live-fetched (not yet published) company must still get its history chart"
print("a live-fetched report carries its own share count, and the chart renders from it")

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

# ---- sector-relative standing rides alongside the whole-board one ----
# Six names share sic 1000, six share sic 2000 — enough for each to get a
# genuine exact-SIC comparison (5 other measured names) rather than a
# fallback to the whole board. A middling name (its OWN sector's median)
# must not read as the same percentile against the whole 12-name board,
# which mixes both sectors' leverage ranges together.
sector_of = {t: client.post("/credit", json={"ticker": t}).get_json()
            for t in LEVERAGE}
for t, d in sector_of.items():
    assert d["sector"] == SIC[t] and d["sector_level"] == "sic", \
        f"{t} should get a genuine exact-SIC comparison: {d}"
    assert d["sector_peers_n"] == 5, d          # 6 in the sector, 5 others
    assert d["sector_fallback"] is False, d
sector_1 = [t for t in LEVERAGE if SIC[t] == "1000"]
sector_1_page = client.get(f"/credit/{sector_1[2]}").get_data(as_text=True)
assert "the same industry" in sector_1_page or "a related part of the market" \
    in sector_1_page, "the sector comparison must say it is a sector comparison"
print(f"each name gets its own sector's percentile ({sector_of[sector_1[0]]['sector']}), "
      f"alongside — not instead of — the whole-board one")

# ---- the leverage/volatility decomposition moves the SAME direction ----
# checked WITHIN each sector separately, since leverage_percentile is a
# sector-relative figure — GGG carries more absolute debt than FFF but
# still reads as its OWN sector's safest, because sector 2000's scale
# resets independently of sector 1000's. A naive whole-board percentile()
# on leverage would also rank the MOST indebted name highest; this must
# not, in EITHER sector.
for sec in ("1000", "2000"):
    names = [t for t in LEVERAGE if SIC[t] == sec]
    lev_order = sorted(names, key=lambda t: LEVERAGE[t])         # safest first
    lev_pcts = [sector_of[t]["leverage_percentile"] for t in lev_order]
    assert all(v is not None for v in lev_pcts), lev_pcts
    assert lev_pcts == sorted(lev_pcts, reverse=True), \
        (f"less debt must read as a HIGHER leverage percentile within its "
         f"own sector, always: {list(zip(lev_order, lev_pcts))}")
    assert lev_pcts[0] == 100, "the least-levered name in its sector must be safest"
_lev_page = client.get(f"/credit/AAA").get_data(as_text=True)
assert "safer than" in _lev_page and "on how hard its" in _lev_page, \
    "the risk decomposition must be visible on the page, not just in the JSON"
print("leverage percentile reads 'higher = safer' consistently within each "
      "sector, and is on the page")

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
A._cik_for = lambda t, timeout=8.0: CIK.get((t or "").upper())

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
# The map has to be READABLE for absence from it to mean anything; an
# empty one means the lookup failed, which is a different answer.
A._cik_map.update(data={"AAPL": 320193}, ts=time.time())
A._cik_for = lambda t, timeout=8.0: None
foreign = client.post("/credit", json={"ticker": "NESN.SW"}).get_json()
assert foreign["dd"] is None and "US" in foreign["verdict"]
blank = client.post("/credit", json={"ticker": "  "})
assert blank.status_code == 400
print("a non-US filer and an empty ticker are refused, not guessed at")


# ---- a transient SEC failure must not become a permanent verdict ----
# The ticker->CIK map is refreshed weekly. On failure the old code stored
# an EMPTY map and stamped the timestamp as a success, so the refresh
# check would not retry for seven days and every report answered "Not a
# US filer — SEC XBRL covers US listings only". For Apple. And the
# startup warmer hits this path first, so one hiccup at boot poisoned
# every report the instance would ever serve.
A._cik_for = REAL_CIK_FOR
A._cik_map.update(data=None, ts=0.0)
_tries = {"n": 0}

TICKERS_JSON = {"fields": ["cik", "name", "ticker", "exchange"],
                "data": [[320193, "Apple Inc.", "AAPL", "Nasdaq"]]}


def flaky_tickers(url, timeout=15):
    _tries["n"] += 1
    if _tries["n"] == 1:
        raise RuntimeError("SEC unavailable")
    return TICKERS_JSON


A._sec_json = flaky_tickers

assert A._cik_for("AAPL") is None, "the first attempt genuinely fails"
assert A._cik_for("AAPL") == 320193, \
    "a failed fetch must be retried, not cached as 'not a US filer' for a week"
print("one SEC hiccup does not turn every company into a non-US filer")


# ---- a lookup that failed is not evidence about the company ----
# With the SEC refusing this address, Coca-Cola was reported as "Not a US
# filer — SEC XBRL covers US listings only". The ticker list simply could
# not be read. Those are different facts and only one is about the company.
A._cik_map.update(data={}, ts=time.time())
A._cik_for = lambda t, timeout=8.0: None
_unknown = A._credit_for("KO")
assert _unknown["dd"] is None
assert "could not be read" in _unknown["verdict"], _unknown["verdict"]
assert "not a US filer" not in _unknown["verdict"].lower(), _unknown["verdict"]

A._cik_map.update(data={"AAPL": 320193}, ts=time.time())
_foreign = A._credit_for("NESN.SW")
assert "Not a US filer" in _foreign["verdict"], _foreign["verdict"]
print("an unreadable ticker list says so; a ticker genuinely absent from a "
      "readable one is reported as not a US filer")

print("\nALL CREDIT-ENDPOINT TESTS PASSED")


# ---- the published credit book answers without touching the SEC ----
# The SEC rate-limits by IP and refuses the web host outright: every call
# from it times out, while the same request from another address answers
# in 0.3 seconds. So the reports are computed on the runner that already
# does the scan and published, exactly as the price and volatility books
# are. A reader must get a standing with no outbound call at all.
A._sec_json = lambda url, timeout=15: (_ for _ in ()).throw(
    AssertionError(f"the published book must be served without calling {url}"))
A._cik_for = lambda t, timeout=8.0: (_ for _ in ()).throw(
    AssertionError("the published book must not need a CIK lookup"))

PUBLISHED_CREDIT = {
    f"P{i:02d}": {"ticker": f"P{i:02d}", "dd": round(2.0 + i * 0.7, 2),
                  "band": "watch it" if i < 3 else "comfortable",
                  "market_leverage": 0.5 - i * 0.03, "as_of": PERIOD,
                  "vol_source": "published", "vol_obs": 250, "vol_thin": False,
                  "missing": []}
    for i in range(8)}
A._credit_book = lambda fetch=False: PUBLISHED_CREDIT

got = client.post("/credit", json={"ticker": "P05"}).get_json()
assert got["dd"] == PUBLISHED_CREDIT["P05"]["dd"], got
assert got["from_scan"] is True and got["ok"] is True
print("a published credit standing is served with zero SEC calls")

# and it must be RANKED against the published set, not against whatever
# happens to be cached — that ranking is the report's only free edge
assert got["peers_n"] == len(PUBLISHED_CREDIT) - 1, got["peers_n"]
assert got["percentile"] is not None
top = client.post("/credit", json={"ticker": "P07"}).get_json()
bottom = client.post("/credit", json={"ticker": "P00"}).get_json()
assert top["percentile"] == 100 and bottom["percentile"] == 0, \
    (top["percentile"], bottom["percentile"])
print(f"ranked against all {len(PUBLISHED_CREDIT)} published names: "
      f"strongest {top['percentile']}%, weakest {bottom['percentile']}%")

# a company the scan did not measure still falls through to a live lookup,
# rather than being reported as unmeasurable
A._credit_book = lambda fetch=False: {}
A._cik_for = lambda t, timeout=8.0: CIK.get((t or "").upper())
A._sec_json = fake_sec
A.cache_store.put("credit:AAA", None)
live = A._credit_for("AAA")
assert live["dd"] is not None and not live.get("from_scan")
print("a name the scan did not cover is still measured on demand")



# ---- a refusing SEC must cost nothing after the first few tries ----
# The page fires /credit on every check. With the SEC refusing this host,
# each one still spent its whole budget rediscovering that, so a dead
# upstream became sixteen seconds of held worker thread per click and the
# whole site slowed down. After a run of failures the answer is known.
A._credit_book = lambda fetch=False: {}
A._cik_for = lambda t, timeout=8.0: 1
A._sec_json = lambda url, timeout=15: (_ for _ in ()).throw(
    TimeoutError("SEC did not answer"))
A._sec_health.update(fails=0, until=0.0)
A._price_book = lambda fetch=False: {t: PRICES for t in LEVERAGE}

_slow = 0
for i in range(A.SEC_BREAK_AFTER):
    A.cache_store.put(f"credit:BRK{i}", None)
    _t0 = time.time()
    A._credit_for(f"BRK{i}", budget_s=4)
    _slow += 1 if time.time() - _t0 > 0.05 else 0

_t0 = time.time()
_after = A._credit_for("BRK9", budget_s=4)
_fast = time.time() - _t0
assert _fast < 0.05, f"the breaker did not open: {_fast:.2f}s"
assert "not answering this server" in _after["verdict"], _after["verdict"]
assert _after["dd"] is None
print(f"after {A.SEC_BREAK_AFTER} refusals the next call answers in "
      f"{_fast*1000:.0f}ms instead of burning its budget")

# the published board is unaffected — it needs no SEC at all
A._credit_book = lambda fetch=False: PUBLISHED_CREDIT
_still = A._credit_for("P05")
assert _still["dd"] == PUBLISHED_CREDIT["P05"]["dd"], _still
print("published standings keep answering while the SEC is shut out")

# and a working SEC clears it
A._sec_health.update(fails=0, until=0.0)
A._credit_book = lambda fetch=False: {}
A._sec_json = fake_sec
A._cik_for = lambda t, timeout=8.0: CIK.get((t or "").upper())
A.cache_store.put("credit:BBB", None)
assert A._credit_for("BBB")["dd"] is not None
print("a SEC that answers is used normally — the breaker only trips on failure")


# ---- the report page: a document, not a one-line verdict ----
# The ask was for something like the Moody's EDF report. A finding inside
# another feature is not that. The page must carry the metric, how it
# moved, what drives it, who it sits next to, and where every figure came
# from — and must never carry a default probability.
A._credit_book = lambda fetch=False: PUBLISHED_CREDIT
_prices = {"dates": [f"2026-06-{d:02d}" for d in range(1, 29)] +
                    [f"2026-07-{d:02d}" for d in range(1, 33)],
           "series": {"P05": [round(100 * (1 - 0.3 * i / 60), 2) for i in range(60)]}}
A._price_book = lambda fetch=False: _prices
PUBLISHED_CREDIT["P05"].update(shares=1e9, default_point=4e10, equity_vol=0.30,
                               equity=1e11, asset_vol=0.22, market_leverage=0.35,
                               as_of="2026-06-28", vol_obs=1180,
                               shares_as_of="2026-07-18", source="Liabilities",
                               name="Placeholder Five, Inc.",
                               sic_desc="Electronic Computers")
page = client.get("/credit/P05")
assert page.status_code == 200, page.status_code
html = page.get_data(as_text=True)
assert "Placeholder Five, Inc." in html and "Electronic Computers" in html, \
    "the company's name and sector are on the page, not just the ticker"

for want in ("P05", "standard deviations", "Where it has been",
             "What is driving it", "Nearest companies measured",
             "Where the figures came from", "<polyline",
             "Over a longer or shorter window"):
    assert want in html, f"the report is missing: {want}"
# the honest limit has to be ON the page, not in a docstring
assert "no percentage is quoted" in html
assert "0.000000%" in html, "the reason no probability is given must be concrete"

# ---- the multi-horizon table: distance only, never a percentage ----
_hi = html.find("Over a longer or shorter window")
assert _hi != -1, "the multi-horizon card did not render"
_hc = html[_hi:html.find('<div class="card">', _hi)]
assert "0.5" in _hc and ">5 years<" in _hc, \
    f"the 0.5 and 5 year horizons must both be on the page: {_hc[:300]}"
assert "%" not in _hc, "the multi-horizon table must never show a percentage"
print("the multi-horizon table shows distances at several windows, no percentage anywhere in it")
# and the held-constant caveat, because the line moves with price alone
assert "held at the" in html and "filing across this" in html
print("the report carries the metric, the history, the drivers, the peers "
      "and the provenance")

# a company it cannot measure gets a page that says so and claims nothing
A._credit_book = lambda fetch=False: {}
A._cik_for = lambda t, timeout=8.0: None
A._cik_map.update(data={"AAPL": 320193}, ts=time.time())
blank = client.get("/credit/NOPE").get_data(as_text=True)
assert "Not measured" in blank
assert "never as safe" in blank, \
    "an unmeasured company must be told apart from a safe one, on the page"
for banned in ("<polyline", "standard deviations —", "Over a longer or shorter window"):
    assert banned not in blank, f"an unmeasured company must not render {banned}"
print("an unmeasured company gets a page that states it, with no chart and no band")

print("\nPUBLISHED CREDIT BOOK PINNED")


# ---- the live path refuses financials too ----
# The runner refused them before publishing, but a financial OUTSIDE the
# published book fell through to the live SEC fetch and was fully
# modelled: Cigna's $55bn of claims payable read as debt coming due,
# with a band, a colour and a peer percentile. Same defect, other door.
_saved_identity = A._company_identity
_saved_cik = A._cik_for
_saved_book = A._credit_book
try:
    A._company_identity = lambda cik, timeout=10.0: {
        "sic": "6324", "sic_desc": "Hospital & Medical Service Plans",
        "name": "CIGNA GROUP"}
    A._cik_for = lambda t, timeout=8.0: 1739940
    A._credit_book = lambda fetch=False: {}
    A._creds.update(data={}, ts=0.0)
    A._sec_health.update(fails=0, until=0.0)
    rep = A._credit_for("CI")
    assert rep["dd"] is None and rep.get("not_modelled"), rep
    assert "not modelled" in (rep.get("verdict") or ""), rep
    assert rep["sic_desc"] == "Hospital & Medical Service Plans", rep
    assert rep["name"] == "CIGNA GROUP", rep
    print("a financial reached through the live path is refused, not modelled, "
          "and still carries its name and sector")

    # and the refusal is cached, so the next ask costs nothing
    A._company_identity = lambda cik, timeout=10.0: (
        _ for _ in ()).throw(AssertionError("hit the network"))
    rep2 = A._credit_for("CI")
    assert rep2.get("not_modelled"), rep2
    print("the refusal is cached like any other answer")
finally:
    A._company_identity = _saved_identity
    A._cik_for = _saved_cik
    A._credit_book = _saved_book

print("\nLIVE SECTOR GATE PINNED")


# ---- an IFRS filer (20-F) reached through the live path ----
_saved_sec = A._sec_json
_saved_cik2 = A._cik_for
_saved_identity2 = A._company_identity
_saved_book2 = A._credit_book
try:
    IFRS_CIK = 999001

    def ifrs_sec(url, timeout=15):
        if "companyfacts" in url:
            raise RuntimeError("companyfacts not needed for this fixture")
        if "ifrs-full/CurrentLiabilities.json" in url:
            return {"units": {"USD": [{"form": "20-F", "end": PERIOD, "val": 6e10}]}}
        if "ifrs-full/NoncurrentLiabilities.json" in url:
            return {"units": {"USD": [{"form": "20-F", "end": PERIOD, "val": 12e10}]}}
        if url.endswith("EntityCommonStockSharesOutstanding.json"):
            return {"units": {"shares": [{"form": "20-F", "end": FILED,
                                          "val": SHARES}]}}
        return {"units": {}}   # every us-gaap concept: empty

    A._sec_json = ifrs_sec
    A._cik_for = lambda t, timeout=8.0: IFRS_CIK
    A._company_identity = lambda cik, timeout=10.0: {
        "sic": "2911", "sic_desc": "Petroleum Refining", "name": "Shell-Shaped Co"}
    A._credit_book = lambda fetch=False: {}
    A._price_book = lambda fetch=False: {"SHLX": PRICES}
    A._creds.update(data={}, ts=0.0)
    A._sec_health.update(fails=0, until=0.0)
    rep = A._credit_for("SHLX")
    assert rep["dd"] is not None, rep
    assert rep["source"] == "ifrs-full: current + noncurrent", rep
    print(f"an IFRS filer reached live is measured via ifrs-full: {rep['source']}")

    # and a GBP-only IFRS filer refuses with the currency named, not the
    # generic "missing balance sheet"
    def ifrs_gbp_sec(url, timeout=15):
        if "companyfacts" in url:
            raise RuntimeError("companyfacts not needed for this fixture")
        if "ifrs-full/CurrentLiabilities.json" in url:
            return {"units": {"GBP": [{"form": "20-F", "end": PERIOD, "val": 6e10}]}}
        if "ifrs-full/Liabilities.json" in url:
            return {"units": {"GBP": [{"form": "20-F", "end": PERIOD, "val": 18e10}]}}
        if url.endswith("EntityCommonStockSharesOutstanding.json"):
            return {"units": {"shares": [{"form": "20-F", "end": FILED,
                                          "val": SHARES}]}}
        return {"units": {}}

    A._sec_json = ifrs_gbp_sec
    A._price_book = lambda fetch=False: {"BTIX": PRICES}
    A._creds.update(data={}, ts=0.0)
    rep_gbp = A._credit_for("BTIX")
    assert rep_gbp["dd"] is None, rep_gbp
    assert "GBP" in (rep_gbp.get("verdict") or ""), rep_gbp
    print(f"a GBP-only IFRS filer reached live refuses with the currency "
          f"named: {rep_gbp['verdict']}")
finally:
    A._sec_json = _saved_sec
    A._cik_for = _saved_cik2
    A._company_identity = _saved_identity2
    A._credit_book = _saved_book2
    A._price_book = lambda fetch=False: {t: PRICES for t in LEVERAGE}

print("\nIFRS LIVE PATH PINNED")
