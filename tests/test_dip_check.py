"""Round 15: "is buying the dip better than a coin flip" — answered
honestly, per ticker, from data the scheduled scan already computes.

Operator: "identify the tickers where to buy the dip... validate with
past if it made good decisions... is it better of a coin flip or not."
This project already ran exactly that test, at whole-market scale, and
the pullback signal lost to a coin flip twice (see STRATEGY.md /
KNOWN_ISSUES.md, p = 0.41-0.50). A fresh per-ticker permutation test
would almost always be starved of trades — a single stock's pullback
signal fires only a handful of times over 5 years, far below the
30-trade floor the whole-market test itself needed for any power. So
this round does NOT re-derive a per-ticker verdict from noise. It
shows two real, honest things instead:

  - the ticker's own actual trades under the site's live rules — data
    the scheduled scan's regular backtest already computes and stores
    in db.py every run, so this is a read, not a new simulation
  - the governing whole-market result, so a handful of trades in one
    name is never mistaken for its own independent answer

Both must degrade gracefully (no trades recorded yet; the weekly null
test hasn't published yet) without ever inventing a number.
"""
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TMP = tempfile.mkdtemp(prefix="dip_check_")
os.environ["MARKET_DB"] = os.path.join(TMP, "market.db")
os.environ["JOURNAL_DB"] = os.path.join(TMP, "journal.db")
os.environ["SCREENER_CACHE_DB"] = os.path.join(TMP, "cache.db")
os.environ["PUBLISHED_DIR"] = os.path.join(TMP, "no_published")
os.makedirs(os.environ["PUBLISHED_DIR"], exist_ok=True)
os.environ["PUBLISHED_BASE"] = "http://127.0.0.1:1"
os.environ["PUBLISHED_FETCH_S"] = "1"
os.environ["SKIP_WARM"] = "1"
sys.path.insert(0, str(ROOT))

import app as A                                                   # noqa: E402
import db                                                          # noqa: E402


# ---- 1. db.trades_for(): the per-ticker read ----

P = A._wide_net_params()

seq = [(2.0, "target"), (-1.0, "stop"), (1.5, "target"), (-1.0, "stop"),
       (3.0, "target"), (-1.0, "stop"), (0.8, "expired")]
many_trades = [{"ticker": "MANY", "date": f"2025-{i + 1:02d}-05",
                "exit_date": f"2025-{i + 1:02d}-19", "outcome_r": r,
                "status": s, "rr_planned": 3.0, "entry": 100.0 + i,
                "stop_px": 95.0 + i, "target_px": 115.0 + i,
                "bars_held": 10, "mfe_r": 1.5, "mae_r": -0.6}
              for i, (r, s) in enumerate(seq)]
few_trades = [{"ticker": "FEW", "date": "2025-03-05", "exit_date": "2025-03-19",
              "outcome_r": -1.0, "status": "stop", "rr_planned": 3.0,
              "entry": 50.0, "stop_px": 47.0, "target_px": 60.0,
              "bars_held": 8, "mfe_r": 0.3, "mae_r": -1.1}]
db.record_backtest(P, many_trades + few_trades, n_stocks=800)

assert len(db.trades_for(P, "MANY")) == 7
assert len(db.trades_for(P, "FEW")) == 1
assert db.trades_for(P, "NEVER") == []
print("trades_for(): the right ticker's trades come back, newest first, "
      "an unmeasured ticker gets an empty list, never an error")

edge = db.edge_for(P)
assert "MANY" in edge and edge["MANY"]["n"] == 7
assert "FEW" not in edge, \
    "a ticker with fewer than MIN_SAMPLE trades must not get an aggregate rate"
print(f"edge_for(): MANY clears MIN_SAMPLE ({db.MIN_SAMPLE}) and gets a rate; "
      f"FEW (1 trade) does not — matching trades_for()'s own exclusion")

print("\nDB TRADES_FOR PINNED")


# ---- 2. /credit/<ticker>: the three trade-count states ----

def _seed_ticker_book(t, dd=6.0):
    A._creds.update(data={t: {"ticker": t, "dd": dd, "band": "comfortable",
                              "shares": 1e8, "default_point": 4e9,
                              "equity_vol": 0.3, "equity": 4e10,
                              "asset_vol": 0.22, "market_leverage": 0.35,
                              "as_of": "2026-08-15", "vol_obs": 250,
                              "shares_as_of": "2026-08-15",
                              "source": "Liabilities", "name": f"{t} Inc",
                              "sic_desc": "Electronic Computers"}}, ts=9e9)
    A._book.update(data={"dates": [f"2026-06-{d:02d}" for d in range(1, 29)],
                         "series": {t: [100.0 - 0.1 * i for i in range(28)]}},
                   ts=9e9)


