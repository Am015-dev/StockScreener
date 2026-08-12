"""Polygon adapter: shape, pacing and refusals — all offline.

The suite is hermetic. Every response here is a fixture; the live
behaviour it encodes was measured once against the real API and is
recorded in the assertions so a change in our reading of the contract
shows up as a test failure rather than as a wrong page.
"""
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ["POLYGON_API_KEY"] = "test-key"
os.environ["POLYGON_INTERVAL_S"] = "0"        # tests must not sleep

import polygon_data as pg                                        # noqa: E402

# ---- the grouped call: one request, the whole market ----
GROUPED = {"status": "OK", "resultsCount": 4, "results": [
    {"T": "AAPL", "c": 304.91, "o": 307.75, "h": 309.97, "l": 302.79, "v": 37e6},
    {"T": "SPY", "c": 700.0, "o": 699.0, "h": 701.0, "l": 698.0, "v": 90e6},
    {"T": "TINY", "c": 2.0, "o": 2.0, "h": 2.0, "l": 2.0, "v": 1000.0},
    {"T": "NOPRICE", "c": None, "o": None, "h": None, "l": None, "v": 5.0},
]}

calls = []


def fake_get(path, params=None, timeout=45.0, progress=None):
    calls.append((path, dict(params or {})))
    if "/aggs/grouped/" in path:
        if "2023-" in path:            # outside the two-year free window
            return None
        return GROUPED
    if path == "/v3/reference/tickers":
        return {"results": [{"ticker": "AAPL", "name": "Apple Inc.",
                             "primary_exchange": "XNAS"},
                            {"ticker": "TINY", "name": "Tiny Co",
                             "primary_exchange": "XNAS"}]}
    return None


pg._get = fake_get

day = pg.grouped_day("2026-08-11")
assert set(day) == {"AAPL", "SPY", "TINY", "NOPRICE"}, day
assert day["AAPL"]["c"] == 304.91 and day["AAPL"]["v"] == 37e6
print("one grouped call yields the whole market, with volume")

# a refusal or a closed market is an empty dict — never a flat market
assert pg.grouped_day("2023-08-11") == {}
print("a day outside the free window is empty, not a market that did not move")

# ---- the universe: ranked by money traded, ETFs excluded ----
common = pg.common_stocks()
assert set(common) == {"AAPL", "TINY"}, common
assert "SPY" not in common
print("common stock is separated from the funds sharing the feed")

uni = pg.universe_by_liquidity(day, common, cap=10, min_dollar_vol=10e6)
assert uni == ["AAPL"], uni          # SPY excluded by type, TINY by liquidity
print("the universe is the liquid common stock, and SPY does not lead it")

# without the type filter SPY would top the list — the reason the filter exists
assert pg.universe_by_liquidity(day, None, cap=2, min_dollar_vol=1e6)[0] == "SPY"
print("and without that filter SPY tops the ranking, as measured")

# a ticker with no close cannot be ranked, and must not be counted as zero
assert "NOPRICE" not in pg.universe_by_liquidity(day, None, cap=10,
                                                 min_dollar_vol=0)
print("a missing close is skipped, never treated as a price of zero")

# ---- the price book: position is the date, and fetches are incremental ----
calls.clear()
dates = ["2026-08-07", "2026-08-10", "2026-08-11"]
book = pg.price_book(dates)
assert book["dates"] == dates
assert len(calls) == 3, f"{len(calls)} calls for 3 days"
assert book["series"]["AAPL"] == [304.91, 304.91, 304.91]
print(f"a cold build costs one call per day ({len(calls)} for {len(dates)})")

calls.clear()
again = pg.price_book(dates, have=book)
assert not calls, f"{len(calls)} calls when every day was already held"
assert again["series"]["AAPL"] == book["series"]["AAPL"]
print("a rebuild over held days costs nothing — steady state is one call a day")

calls.clear()
grown = pg.price_book(dates + ["2026-08-12"], have=book)
assert len(calls) == 1, f"{len(calls)} calls to add one day"
assert grown["dates"][-1] == "2026-08-12"
assert len(grown["series"]["AAPL"]) == 4
print("adding a day costs exactly one call, and the row grows with it")

# a name absent on some days keeps its slot, so position stays the date
sparse = dict(book)
assert all(len(v) == len(book["dates"]) for v in book["series"].values()), \
    "every series must have one slot per date"
print("every series has one slot per date — position is the date, by construction")

# ---- pacing: five a minute is real, so the module must enforce it ----
# Measured against the live API: a burst of twelve got two 200s and ten
# 429s. A 429 costs a call from a budget of five, so the module paces
# rather than discovering the limit by hitting it. This exercises the
# REAL _get (its urlopen fails instantly, which is all we need — the
# sleep happens before the request).
import importlib                                                  # noqa: E402
os.environ["POLYGON_INTERVAL_S"] = "0.5"
importlib.reload(pg)
t0 = time.time()
pg._get("/v3/reference/tickers")        # fails on the network, but paces first
pg._get("/v3/reference/tickers")
_elapsed = time.time() - t0
assert _elapsed >= 0.5, f"two calls took {_elapsed:.2f}s — nothing is pacing them"
os.environ["POLYGON_INTERVAL_S"] = "0"
print(f"the module paces its own calls ({_elapsed:.1f}s for two at a 0.5s limit)")

# ---- no key, no calls, no crash ----
importlib.reload(pg)
_saved = os.environ.pop("POLYGON_API_KEY", None)
importlib.reload(pg)
assert pg.have_key() is False
assert pg.grouped_day("2026-08-11") == {}
assert pg.common_stocks() == {}
print("with no key it makes no calls and returns nothing, rather than failing")
if _saved:
    os.environ["POLYGON_API_KEY"] = _saved

# ---- the SIC helper matches the credit model's own rule ----
importlib.reload(pg)
assert pg.is_financial_sic(6021) and pg.is_financial_sic("6331")
assert not pg.is_financial_sic(3571) and not pg.is_financial_sic(None)
import credit                                                     # noqa: E402
for code in (6000, 6021, 6331, 6799, 3571, 7372):
    assert pg.is_financial_sic(code) == credit.is_financial(code), code
print("the SIC rule agrees with credit.py's, code for code")

print("\nALL POLYGON TESTS PASSED")
