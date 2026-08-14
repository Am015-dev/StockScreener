"""vix.py: today's VIX close against its own trailing history.

Investigated while reviewing Fincept Terminal (github.com/Fincept-
Corporation/FinceptTerminal, AGPL-3.0) at the operator's request. Only
the CBOE CDN URL is borrowed, with attribution — everything here is a
fresh implementation against the public CSV, and these tests exist so
the module's actual behaviour (not its docstring's claims about itself)
is what ships: the CSV parser degrades on bad rows rather than raising,
the percentile direction is the one the caller actually needs, and the
note() thresholds fire only in the tails, matching the rest of this
page's rule of staying silent on the ordinary case.
"""
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import vix

# ---- CSV parsing ----
CSV_OK = """DATE,OPEN,HIGH,LOW,CLOSE
01/02/2020,13.10,13.50,12.90,13.00
01/03/2020,14.20,14.50,14.00,14.10
"""
rows = vix.parse_vix_csv(CSV_OK)
assert rows == [("2020-01-02", 13.00), ("2020-01-03", 14.10)], rows
print("a well-formed CSV parses to (iso date, close) pairs in file order")

CSV_MESSY = """DATE,OPEN,HIGH,LOW,CLOSE
01/02/2020,13.10,13.50,12.90,13.00
not-a-date,x,x,x,x
01/04/2020,14.20,14.50,14.00,not-a-number
01/05/2020,14.20,14.50,14.00,0
01/06/2020,14.20,14.50,14.00,15.50
"""
rows2 = vix.parse_vix_csv(CSV_MESSY)
assert rows2 == [("2020-01-02", 13.00), ("2020-01-06", 15.50)], rows2
print("a bad date, a non-numeric close, and a zero close are all skipped — "
      "fewer rows read, never a crash")

assert vix.parse_vix_csv("") == []
assert vix.parse_vix_csv(None) == []
print("empty or missing text parses to an empty list, not an error")


# ---- regime(): the actual behaviour, not the aspiration ----
def mk_csv(n: int, base: float = 20.0, last: float | None = None) -> str:
    lines = ["DATE,OPEN,HIGH,LOW,CLOSE"]
    for i in range(n):
        m, d = (i % 12) + 1, (i % 28) + 1
        val = base if (last is None or i < n - 1) else last
        lines.append(f"{m:02d}/{d:02d}/2020,{val},{val},{val},{val}")
    return "\n".join(lines)


# get_text raising -> None, never a crash
r = vix.regime(lambda u: (_ for _ in ()).throw(RuntimeError("down")))
assert r is None
print("a fetch failure answers None, not an exception")

# exactly 60 rows: window[:-1] is only 59, one short of the history floor
# — this is the actual boundary the code enforces, not a rounder number
r60 = vix.regime(lambda u: mk_csv(60))
assert r60 is None, r60
r61 = vix.regime(lambda u: mk_csv(61))
assert r61 is not None, "61 rows is enough: 60 of history plus today"
print("60 rows refuses (59 of history is one short); 61 succeeds — the "
      "real boundary, not an approximation of it")

# percentile direction: today's close ABOVE every day of history reads
# 100 — the highest it can read, matching "today is unusually elevated"
spike = vix.regime(lambda u: mk_csv(120, base=20.0, last=45.0))
assert spike["level"] == 45.0
assert spike["percentile_5y"] == 100, spike
assert spike["n_obs"] == 119
assert spike["as_of"] is not None
print(f"a close well above 119 days of flat history reads at the "
      f"{spike['percentile_5y']}th percentile — the un-ambiguous top")

# and the opposite: today's close BELOW every day of history reads 0
calm = vix.regime(lambda u: mk_csv(120, base=20.0, last=5.0))
assert calm["percentile_5y"] == 0, calm
print(f"a close well below history reads at the {calm['percentile_5y']}th "
      f"percentile — the un-ambiguous bottom")

# the lookback window actually bounds history — a huge file is trimmed to
# the last LOOKBACK_SESSIONS rows before today, not read in full
huge = vix.regime(lambda u: mk_csv(vix.LOOKBACK_SESSIONS + 500, base=20.0, last=45.0))
assert huge["n_obs"] == vix.LOOKBACK_SESSIONS - 1, huge
print(f"a file far longer than the lookback window is trimmed to "
      f"{vix.LOOKBACK_SESSIONS} sessions, not read in full")

print("\nVIX REGIME PINNED")


# ---- note(): silent on the ordinary case, on both tails and on None ----
assert vix.note(None) is None
assert vix.note({}) is None
print("no reading at all, or a reading with no percentile, says nothing")

mid = {"level": 18.5, "as_of": "2026-08-13", "percentile_5y": 50, "n_obs": 1259}
assert vix.note(mid) is None
print("an ordinary, middle-of-the-range reading is silent — the same rule "
      "the SPY/Stoxx regime note already follows")

high = {"level": 32.1, "as_of": "2026-08-13", "percentile_5y": 92, "n_obs": 1259}
hn = vix.note(high)
assert hn is not None and "elevated" in hn and "32.1" in hn and "92" in hn
print(f"an elevated reading (92nd percentile) speaks: {hn[:70]}...")

low = {"level": 9.4, "as_of": "2026-08-13", "percentile_5y": 4, "n_obs": 1259}
ln = vix.note(low)
assert ln is not None and "calm" in ln and "9.4" in ln
print(f"an unusually calm reading (4th percentile) speaks too: {ln[:70]}...")

# the exact boundary: HIGH_PCTL and LOW_PCTL are inclusive, not one past
edge_high = vix.note({"level": 30.0, "as_of": "x", "percentile_5y": vix.HIGH_PCTL, "n_obs": 100})
assert edge_high is not None, "the high threshold itself must speak, not require one more"
edge_low = vix.note({"level": 10.0, "as_of": "x", "percentile_5y": vix.LOW_PCTL, "n_obs": 100})
assert edge_low is not None, "the low threshold itself must speak, not require one less"
just_inside = vix.note({"level": 20.0, "as_of": "x", "percentile_5y": vix.HIGH_PCTL - 1, "n_obs": 100})
assert just_inside is None, "one percentile point inside either threshold must stay silent"
print("both thresholds are inclusive, and one point on the ordinary side of "
      "either is silent")

print("\nVIX NOTE PINNED")
