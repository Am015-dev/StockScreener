"""Round 12: picks must differ from each other, and say what differs.

Three things are pinned here, matching the three things that shipped:

  - plan.trade_plan()'s four text fields actually branch on real
    thresholds (calm vs. volatile, strong edge vs. marginal, imminent
    earnings vs. not) rather than being one fixed sentence with the
    numbers swapped in.
  - plan.standout() names the one real, cross-pick fact that makes a
    pick different from its peers that day — and says nothing when a
    pick is not the extreme on anything, rather than inventing a reason.
  - /today actually renders the radar's data (real p["components"]) and
    the standout line into the page, and the exact-numbers bar list
    that used to be the only view of the components is still there.
"""
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import plan                                                      # noqa: E402
import ranking                                                   # noqa: E402


def cand(t, **kw):
    base = {"ticker": t, "name": f"{t} Inc", "price": 100.0,
            "market_value": 50e9, "annual_vol": 0.30, "dd": 6.0,
            "days_to_earnings": 60, "sector": "Technology"}
    base.update(kw)
    return base


# ---- 1. trade_plan()'s branches actually produce different sentences ----
calm = plan.trade_plan(cand("CALM", annual_vol=0.20), risk_budget=100.0)
wild = plan.trade_plan(cand("WILD", annual_vol=0.90), risk_budget=100.0)
assert calm["stop_text"] != wild["stop_text"], "calm and volatile got the same stop sentence"
assert "genuinely calm" in calm["stop_text"]
assert "swings harder" in wild["stop_text"]
print("stop_text branches on measured volatility, not just the numbers in it")

strong = plan.trade_plan(cand("STRONG", annual_vol=0.5), risk_budget=100.0)
marginal = plan.trade_plan(cand("MARGINAL", annual_vol=0.05), risk_budget=100.0)
assert strong["typical_move_text"] != marginal["typical_move_text"]
assert "rounding error" in strong["typical_move_text"]
assert "rounding error" not in marginal["typical_move_text"]
print("typical_move_text branches on move-vs-cost, not fixed wording")

soon = plan.trade_plan(cand("SOON", days_to_earnings=12), risk_budget=100.0,
                       horizon=ranking.DEFAULT_HORIZON)
far = plan.trade_plan(cand("FAR", days_to_earnings=60), risk_budget=100.0)
assert soon["time_stop_text"] != far["time_stop_text"]
assert "report falls due" in soon["time_stop_text"]
print("time_stop_text branches on how close the report date sits to the hold window")

credit_best = plan.trade_plan(
    cand("CREDITBEST", components={"credit headroom": 28, "calm enough to size up": 5,
                                   "adds to what you own": 2, "confirmed pattern": 1}),
    risk_budget=100.0)
calm_best = plan.trade_plan(
    cand("CALMBEST", components={"credit headroom": 5, "calm enough to size up": 28,
                                 "adds to what you own": 2, "confirmed pattern": 1}),
    risk_budget=100.0)
assert credit_best["wrong_if"] != calm_best["wrong_if"]
assert "room on its debts" in credit_best["wrong_if"]
assert "picked specifically for being quiet" in calm_best["wrong_if"]
print("wrong_if branches on which component actually put the name on the list")


# ---- 2. standout() names a real cross-pick fact, or nothing ----
def pick(ticker, stop_pct, cost_share_pct, dte=None):
    return {"ticker": ticker, "stop_pct": stop_pct,
            "cost_share_pct": cost_share_pct, "days_to_earnings": dte}


picks = [
    pick("TIGHT", stop_pct=4.0, cost_share_pct=40.0),
    pick("EDGE", stop_pct=9.0, cost_share_pct=8.0),
    pick("EARNS", stop_pct=9.5, cost_share_pct=45.0, dte=7),
    pick("PLAINA", stop_pct=9.0, cost_share_pct=42.0),
    pick("PLAINB", stop_pct=9.2, cost_share_pct=44.0),
]
lines = [plan.standout(picks, i) for i in range(len(picks))]
assert lines[0] and "Tightest stop" in lines[0], lines[0]
assert lines[1] and "Best edge" in lines[1], lines[1]
assert lines[2] and "only one" in lines[2] and "7 sessions" in lines[2], lines[2]
assert lines[3] is None, "a pick that is not the extreme on anything must get nothing"
assert lines[4] is None, "a pick that is not the extreme on anything must get nothing"
print("standout() picks the correct extreme fact for the pick that holds it, "
      "and nothing for the two that hold nothing")

