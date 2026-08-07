"""Position-sizing math tests: currency detection, EUR<->listing-unit
conversion (including London pence and USD-quoted LSE lines), the
risk-per-trade invariant across currencies, the ticket cap, and the
market-breadth throttle. This is the suite that keeps the GBp ~100x
sizing bug — and its ~85x inverse — from ever coming back."""
import hashlib
import os
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TMP = tempfile.mkdtemp(prefix="sizing_")
os.environ["SCREENER_CACHE_DB"] = os.path.join(TMP, "cache.db")
os.environ["MARKET_DB"] = os.path.join(TMP, "market.db")
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).parent))

import numpy as np
import pandas as pd
import yfinance as yf
from yfinance.exceptions import YFRateLimitError

import screener
from _synth import pullback_hist, weak_hist

# hermetic: no sleeping, no network, no auth session
screener.RETRY_DELAYS = ()
screener.QUOTE_RETRY_MAX_WAIT = 0
screener._yahoo_auth_session = lambda: (None, None)
screener._get_quote_v7 = lambda t: None


def boom(*a, **k):
    raise YFRateLimitError()


class BoomTicker:
    def __init__(self, t): self.t = t
    @property
    def info(self): boom()
    def get_earnings_dates(self, limit=4): boom()


yf.download = boom
yf.screen = boom
yf.Ticker = BoomTicker

# pin FX so every expected number below is exact
RATES = {"USD": 1.10, "GBP": 0.85, "CHF": 0.94,
         "DKK": 7.46, "SEK": 11.3, "NOK": 11.6}
screener._fx.update(rates=dict(RATES), ts=time.time())

# ---- currency detection: suffix heuristic + metadata override ----
assert screener._ccy("AAPL") == "USD"
assert screener._ccy("SHEL.L") == "GBp"      # London default: pence
assert screener._ccy("SAP.DE") == "EUR"
assert screener._ccy("NOVO-B.CO") == "DKK"
assert screener._ccy("ERIC-B.ST") == "SEK"
screener._ccy_override["ZZV.L"] = "USD"      # USD line on the LSE (VUAA.L case)
assert screener._ccy("ZZV.L") == "USD"

# ---- EUR -> listing-unit multipliers ----
assert screener._eur_to_listing("SAP.DE") == 1.0
assert abs(screener._eur_to_listing("AAPL") - 1.10) < 1e-9
assert abs(screener._eur_to_listing("SHEL.L") - 85.0) < 1e-9   # 0.85 GBP x 100 pence
assert abs(screener._eur_to_listing("ZZV.L") - 1.10) < 1e-9    # override beats .L

# ---- _to_eur must be the exact inverse of _eur_to_listing ----
for t in ("AAPL", "SHEL.L", "SAP.DE", "NOVO-B.CO", "ERIC-B.ST", "ZZV.L",
          "NESN.SW", "EQNR.OL"):
    for amount in (1.0, 90.0, 12345.6):
        back = screener._to_eur(amount * screener._eur_to_listing(t), t)
        assert abs(back - amount) < 1e-6, (t, amount, back)
print("FX unit checks OK (suffix map, pence x100, USD-line override, inverses)")


# ---- end-to-end: same setup in four currencies must risk the SAME EUR ----
def run_scan(tickers, hists, params):
    ohlc = pd.concat(hists, axis=1)
    bench = pd.Series(np.linspace(300, 400, 260),
                      index=pd.bdate_range(end=pd.Timestamp.today(), periods=260))
    p = screener.clean_params(params)
    key = (p["include_us"], p["include_eu"], tuple(p["sectors"]),
           round(p["min_mkt_cap_b"] * 1e9), p["universe_max"])
    okey = hashlib.md5(",".join(tickers).encode()).hexdigest()
    screener._cache.update(universe_key=key, universe=list(tickers),
                           universe_ts=time.time(), ohlc_key=okey, ohlc=ohlc,
                           ohlc_ts=time.time(),
                           bench={"US": bench, "EU": bench}, bench_ts=time.time())
    screener._rl.update(until={}, hits=0)
    log = []
    res = screener.run_screener(params, progress=lambda m: log.append(str(m)))
    return res, log


CANDS = ["ZZUS", "ZZEU.DE", "ZZGB.L", "ZZV.L"]   # USD, EUR, GBp, USD-override
# same proven pullback shape for all four: the variable under test is the
# currency, and identical bars make the EUR-risk comparison exact
HISTS = {t: pullback_hist(0) for t in CANDS}
MAX_RISK = 90.0
params = {"min_dollar_vol_m": 10, "min_rr": 0.5, "rsi_low": 0, "rsi_high": 100,
          "min_stop_atr": 0, "earnings_drop_days": 0, "max_friction_pct": 0,
          "strict_gates": False, "max_support_dist_pct": 0, "exclude": "",
          "max_risk_eur": MAX_RISK, "ticket_eur": 1_000_000.0}

res, log = run_scan(CANDS, HISTS, params)
df = res["df"]
got = set(df["ticker"])
assert got == set(CANDS), (got, res["rejections"])
rf = res["breadth"]["risk_factor"]
for _, row in df.iterrows():
    expect = MAX_RISK * rf
    assert abs(row["risk_EUR"] - expect) / expect < 0.01, \
        (row["ticker"], row["risk_EUR"], expect)
risks = df["risk_EUR"].tolist()
assert max(risks) / min(risks) < 1.01, risks   # identical across currencies
print(f"risk invariant OK: all four currencies risk "
      f"~EUR {MAX_RISK * rf:.2f} (throttle {rf}x) — no 100x/85x drift")

# ---- ticket cap: position VALUE capped at ticket_eur, risk drops below max ----
params_ticket = dict(params, ticket_eur=200.0)
res_t, _ = run_scan(CANDS, HISTS, params_ticket)
df_t = res_t["df"]
rf_t = res_t["breadth"]["risk_factor"]
assert set(df_t["ticker"]) == set(CANDS)
for _, row in df_t.iterrows():
    value_eur = screener._to_eur(row["shares"] * row["price"], row["ticker"])
    assert abs(value_eur - 200.0 * rf_t) / (200.0 * rf_t) < 0.01, \
        (row["ticker"], value_eur)
    assert row["risk_EUR"] < MAX_RISK * rf_t
print("ticket cap OK: EUR 200 ticket binds identically in all currencies")

# ---- breadth throttle: weak tape -> 0.25x sizing, flagged ----
FILLERS = [f"ZZW{i}" for i in range(12)]
hists2 = dict(HISTS)
hists2.update({t: weak_hist(i) for i, t in enumerate(FILLERS)})
res_w, log_w = run_scan(CANDS + FILLERS, hists2, params)
b = res_w["breadth"]
assert b["pct"] is not None and b["pct"] < 35, b
assert b["risk_factor"] == 0.25, b
df_w = res_w["df"]
assert len(df_w) > 0
for _, row in df_w.iterrows():
    assert "risk_throttled" in (row["flags"] or ""), row["ticker"]
    assert abs(row["risk_EUR"] - MAX_RISK * 0.25) / (MAX_RISK * 0.25) < 0.01, \
        (row["ticker"], row["risk_EUR"])
assert any("defensive mode" in l for l in log_w), log_w
print(f"breadth throttle OK: {b['pct']}% breadth -> 0.25x, flags + sizing scaled")

print("\nALL SIZING-MATH TESTS PASSED")
