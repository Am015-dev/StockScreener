"""The check a reader brings their own trade to.

This project's picks lose money — profit factor 0.62 through a realistic
book, and not one published pick has resolved. Selling those picks would
be selling something the site itself says not to use.

The check is what survives that. It makes no prediction: every output is
a fact about data a reader cannot assemble themselves — an earnings date
verified against a full calendar, how much of the trade they already own
measured on returns rather than guessed from sector labels, and whether
their book gets wider or merely heavier. None of it needs the strategy to
work, and none of it is available free anywhere.

Which is exactly why it has to refuse cleanly. A check that guesses is
worse than no check, because the reader stops looking.
"""
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TMP = tempfile.mkdtemp(prefix="pretrade_")
os.environ.setdefault("MARKET_DB", os.path.join(TMP, "m.db"))
os.environ.setdefault("SCREENER_CACHE_DB", os.path.join(TMP, "c.db"))
sys.path.insert(0, str(ROOT))

import numpy as np

import concentration as cc
import pretrade

rng = np.random.default_rng(3)
N = 70
F = rng.normal(0, 0.012, (2, N))


def mk(load, idio=0.004):
    r = load[0] * F[0] + load[1] * F[1] + rng.normal(0, idio, N)
    return [round(float(x), 2) for x in 100 * np.cumprod(1 + r)]


# The published book carries one shared calendar and one aligned series
# per ticker. Correlating without it lines columns up by row position,
# which is only right when every stock traded on exactly the same days.
DATES = [f"2026-{1 + i // 28:02d}-{1 + i % 28:02d}" for i in range(N)]
BOOK = {"dates": DATES,
        "series": {"NVDA": mk([1, 0]), "AMD": mk([1, 0]), "AVGO": mk([1, 0]),
                   "KO": mk([0, 1]), "PEP": mk([0, 1]),
                   "XOM": mk([0, 0], idio=0.015)}}
HELD = [{"ticker": "NVDA"}, {"ticker": "AMD"}, {"ticker": "KO"}]


def levels(r):
    return {f["level"] for f in r["findings"]}


def headline(r, level):
    return next((f["headline"] for f in r["findings"] if f["level"] == level), None)


# ---- the finding that justifies the price ----
r = pretrade.check("AVGO", HELD, BOOK, {"AVGO": 30}, True)
h = headline(r, "warn")
assert "already own this trade" in h, h
assert r["verdict"] == "warn"
assert r["bets_before"] and r["bets_after"]
assert r["bets_after"] - r["bets_before"] < 0.35, (r["bets_before"], r["bets_after"])
print(f"buying a third semiconductor: {h!r}; book goes "
      f"{r['bets_before']} -> {r['bets_after']} independent bets")

# ---- and the opposite verdict, on the same book ----
r2 = pretrade.check("XOM", HELD, BOOK, {}, True)
assert r2["verdict"] == "ok", [f["headline"] for f in r2["findings"]]
assert r2["bets_after"] - r2["bets_before"] >= 0.35
assert "do not already own" in " ".join(f["headline"] for f in r2["findings"])
print(f"buying something uncorrelated: verdict ok, book goes "
      f"{r2['bets_before']} -> {r2['bets_after']}")

# the check must actually DISCRIMINATE — a tool that says "warn" to
# everything is decoration
assert r["verdict"] != r2["verdict"], "the check gives the same answer to both"

# ---- earnings: verified, dated, or refused. Never assumed. ----
soon = pretrade.check("XOM", HELD, BOOK, {"XOM": 4}, True)
assert soon["verdict"] == "block", soon["verdict"]
assert "Earnings in 4 days" in headline(soon, "block")

clear = pretrade.check("XOM", HELD, BOOK, {"XOM": 30}, True)
assert "block" not in levels(clear), [f["headline"] for f in clear["findings"]]

absent = pretrade.check("XOM", HELD, BOOK, {}, True)      # complete calendar
assert "block" not in levels(absent)
assert "at least" in " ".join(f["headline"] for f in absent["findings"])

holed = pretrade.check("XOM", HELD, BOOK, {}, False)      # incomplete calendar
assert holed["verdict"] == "block", holed["verdict"]
assert "could not be verified" in headline(holed, "block")
print("earnings: 4 days -> block, 30 days -> pass, absent from a COMPLETE "
      "calendar -> pass, absent from a HOLED one -> block")

# ---- an unmeasurable overlap must not read as 'new' ----
unknown = pretrade.check("NOTINBOOK", HELD, BOOK, {"NOTINBOOK": 30}, True)
warn = headline(unknown, "warn")
assert warn and "could not be measured" in warn, [f["headline"] for f in unknown["findings"]]
assert "do not already own" not in " ".join(f["headline"] for f in unknown["findings"]), \
    "an unmeasurable overlap must never be reported as a new bet"
print("a ticker with no shared history: reported unmeasurable, never as 'new'")

# ---- no holdings is a prompt, not a false all-clear ----
bare = pretrade.check("AVGO", [], BOOK, {"AVGO": 30}, True)
assert "No holdings given" in " ".join(f["headline"] for f in bare["findings"])
assert "bets_before" not in bare
assert "do not already own" not in " ".join(f["headline"] for f in bare["findings"]), \
    "with no book, the check cannot say a trade is new"
