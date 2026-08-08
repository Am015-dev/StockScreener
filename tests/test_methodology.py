"""The screener must obey its own definition, and never publish fail-open.

An external audit of a live run found five defects that every existing
test missed, because each was a question about *what the tool claims to
be* rather than whether a number was computed correctly:

  - 52 published picks with no earnings data, still scored and ranked —
    fail-open, on the single most safety-critical column;
  - ~25% of rows breaking the site's own pullback definition: RSI 62-67
    momentum names, entries 8-12% above support;
  - a name with an analyst consensus of Sell ranked 55;
  - a displayed reward:risk of 16.5, from a stale swing high.

A pullback screener listing chases is wrong even when every number in the
row is accurate. These tests pin the definition itself.
"""
import os
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TMP = tempfile.mkdtemp(prefix="method_")
os.environ["MARKET_DB"] = os.path.join(TMP, "market.db")
os.environ["SCREENER_CACHE_DB"] = os.path.join(TMP, "cache.db")
# Hermetic: the app's warmers reach Nasdaq, the SEC and GitHub, and
# its startup adoption fetches the real published index and caches
# it — which a stub installed later can no longer displace.
os.environ["SKIP_WARM"] = "1"
# The deploy branch SHIPS published/*.json, so a CI checkout has real
# scan results on disk that the app adopts at import — overriding
# whatever this test set up. Point the lookup somewhere empty.
os.environ["PUBLISHED_DIR"] = os.path.join(TMP, "no_published")
os.makedirs(os.environ["PUBLISHED_DIR"], exist_ok=True)
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

import screener

# ---- the methodology ceilings cannot be exceeded by anyone ----
for key, ceiling in screener.METHODOLOGY_MAX.items():
    p = screener.clean_params({key: ceiling + 25})
    assert p[key] == ceiling, f"{key} was not clamped: {p[key]}"
    assert any(key in c for c in p["methodology_clamped"]), \
        "a clamp must be reported, never silent"
# and a value inside the bounds passes through untouched, unreported
ok = screener.clean_params({"rsi_high": 55, "max_support_dist_pct": 4})
assert ok["rsi_high"] == 55 and ok["max_support_dist_pct"] == 4
assert ok["methodology_clamped"] == []
print(f"methodology ceilings enforced and reported: {screener.METHODOLOGY_MAX}")

# the ceilings must actually exclude the names the audit caught
for rsi in (62, 65, 67):
    assert rsi > screener.clean_params({"rsi_high": 99})["rsi_high"], \
        f"RSI {rsi} would still qualify — that is a momentum name, not a dip"
# 8-12% was the range the audit found live; the rubric draws the line at 5%,
# because the whole argument for this setup is that the stop sits just under
# support, so a loss stays cheap. An entry 8% above support is a different
# trade wearing the same name.
for dist in (12.0, 11.9, 8.0, 5.1):
    assert dist > screener.clean_params({"max_support_dist_pct": 99})["max_support_dist_pct"], \
        f"an entry {dist}% above support is not 'near support'"
print("the exact rows the audit flagged (RSI 62-67, entries 8-12% above "
      "support) can no longer qualify")

# ---- an analyst Sell consensus is a standing deduction ----
def row(**kw):
    base = {"RR": 3.0, "RSI": 45.0, "price": 100.0, "support": 95.0,
            "rs_3m": 2.0, "vol_ratio": 0.8, "days_to_earnings": 30,
            "analyst_mean": None, "flags": ""}
    base.update(kw)
    return base


P = screener.clean_params({})
neutral, _ = screener.score_row(row(), P)
buy, _ = screener.score_row(row(analyst_mean=1.8), P)
sell, _ = screener.score_row(row(analyst_mean=4.2), P)
assert buy > neutral > sell, (buy, neutral, sell)
assert neutral - sell >= 25, \
    f"a Sell consensus cost only {neutral - sell} points — it contradicts the " \
    f"entire premise of buying the dip and must not be a rounding error"
assert sell < 50, f"a Sell-rated name scored {sell}; it must not read as 'okay'"
print(f"analyst Sell: {neutral} -> {sell} (Buy {buy}) — no longer a top-half score")

# ---- an implausible reward:risk is a data fault, not an opportunity ----
# This used to assert only that two CONSTANTS compared as expected, which
# cannot fail and did not touch the screener: deleting the emit-time gate
# outright left the whole suite green. The audit that prompted the gate
# found "R:R 16.5" on the live board, from a swing high years stale. So
# the gate itself is now run.
assert screener.RR_SANE_MAX <= 10, screener.RR_SANE_MAX
_rejects = {}


def _reject(t, why):
    _rejects[t] = why


_gate = screener._rr_gate if hasattr(screener, "_rr_gate") else None
assert _gate is not None, "the reward:risk gate must be callable to be testable"

# a stale target level: 16.5:1 on a name whose stop is 1% away
assert _gate("STALE", price=100.0, stop=99.0, resistance=116.5,
             reject=_reject) is False
