"""Unit tests for journal.py outcome replay, dedupe, scoreboard, restore."""
import os
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TMP = tempfile.mkdtemp(prefix="journal_")
DB = os.path.join(TMP, "journal.db")
os.environ["JOURNAL_DB"] = DB
sys.path.insert(0, str(ROOT))

import pandas as pd

import journal

YESTERDAY = time.time() - 86400 * 10  # picks recorded 10 days ago


BASE = pd.Timestamp(pd.Timestamp.now().date()) - pd.Timedelta(days=9)

def bars(rows, start=None):
    """rows = [(open, high, low, close), ...] -> daily OHLC frame, dated
    relative to today so the 10-days-ago picks always precede the bars."""
    idx = pd.bdate_range(start=start if start is not None else BASE,
                         periods=len(rows))
    return pd.DataFrame(rows, columns=["Open", "High", "Low", "Close"], index=idx)


picks = [
    # ticker    entry  stop  target
    {"ticker": "TGT",  "price": 100, "stop": 95,  "resistance": 115, "RR": 3.0, "score": 80, "name": "Target Hit Co", "sector": "Tech", "shares": 2, "risk_EUR": 10},
    {"ticker": "STP",  "price": 50,  "stop": 47,  "resistance": 59,  "RR": 3.0, "score": 60, "name": "Stop Hit Co",   "sector": "Tech", "shares": 3, "risk_EUR": 9},
    {"ticker": "GAP",  "price": 200, "stop": 190, "resistance": 230, "RR": 3.0, "score": 75, "name": "Gap Down Co",   "sector": "Ind",  "shares": 1, "risk_EUR": 10},
    {"ticker": "BOTH", "price": 10,  "stop": 9,   "resistance": 13,  "RR": 3.0, "score": 55, "name": "Both Same Bar", "sector": "Ind",  "shares": 5, "risk_EUR": 5},
    {"ticker": "EXP",  "price": 30,  "stop": 28,  "resistance": 40,  "RR": 5.0, "score": 65, "name": "Expiry Co",     "sector": "Ind",  "shares": 4, "risk_EUR": 8},
    {"ticker": "OPEN", "price": 70,  "stop": 66,  "resistance": 82,  "RR": 3.0, "score": 72, "name": "Still Open",    "sector": "Ind",  "shares": 1, "risk_EUR": 4},
]
added = journal.record_picks(picks, YESTERDAY)
assert added == 6, added

# dedupe: same ticker while a pick is open -> not re-recorded (even next day)
assert journal.record_picks([picks[0]], YESTERDAY + 86400) == 0
# bad geometry (stop >= entry) -> skipped
assert journal.record_picks([{"ticker": "BAD", "price": 10, "stop": 11, "resistance": 12}], time.time()) == 0
# contamination guard: unverified/unavailable picks never enter the record
assert journal.record_picks([dict(picks[0], ticker="DIRTY",
                                  flags="fundamentals_unavailable")],
                            YESTERDAY) == 0

flat = lambda o: (o, o * 1.01, o * 0.99, o)
HIST = {
    # runs up, touches 115 intraday on bar 3 -> +3R at the target
    "TGT": bars([flat(101), flat(104), (108, 116, 107, 114), flat(113)]),
    # drifts down, low touches 47 on bar 2 -> -1R at the stop
    "STP": bars([flat(49.5), (48.5, 48.8, 46.9, 47.2), flat(46)]),
    # gaps open below the stop on bar 2: exit at the open 185 -> worse than -1R
    "GAP": bars([flat(196), (185, 187, 183, 186)]),
    # one bar spans both stop (9) and target (13): stop assumed first -> -1R
    "BOTH": bars([(10, 13.5, 8.8, 12)]),
    # 45 flat bars, never touches 28 or 40 -> expired at last close
    "EXP": bars([flat(31)] * 45),
    # 3 quiet bars, nothing hit -> stays open
    "OPEN": bars([flat(70), flat(71), flat(69.5)]),
}

resolved = journal.update_outcomes(lambda t: HIST.get(t))
assert resolved == 5, resolved

snap = journal.snapshot()
by = {r["ticker"]: r for r in snap["recent"]}
assert by["TGT"]["status"] == "hit_target" and by["TGT"]["outcome_r"] == 3.0
assert by["STP"]["status"] == "hit_stop" and by["STP"]["outcome_r"] == -1.0
assert by["GAP"]["status"] == "hit_stop" and by["GAP"]["outcome_r"] == -1.5  # (185-200)/10
assert by["BOTH"]["status"] == "hit_stop" and by["BOTH"]["outcome_r"] == -1.0
assert by["EXP"]["status"] == "expired" and by["EXP"]["outcome_r"] == 0.5   # (31-30)/2
assert snap["n_open"] == 1 and snap["open"][0]["ticker"] == "OPEN"
assert snap["n_resolved"] == 5
assert snap["n_wins"] == 2 and snap["hit_rate_pct"] == 40   # TGT + EXP positive
assert snap["total_r"] == 0.0  # 3.0 -1.0 -1.5 -1.0 +0.5
assert snap["grades"]["strong"]["n"] == 2   # TGT(80), GAP(75)
assert snap["grades"]["weak"]["n"] == 3

# fetch_missing fallback used for tickers get_bars can't serve
assert journal.update_outcomes(lambda t: None, lambda ts: {"OPEN": bars([(82.5, 83, 82, 82.6)], start=BASE + pd.Timedelta(days=5))}) == 1
snap = journal.snapshot()
assert snap["n_open"] == 0
by = {r["ticker"]: r for r in snap["recent"]}
assert by["OPEN"]["status"] == "hit_target" and by["OPEN"]["outcome_r"] == 3.12  # gap over target: (82.5-70)/4

# export -> wipe -> restore round-trip
dump = journal.export_all()
assert len(dump) == 6
os.remove(DB)
assert journal.snapshot()["n_total"] == 0
assert journal.restore(dump) == 6
snap2 = journal.snapshot()
assert snap2["n_total"] == 6 and snap2["n_resolved"] == 6
assert journal.restore(dump) == 0  # idempotent
assert journal.restore("garbage") == 0 and journal.restore([{"x": 1}]) == 0

print("ALL JOURNAL TESTS PASSED")
