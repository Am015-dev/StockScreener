"""One card, one decision — and "no action" must be a complete answer.

The screener page asked a reader to understand RSI, reward:risk and a
twenty-one column table before they could do anything, and carried a Run
button that started a crawl inside the web process. That button was the
source of every timeout, every failure banner and every stale download,
and once pressed it replaced the full scheduled scan with whatever the
trimmed run found.

These tests pin the replacement: the card says what to do, what was
refused and why, and whether any of it has ever worked — and it renders
from finished state without starting anything.
"""
import datetime as dt
import os
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TMP = tempfile.mkdtemp(prefix="brief_")
for k, f in (("MARKET_DB", "m.db"), ("JOURNAL_DB", "j.db"),
             ("SCREENER_CACHE_DB", "c.db"), ("RESULTS_CSV", "r.csv")):
    os.environ[k] = os.path.join(TMP, f)
sys.path.insert(0, str(ROOT))

import brief
import market_clock as mc

PICK = {"ticker": "MAR", "name": "Marriott International", "price": 353.91,
        "stop": 335.70, "resistance": 410.98, "shares": 0.8167,
        "risk_EUR": 12.86, "RR": 3.13, "RSI": 42.7, "support": 340.81,
        "vol_ratio": 0.82, "earnings_in": ">45d", "analyst": "Buy (24)",
        "edge_r": 0.28, "edge_n": 21, "mpc_r": 0.229, "friction_pct": 5.3,
        "sector": "Consumer Cyclical"}
BLOCKED = [
    {"ticker": "SHEL.L", "why_not": "no earnings calendar covers this listing while "
                                    "your 10-day earnings gate is on — blocked"},
    {"ticker": "NESN.SW", "why_not": "no earnings calendar covers this listing — blocked"},
    {"ticker": "XYZ", "why_not": "profitability/size/sector can't be verified right now"},
]
STATE = {"results": [PICK], "pending": BLOCKED, "universe_size": 1500,
         "results_ts": time.time(),
         "journal": {"n_total": 47, "n_resolved": 47, "hit_rate_pct": 61,
                     "avg_r": 0.4, "total_r": 18.8, "recent": []},
         "backtest": {"portfolio": {"profit_factor": 0.62, "sortino": -2.04}},
         "concentration": {"n_picks": 25, "effective_bets": 13.4}}

b = brief.build(STATE, mc.state(), mc.staleness(STATE["results_ts"]))

# ---- the action, in money the reader can check ----
a = b["action"]
assert a["ticker"] == "MAR"
assert a["shares"] == 0.82
assert a["cost"] == "$289.04", a["cost"]          # 0.8167 x 353.91
assert a["risk_eur"] == 12.86
assert a["reward_eur"] == 40.25, a["reward_eur"]  # 12.86 x 3.13
assert a["earnings_clear"] is True
print(f"action: buy {a['shares']} {a['ticker']} for {a['cost']}, "
      f"risk EUR{a['risk_eur']}, reward EUR{a['reward_eur']}")

# the "why" must be a sentence, not a set of ratios
why = a["why"]
for jargon in ("RSI", "R:R", "ATR", "reward:risk"):
    assert jargon not in why, f"jargon leaked into the plain-language why: {why!r}"
assert "buyers defended" in why and "analysts" in why, why
print(f"why, in words: {why}")

# ---- ">45d" is a confirmation, not a missing value ----
cleared = brief.build(dict(STATE, results=[dict(PICK, earnings_in=">45d")]))
assert cleared["action"]["earnings_clear"] is True
soon = brief.build(dict(STATE, results=[dict(PICK, earnings_in="4d",
                                             days_to_earnings=4)]))
assert soon["action"]["earnings_clear"] is False
assert soon["action"]["earnings"] == "4d"
print("earnings: '>45d' reads as confirmed clear, '4d' reads as a dated warning")

# ---- blocked picks are grouped by the RULE, never a bare count ----
assert b["n_blocked"] == 3
reasons = {g["reason"]: g["n"] for g in b["blocked"]}
assert reasons.get("no earnings calendar covers the listing") == 2, reasons
assert reasons.get("company fundamentals could not be verified") == 1, reasons
for g in b["blocked"]:
    assert g["tickers"], "a blocked group must name its tickers"
    assert "blocked" not in g["reason"].lower() or "could not" in g["reason"].lower()
print(f"blocked, grouped by rule: {reasons}")

# ---- the failing bar is ON the card, not buried ----
assert b["bar"]["profit_factor"] == 0.62
assert b["bar"]["passes"] is False
passing = brief.build(dict(STATE, backtest={"portfolio": {"profit_factor": 1.8,
                                                          "sortino": 1.2}}))
assert passing["bar"]["passes"] is True
print(f"tradeable bar carried on the card: PF {b['bar']['profit_factor']} -> passes="
      f"{b['bar']['passes']}")

# ---- the record, when there is one ----
assert b["record"]["n"] == 47 and b["record"]["win_rate"] == 61
# logged-but-unresolved must not masquerade as a win rate
young = brief.build(dict(STATE, journal={"n_total": 26, "n_resolved": 0}))
assert young["record"]["n"] == 0 and young["record"]["logged"] == 26
assert young["record"]["win_rate"] is None, \
    "picks that have not resolved cannot produce a win rate"
print("record: 47 resolved -> 61%; 26 logged but unresolved -> no win rate claimed")

# ---- NO ACTION is a complete answer, not an error ----
empty = brief.build(dict(STATE, results=[], pending=[]))
assert empty["action"] is None
assert empty["n_qualified"] == 0
assert empty["blocked"] == []
assert empty["scanned"] == 1500, "an empty day must still say how much was looked at"
assert empty["bar"]["profit_factor"] == 0.62, "the bar is stated even with no pick"
print("no qualifying setup: action is None, scan size and bar still reported")

# ---- it must never raise on missing pieces ----
for broken in ({}, {"results": [{}]}, {"results": [{"ticker": "X"}], "pending": [{}]},
               {"results": None, "pending": None, "journal": None}):
    out = brief.build(broken)
    assert isinstance(out, dict) and "action" in out
print("degenerate state renders a card instead of a stack trace")

# ---- the card reads from finished state; it computes nothing about markets ----
# Checked on the import graph, not on the source text: grepping for words
# also matches the docstring that EXPLAINS why the downloads were a problem.
import ast

tree = ast.parse((ROOT / "brief.py").read_text())
imported = set()
for node in ast.walk(tree):
    if isinstance(node, ast.Import):
        imported.update(a.name.split(".")[0] for a in node.names)
    elif isinstance(node, ast.ImportFrom) and node.module:
        imported.add(node.module.split(".")[0])
banned = {"yfinance", "requests", "screener", "backtest", "urllib", "http",
          "socket", "cache_store", "db"}
assert not (imported & banned), \
    f"the brief must not reach the market or the database: {imported & banned}"
print(f"brief.py imports only {sorted(imported)} — it renders decisions, it cannot "
      f"make one wrong")

print("\nALL BRIEF TESTS PASSED")