print("with no holdings: asks for them rather than declaring the trade clean")

# ---- self-overlap is not a finding ----
same = pretrade.check("NVDA", [{"ticker": "NVDA"}], BOOK, {"NVDA": 30}, True)
assert same["held"] == [], "a ticker cannot be its own overlap"
print("checking a stock you already hold does not report it against itself")

# ---- costs ----
pricey = pretrade.check("XOM", HELD, BOOK, {"XOM": 30}, True,
                        reward_eur=40.0, friction_pct=17.0)
assert any("Costs take" in f["headline"] and f["level"] == "warn"
           for f in pricey["findings"]), [f["headline"] for f in pricey["findings"]]
cheap = pretrade.check("XOM", HELD, BOOK, {"XOM": 30}, True,
                       reward_eur=40.0, friction_pct=4.0)
assert any("Costs take" in f["headline"] and f["level"] == "ok"
           for f in cheap["findings"])
print("costs: 17% of the target profit warns, 4% does not")

# ---- every finding must be a sentence, not a code ----
for r_ in (r, r2, soon, holed, unknown):
    for f in r_["findings"]:
        assert f["headline"] and f["detail"], f
        assert len(f["detail"]) > 30, f["detail"]
        for jargon in ("mpc", "corr_max", "R)", "eigen", "_"):
            assert jargon not in f["headline"], f["headline"]
print("every finding carries a headline and a plain-language explanation")

# ---- it makes no forecast ----
import ast

tree = ast.parse((ROOT / "pretrade.py").read_text())
imported = set()
for node in ast.walk(tree):
    if isinstance(node, ast.Import):
        imported.update(a.name.split(".")[0] for a in node.names)
    elif isinstance(node, ast.ImportFrom) and node.module:
        imported.add(node.module.split(".")[0])
assert not (imported & {"yfinance", "requests", "backtest", "urllib"}), imported
print(f"pretrade.py imports only {sorted(imported)} — it measures, it does not predict")

print("\nALL PRE-TRADE CHECK TESTS PASSED")

# ---- "still loading" is not "broken", and neither may hang ----
# The live endpoint took over 180 seconds on its first call and 176ms on
# every one after: it was building a 45-day earnings calendar — ~32
# sequential requests — inside the web request. A user pressed the button
# and watched a spinner with no explanation until they gave up.
warm = pretrade.check("XOM", HELD, BOOK, {}, False, warming=True)
assert warm["verdict"] == "block", "an unready calendar must still block the pick"
h = headline(warm, "block")
assert "still loading" in h.lower(), h
assert "not an error" in " ".join(f["detail"] for f in warm["findings"]).lower()

broke = pretrade.check("XOM", HELD, BOOK, {}, False, warming=False)
assert broke["verdict"] == "block"
assert "could not be verified" in headline(broke, "block")
assert headline(warm, "block") != headline(broke, "block"), \
    "loading and broken must not read identically — only one is worth waiting out"
print("cold cache says 'still loading'; a real failure says 'could not be verified'; "
      "both block")

# a missing price book while warming says so rather than blaming the data
nb = pretrade.check("XOM", HELD, {}, {"XOM": 30}, True, warming=True)
assert any("still loading" in f["headline"].lower() for f in nb["findings"]), \
    [f["headline"] for f in nb["findings"]]
print("an empty price book during warm-up is reported as loading, not as unmeasurable")

# and the build guard itself: asking without building must never walk the window
import inspect
import screener as _sc

sig = inspect.signature(_sc._earnings_calendar)
assert "build" in sig.parameters and sig.parameters["build"].default is True, sig
src = inspect.getsource(_sc._earnings_calendar)
assert "if not build:" in src, "build=False must return before the request loop"
assert src.index("if not build:") < src.index("for i in range("), \
    "the guard must come BEFORE the loop it is guarding"
print("_earnings_calendar(build=False) returns before the 32-request loop")


# ---- holdings that could not be compared must be named, not counted ----
# Every sentence used to count len(held) while the comparison only ever
# covered the holdings that appear in the published book. A reader with
# eight positions, three of them measurable, was told "across 8 positions"
# — and any of the five unchecked ones could have been the same trade.
MIXED = [{"ticker": t} for t in
         ("NVDA", "AMD", "KO", "TSLA", "VOD.L", "ASML.AS", "7203.T", "BABA")]
rm = pretrade.check("XOM", MIXED, BOOK, {}, True)
_text = " ".join(f["headline"] + " " + f["detail"] for f in rm["findings"])
assert "could not be compared" in _text, _text
for _t in ("TSLA", "VOD.L", "ASML.AS"):
    assert _t in _text, f"{_t} was silently dropped from the comparison"
# naming the total in "5 of your 8 could not be compared" is fine; what
# must not survive is a claim ABOUT all eight that only covered three
for _claim in ("Across 8 positions", "You hold 8 positions",
               "You hold 8 measurable"):
    assert _claim not in _text, f"{_claim!r} describes positions never measured"
# the sentence about the book must count the 3 that were compared, not
# all 8 — the phrasing has changed twice, the requirement has not
assert "Your 3 positions" in _text, _text
print("holdings with no published history are named and excluded from the count")

print("\nCOLD-START BEHAVIOUR PINNED")