# a tie for the extreme is not a standout for either side of the tie
tied = [pick("A", stop_pct=5.0, cost_share_pct=30.0),
        pick("B", stop_pct=5.0, cost_share_pct=30.0),
        pick("C", stop_pct=9.0, cost_share_pct=50.0)]
assert plan.standout(tied, 0) is None and plan.standout(tied, 1) is None, \
    "a tie for the tightest stop must not be claimed by either name"
print("a tie for an extreme belongs to neither pick, not both")

assert plan.standout([pick("SOLO", stop_pct=5.0, cost_share_pct=10.0)], 0) is None, \
    "a single pick has no peers to be a standout against"
print("with fewer than two picks there is nothing to compare against, so nothing is claimed")

print("\nALL TODAY-VISUALS UNIT TESTS PASSED")


# ---- 3. /today actually renders the real components into the radar's
# data attribute, and the exact-number bar list is still there too ----
os.environ.setdefault("PUBLISHED_DIR", "/tmp/today-visuals-published")
os.makedirs(os.environ["PUBLISHED_DIR"], exist_ok=True)
os.environ["PUBLISHED_BASE"] = "http://127.0.0.1:1"
os.environ["PUBLISHED_FETCH_S"] = "1"
os.environ["SKIP_WARM"] = "1"

import json                                                      # noqa: E402
import time                                                       # noqa: E402

import app                                                        # noqa: E402

tickers = [f"VIS{i}" for i in range(8)]
series = {t: [100.0 + i + j * (0.3 if i % 2 else -0.1) for j in range(60)]
          for i, t in enumerate(tickers)}
prices = {"dates": [f"2026-06-{(j % 28) + 1:02d}" for j in range(60)], "series": series}
vol = {t: {"vol": 0.18 + 0.1 * i, "obs": 250, "as_of": "2026-08-14"}
       for i, t in enumerate(tickers)}
credit = {t: {"ticker": t, "dd": 4.0 + i, "equity": 4e10, "shares": 1e8,
             "band": "comfortable"} for i, t in enumerate(tickers)}
earnings = {"as_of": time.strftime("%Y-%m-%d", time.gmtime()), "complete": True,
           "map": {t: 60 for t in tickers}}

app._book.update(data=prices, ts=9e9)
app._vols.update(data=vol, ts=9e9)
app._creds.update(data=credit, ts=9e9)
app._earn_pub.update(data=earnings, ts=9e9)
app._today_memo.update(key=None, res=None)

c = app.app.test_client()
r = c.get("/today")
assert r.status_code == 200, r.status_code
body = r.data.decode()

res = ranking.score(app._today_candidates(ranking.DEFAULT_HORIZON), holdings=[],
                    patterns_report=app._patterns_book(), risk_budget=100.0,
                    horizon=ranking.DEFAULT_HORIZON, corr_by_ticker=None)
top = res["ranked"][:5]
assert top, "fixture produced no picks — nothing to check the radar against"

import html as _html                                             # noqa: E402
import re as _re                                                  # noqa: E402

# tojson() escapes single quotes but not double quotes (it targets <script>
# tags, not HTML attributes) — a dict serialized straight into a
# double-quoted data- attribute breaks the attribute the instant a key
# contains a space-separated component name. Caught by running this, not
# by writing it: the first version of this template did exactly that.
assert 'data-components="{' not in body, \
    "a dict was serialized into a double-quoted attribute — its own quotes break the HTML"
radar_attrs = _re.findall(r"class=\"radar\" data-components='([^']*)'", body)
assert len(radar_attrs) == len(top), \
    f"expected {len(top)} radar divs (one per pick), found {len(radar_attrs)}"
rendered_comp = [json.loads(_html.unescape(a)) for a in radar_attrs]
real_comp = [row["components"] for row in top]
assert rendered_comp == real_comp, \
    ("the radar's data-components must be ranking.py's real per-pick output, "
     f"not a placeholder or a copy that drifted: {rendered_comp} != {real_comp}")
print(f"radar data-components for all {len(top)} picks match ranking.py's real "
      f"output, not a placeholder")

assert 'class="bars"' in body, \
    "the exact-numbers bar list must still be there — the radar promotes, it does not replace"
print("the collapsed exact-numbers bar list survives unchanged alongside the radar")

# components must differ across at least two of today's picks, or the
# radar draws the same shape for everyone and the whole point is lost
comp_sets = [tuple(sorted(row["components"].items())) for row in top]
assert len(set(comp_sets)) > 1, \
    "every pick scored identically on every component — the radar would be one shape"
print("today's picks carry genuinely different component scores, "
      "so the radar draws genuinely different shapes")

print("\nALL TODAY /today INTEGRATION CHECKS PASSED")