assert "STALE" in _rejects and "implausible" in _rejects["STALE"], _rejects
assert "16.5" in _rejects["STALE"], _rejects["STALE"]

# a normal setup passes untouched
_rejects.clear()
assert _gate("FINE", price=100.0, stop=95.0, resistance=115.0,
             reject=_reject) is True
assert not _rejects, _rejects

# and degenerate risk math is refused rather than dividing by zero
_rejects.clear()
assert _gate("FLAT", price=100.0, stop=100.0, resistance=115.0,
             reject=_reject) is False
assert "degenerate" in _rejects["FLAT"], _rejects
print(f"reward:risk above {screener.RR_SANE_MAX:g} is rejected by the gate "
      f"itself, not merely by a comparison of two constants")

# ---- no published preset may fail open or break the methodology ----
import scheduled_scan

for name, overrides in scheduled_scan.PRESETS.items():
    p = screener.clean_params(dict(overrides, universe_max=100))
    assert p["strict_gates"], \
        f"preset {name!r} publishes picks whose safety gates were never verified"
    assert not p["methodology_clamped"], \
        f"preset {name!r} exceeds the methodology: {p['methodology_clamped']}"
    scheduled_scan._assert_publishable(name, p)   # must not raise
print(f"all {len(scheduled_scan.PRESETS)} presets are fail-closed and within "
      f"the methodology")

# and the guard actually fires on a bad preset
for bad, why in (({"strict_gates": False}, "strict_gates"),
                 ({"rsi_high": 68}, "methodology")):
    try:
        scheduled_scan._assert_publishable(
            "bad", screener.clean_params(dict(bad, universe_max=100)))
        raise AssertionError(f"a preset with {bad} must be refused")
    except ValueError as e:
        assert why in str(e).lower() or "methodology" in str(e).lower(), e
print("the publisher refuses a fail-open or off-methodology preset")

# ---- stored results must not outlive the rules that made them ----
# A snapshot written before a rule tightened kept being served, so the
# methodology fix reached the code and never the screen. That is how a
# live page carried RSI 67 rows and a reward:risk of 13.9 hours after
# both were supposedly banned.
os.environ.setdefault("JOURNAL_DB", os.path.join(TMP, "j.db"))
os.environ.setdefault("RESULTS_CSV", os.path.join(TMP, "r.csv"))
import app as app_mod

good = {"ticker": "OK", "RSI": 44.0, "RR": 3.0, "price": 100.0, "support": 96.0}
offenders = [
    ({"ticker": "HOT", "RSI": 67.3, "RR": 3.0, "price": 100.0, "support": 96.0}, "RSI"),
    ({"ticker": "WILD", "RSI": 44.0, "RR": 13.9, "price": 100.0, "support": 96.0}, "reward:risk"),
    ({"ticker": "FAR", "RSI": 44.0, "RR": 3.0, "price": 100.0, "support": 80.0}, "above support"),
]
assert app_mod._methodology_violations(good) == [], "a compliant row must survive"
for bad, expect in offenders:
    why = app_mod._methodology_violations(bad)
    assert why, f"{bad['ticker']} should have been flagged"
    assert any(expect in w for w in why), (bad["ticker"], why)

payload = {"results": [good] + [o for o, _ in offenders],
           "near_board": [o for o, _ in offenders], "pending": []}
cleaned, dropped = app_mod._drop_offmethod(payload)
assert dropped == 3, dropped
assert [r["ticker"] for r in cleaned["results"]] == ["OK"], cleaned["results"]
assert cleaned["near_board"] == [], "the near board must be filtered too"
assert cleaned["top_picks"] == [good]
print(f"stored rows revalidated on load: {dropped} off-methodology rows dropped, "
      f"compliant rows kept")

# the revalidator must measure distance the way the screener measures it.
# Off price instead of off support, 5.2% reads as 4.9% and slips through —
# a filter quietly looser than the rule it exists to enforce.
edge_dist = screener.METHODOLOGY_MAX["max_support_dist_pct"] + 0.2
just_over = {"ticker": "EDGE", "RSI": 44.0, "RR": 3.0,
             "price": 100.0 * (1 + edge_dist / 100), "support": 100.0}
assert app_mod._methodology_violations(just_over), \
    f"a row {edge_dist}% above support must be caught, not rounded into range"
just_under = dict(just_over, ticker="NEAR",
                  price=100.0 * (1 + (edge_dist - 0.4) / 100))
assert app_mod._methodology_violations(just_under) == [], \
    "a row inside the ceiling must not be dropped"
print(f"support distance revalidated off support: {edge_dist:g}% dropped, "
      f"{edge_dist - 0.4:g}% kept")

# a row missing the fields cannot be judged, and must not be silently dropped
assert app_mod._methodology_violations({"ticker": "SPARSE"}) == []
print("a row with no comparable fields is kept, not guessed at")

print("\nALL METHODOLOGY TESTS PASSED")
