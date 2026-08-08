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

# ---- every number a reader sees must be in money, not in R ----
# "+0.229R" was the most novel figure on the card and the least readable:
# R is units of planned risk, it is meaningless outside trading, and the
# page never defined it. Multiplying by the euro risk needs no glossary.
assert a["edge_eur"] == round(0.28 * 12.86, 2), a["edge_eur"]
assert a["adds_eur"] == round(0.229 * 12.86, 2), a["adds_eur"]
assert "adds_r" not in a and "edge_r" not in a, \
    "R-denominated figures must not reach the card"
for x in b["also"]:
    assert "adds_r" not in x, x
print(f"expectancy in money: EUR{a['edge_eur']} average, EUR{a['adds_eur']} after "
      f"removing what you already own")

# ---- the card must not tell a reader to buy what it also warns them off ----
# It said "One action today: buy MAR" and, four inches lower, "do not put
# real money behind this yet". Both were true; together they were useless.
assert b["tradeable"] is False, "PF 0.62 cannot read as tradeable"
passing_b = brief.build(dict(STATE, backtest={"portfolio": {"profit_factor": 1.8,
                                                            "sortino": 1.2}}))
assert passing_b["tradeable"] is True
print("headline follows the evidence: tradeable=False at PF 0.62, True at PF 1.8")

# ---- the concentration finding must arrive as an instruction ----
withc = brief.build(dict(STATE, concentration={
    "n_picks": 25, "effective_bets": 13.4,
    "biggest_cluster": {"n": 3, "tickers": ["WM", "RSG", "WCN"], "mean_corr": 0.85}}))
assert withc["one_trade"]["tickers"] == ["WM", "RSG", "WCN"], withc["one_trade"]
assert brief.build(dict(STATE, concentration={"n_picks": 3}))["one_trade"] is None
print(f"cluster surfaces as a do-not: {withc['one_trade']['tickers']} are one trade")

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

# ---- the card is a watchlist, not a recommendation ----
# Taking money changes the obligations: presenting an unprofitable pattern
# as something to do is dishonest before the first euro and a different
# regulatory category after it.
assert len(b["watchlist"]) >= 1
w = b["watchlist"][0]
for k in ("ticker", "price", "stop", "target", "rsi", "earnings"):
    assert k in w, (k, w)
assert w["support_dist"] is not None
src = (ROOT / "templates" / "brief.html").read_text()
# The banner must state the permutation-test finding, not a softer
# version of it. "Has not been profitable" invites the thought that
# tuning might fix it; "indistinguishable from random entry" does not.
assert "filter, not a" in src, "the card must not present the list as a signal"
assert "worse" in src and "coin flip" in src, \
    "the banner must state that random entry beat the pattern"
# Affirmative language only — the disclaimer itself contains the word
# "recommendations", and a bare substring check flags its own denial.
for banned in ("One action today: buy", "we recommend", "you should buy",
               "our recommendation", "buy this", "top pick of the day"):
    assert banned.lower() not in src.lower(), \
        f"recommendation language on the card: {banned!r}"
assert "no better than random entry" in src or "filter, not a" in src, \
    "the card must say the pattern tested no better than random"
print(f"watchlist of {len(b['watchlist'])} rows, permanent research-only banner, "
      f"no recommendation language")

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

# ---- a trade that cannot be fully described is not shown at all ----
# The card states an entry, a stop and a euro risk as plain facts and the
# template formats all three as numbers. A pick missing any of them used
# to take the whole page down with a 500; the fix must be to withhold the
# card, not to print "None" where a stop price belongs — a half-described
# trade reads as a complete one.
full = {"ticker": "ZZA", "price": 100.0, "stop": 94.0, "resistance": 118.0,
        "risk_EUR": 100.0, "RR": 3.2, "shares": 12.0}
assert brief.build({"results": [dict(full)]})["action"] is not None

for missing in ("stop", "risk_EUR", "price"):
    partial = dict(full)
    partial[missing] = None
    out = brief.build({"results": [partial]})
    assert out["action"] is None, \
        f"a pick with no {missing} must not be presented as today's trade"
print("a pick missing its stop, its risk or its price is withheld, not half-drawn")

print("\nALL BRIEF TESTS PASSED")