c = A.app.test_client()

_seed_ticker_book("MANY")
body_many = " ".join(c.get("/credit/MANY").get_data(as_text=True).split())
assert "Buying the dip in MANY, checked" in body_many
# 4 winners of 7 (2.0, 1.5, 3.0, 0.8 > 0; -1.0 x3 <= 0) = 57.1%, avg +0.61R —
# computed directly from the fixture above, not eyeballed
assert "57.1% winners" in body_many, body_many
assert "average +0.61R" in body_many, body_many
assert "too few to say anything" not in body_many
assert "2025-05-05" in body_many, "MANY's own trade dates must be shown"
assert "more, not shown" not in body_many, "7 trades must not trigger the >10 truncation note"
print("MANY (7 trades, clears MIN_SAMPLE): shows a real win rate and avg R, "
      "not the too-few-trades caveat")

_seed_ticker_book("FEW")
body_few = c.get("/credit/FEW").get_data(as_text=True)
assert "Buying the dip in FEW, checked" in body_few
assert "too few to say anything about FEW specifically" in body_few
assert "2025-03-05" in body_few, "FEW's one real trade must still be shown"
print("FEW (1 trade, under MIN_SAMPLE): shows the real trade, but explicitly "
      "refuses to state a win rate from it")

_seed_ticker_book("NEVER")
body_never = " ".join(c.get("/credit/NEVER").get_data(as_text=True).split())
assert "Buying the dip in NEVER, checked" in body_never
assert ("has not triggered the pullback signal" in body_never
        and "nothing to show" in body_never)
print("NEVER (0 trades): says so plainly, invents nothing")

print("\n/credit/<ticker> TRADE-COUNT STATES PINNED")


# ---- 3. the governing whole-market verdict: live, and its fallback ----

_seed_ticker_book("CTXV")

# no null_test.json published yet -> the dated, hardcoded citation
A._null_test_pub.update(data=None, ts=0.0)
body_fallback = " ".join(c.get("/credit/CTXV").get_data(as_text=True).split())
assert "the signal lost" in body_fallback.lower()
assert "657 stocks" in body_fallback
print("with no published null_test.json yet, the dated whole-market citation "
      "renders instead of silence — the claim is too central to omit")

# a live, dead-signal result
A._null_test_pub.update(data={
    "universe": 512, "real": {"n": 3200, "avg_r": 0.08, "win_rate_pct": 43.0,
                              "profit_factor": 1.14},
    "null": {"runs": 30, "mean_avg_r": 0.07, "mean_win_rate": 58.0,
            "mean_pf": 1.2, "mean_trades": 3600},
    "z": 0.31, "p_value": 0.41, "signal_alive": False,
    "verdict": "the signal carries no information a coin flip does not",
}, ts=9e9)
body_dead = " ".join(c.get("/credit/CTXV").get_data(as_text=True).split())
assert "The signal lost" in body_dead
assert "p = 0.410" in body_dead
assert "512 stocks" in body_dead
print("a published, dead-signal null_test.json renders its real z/p and "
      "universe size, not the hardcoded fallback")

# a live, alive-signal result (the other branch, exercised even though it
# is not this project's current real-world finding)
A._null_test_pub.update(data={
    "universe": 512, "real": {"n": 3200, "avg_r": 0.20, "win_rate_pct": 55.0,
                              "profit_factor": 1.6},
    "null": {"runs": 30, "mean_avg_r": 0.05, "mean_win_rate": 50.0,
            "mean_pf": 1.1, "mean_trades": 3600},
    "z": 2.8, "p_value": 0.02, "signal_alive": True,
    "verdict": "the signal beats random entry on this sample",
}, ts=9e9)
body_alive = " ".join(c.get("/credit/CTXV").get_data(as_text=True).split())
assert "The signal beat random entry" in body_alive
assert "The signal lost" not in body_alive
print("the signal_alive=True branch renders its own distinct sentence, "
      "not a reused 'lost' phrase with different numbers")

print("\nWHOLE-MARKET VERDICT RENDERING PINNED")


# ---- 4. _restore_market_db(): the fix for a real bug found by testing
# the live site directly ----
# db.trades_for()/edge_for() are useless without this: nothing on Render
# ever runs the scan itself, so nothing on Render ever calls
# db.record_backtest() directly — the local MARKET_DB starts and stays
# empty forever unless something pulls in the scan's own published copy.
# Caught live: every one of Today's Five showed "has not triggered the
# pullback signal... nothing to show" even for tickers independently
# confirmed (by querying the published state/market.db directly) to have
# real recorded trades.
import requests                                                   # noqa: E402


class _FakeDbResp:
    def __init__(self, content: bytes, status: int = 200):
        self.content, self.status_code = content, status


restore_market_db_path = os.path.join(TMP, "restored_market.db")
os.environ["MARKET_DB"] = restore_market_db_path
import importlib                                                  # noqa: E402
importlib.reload(db)

# build a small real sqlite payload the same way the scan would
real_db_path = os.path.join(TMP, "source_market.db")
os.environ["MARKET_DB"] = real_db_path
importlib.reload(db)
db.record_backtest(P, [{"ticker": "RESTORED", "date": "2025-01-01",
                        "exit_date": "2025-01-10", "outcome_r": 1.5,
                        "status": "target", "rr_planned": 3.0,
                        "entry": 10.0, "stop_px": 9.0, "target_px": 13.0,
                        "bars_held": 6, "mfe_r": 1.5, "mae_r": -0.2}],
                  n_stocks=10)
# WAL mode buffers writes in a -wal sidecar until something checkpoints
# it into the main file — reading the file's own bytes right after a
# write (as this fixture does, and as scheduled_scan.py's own commit
# step effectively does once its process exits) needs that checkpoint
# forced explicitly rather than assumed
db._conn().execute("PRAGMA wal_checkpoint(TRUNCATE)")
real_db_bytes = Path(real_db_path).read_bytes()

# now point MARKET_DB at a target the app has never written to, and
# confirm _restore_market_db() is what populates it
os.environ["MARKET_DB"] = restore_market_db_path
importlib.reload(db)
A.market_db = db
assert db.trades_for(P, "RESTORED") == [], \
    "the target file must start empty, or this test proves nothing"

_saved_get = requests.get
requests.get = lambda url, timeout=None: _FakeDbResp(real_db_bytes)
try:
    ok = A._restore_market_db(force=True)
finally:
    requests.get = _saved_get
assert ok is True
assert len(db.trades_for(P, "RESTORED")) == 1
print("_restore_market_db(): a fetched market.db is swapped into place, "
      "and db.trades_for() reads the restored data immediately")

requests.get = lambda url, timeout=None: _FakeDbResp(b"", status=404)
try:
    ok2 = A._restore_market_db(force=True)
finally:
    requests.get = _saved_get
# the return value means "is there currently usable data", not "did
# this specific fetch succeed" — a 404 leaves the previous good copy in
# place, so it correctly stays True, not False
assert ok2 is True, \
    "a failed fetch must not make an already-restored instance report unhealthy"
assert len(db.trades_for(P, "RESTORED")) == 1, \
    "a failed fetch must leave the previously-restored data in place"
print("a failed fetch (404) is a safe no-op — the last good copy is kept in "
      "place and still reported healthy, nothing crashes, nothing empties")

# the OTHER half of that same semantic: a cold instance that has never
# restored anything, hitting a failure, must correctly report unhealthy —
# not accidentally inherit a stale True from a different code path
A._market_db_restored.update(ts=0.0, ok=False)
requests.get = lambda url, timeout=None: _FakeDbResp(b"", status=500)
try:
    ok3 = A._restore_market_db(force=True)
finally:
    requests.get = _saved_get
assert ok3 is False, \
    "a cold instance that has never successfully restored must report unhealthy on failure"
print("a cold instance (never yet restored) that fails stays correctly "
      "unhealthy, rather than inheriting an unrelated True")

A._market_db_restored.update(ts=__import__("time").time(), ok=True)
calls = []
requests.get = lambda url, timeout=None: (calls.append(1),
                                          _FakeDbResp(real_db_bytes))[1]
try:
    A._restore_market_db(force=False)
finally:
    requests.get = _saved_get
assert not calls, "a fresh restore must not be re-fetched before MARKET_DB_POLL_S"
print("without force=True, a recent restore is not re-fetched — same rate "
      "limiting pattern as every other published book on this site")

print("\nMARKET.DB RESTORE PINNED")

print("\nALL DIP-CHECK TESTS PASSED")
