"""Reading a broker export — 161 lines that decide what the reader owns.

This module had no tests at all, and it is on a request path and handles
money. Three mutations survived the whole suite before these existed:

  - deleting the cost-pool reduction on a SELL, so a partial sale left the
    full cost in place and every later average cost was too high
  - zeroing the cash total, so every import reported no free cash
  - flipping a SELL's cash sign, so selling DEBITED the account

Each of those changes what the site tells someone they own, which is the
one input the whole product reads from.
"""
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TMP = tempfile.mkdtemp(prefix="import_")
os.environ.setdefault("MARKET_DB", os.path.join(TMP, "m.db"))
os.environ.setdefault("SCREENER_CACHE_DB", os.path.join(TMP, "c.db"))
sys.path.insert(0, str(ROOT))

import portfolio_import as pi


def by_ticker(res):
    return {h["ticker"]: h for h in res["holdings"]}


# ---- the simple shape: ticker, shares, cost ----
simple = pi.parse_portfolio_csv(
    "ticker,shares,cost\nNVDA,10,118.20\nKO,30,61.05\n")
h = by_ticker(simple)
assert h["NVDA"]["shares"] == 10 and h["NVDA"]["cost"] == 118.20, h
assert h["KO"]["shares"] == 30
print("a plain ticker/shares/cost file reads back exactly")


# ---- a transaction history: average cost must survive a partial sale ----
# 10 @ 100 then 10 @ 200 is 20 shares at an average of 150. Selling 10
# leaves 10 shares STILL at 150 — the cost pool has to come down with the
# quantity. Leaving it whole reports 10 shares at 300.
txn = """Type,Ticker,Quantity,Price per share,Total Amount,Currency,FX Rate
BUY,ACME,10,100,1000,USD,1
BUY,ACME,10,200,2000,USD,1
SELL,ACME,10,250,2500,USD,1
"""
res = pi.parse_portfolio_csv(txn)
acme = by_ticker(res)["ACME"]
assert abs(acme["shares"] - 10) < 1e-6, acme
assert abs(acme["cost"] - 150.0) < 1e-4, \
    f"average cost after a partial sale is {acme['cost']}, not 150 — the cost " \
    f"pool did not come down with the shares"
print(f"a partial sale leaves {acme['shares']:g} shares at an average cost of "
      f"{acme['cost']:.2f}, not the whole original pool")


# ---- and a full exit leaves nothing behind ----
out = pi.parse_portfolio_csv(txn + "SELL,ACME,10,260,2600,USD,1\n")
assert "ACME" not in by_ticker(out), out["holdings"]
print("a position sold out completely does not linger in the holdings")


# ---- cash: a sale credits, a purchase debits ----
cash_txn = """Type,Ticker,Quantity,Price per share,Total Amount,Currency,FX Rate
CASH TOP-UP,,0,0,10000,EUR,1
BUY,ACME,10,100,1000,EUR,1
SELL,ACME,4,150,600,EUR,1
"""
res = pi.parse_portfolio_csv(cash_txn)
assert res["cash_eur"] > 0, "an import that reports no free cash sizes nothing"
assert abs(res["cash_eur"] - 9600.0) < 1.0, \
    f"cash is {res['cash_eur']}, not 9600 — a buy must debit and a sell credit"
print(f"cash after a top-up, a buy and a sale: €{res['cash_eur']:,.0f}")

# the direction has to be right, not merely non-zero
more_sales = pi.parse_portfolio_csv(cash_txn + "SELL,ACME,6,150,900,EUR,1\n")
assert more_sales["cash_eur"] > res["cash_eur"], \
    "selling more must leave MORE cash, not less"
print("selling more leaves more cash — the sign is right, not just non-zero")


# ---- selling more than was tracked is reported, not silently negative ----
odd = pi.parse_portfolio_csv(
    "Type,Ticker,Quantity,Price per share,Total Amount,Currency,FX Rate\n"
    "BUY,ACME,5,100,500,USD,1\nSELL,ACME,9,120,1080,USD,1\n")
assert all(h["shares"] >= 0 for h in odd["holdings"]), odd["holdings"]
assert any("sold more than tracked" in n for n in odd["notes"]), odd["notes"]
print("selling more than the file recorded is reported, never a negative holding")


# ---- the app's own header-less export round-trips ----
# templates/index.html writes holdings back as bare "ticker, shares, cost"
# lines with no header row (matches the textarea placeholder). A user who
# saves and re-uploads their own data must not get a hard refusal.
headerless = pi.parse_portfolio_csv("AAPL, 10, 172.50\nMSFT, 5, 300.00\n")
hh = by_ticker(headerless)
assert hh["AAPL"]["shares"] == 10 and hh["AAPL"]["cost"] == 172.50, hh
assert hh["MSFT"]["shares"] == 5, hh
print("a header-less ticker/shares/cost export round-trips without a header row")


# ---- unusable input is refused with a reason ----
for bad, why in ((""                      , "empty file"),
                 ("a,b,c\n1,2,3\n"        , "no ticker/shares columns")):
    try:
        pi.parse_portfolio_csv(bad)
        raise AssertionError(f"{why}: should have been refused")
    except ValueError as e:
        assert why.split()[0] in str(e).lower() or "no ticker" in str(e).lower(), e
print("an empty file and a file with no recognisable columns are both refused")


# ---- a ticker that needs a Yahoo suffix is flagged, not silently wrong ----
eu = pi.parse_portfolio_csv(
    "Type,Ticker,Quantity,Price per share,Total Amount,Currency,FX Rate\n"
    "BUY,BAYN,10,25,250,EUR,1\n")
assert any("suffix" in n.lower() for n in eu["notes"]), eu["notes"]
isin = pi.parse_portfolio_csv(
    "Type,Ticker,Quantity,Price per share,Total Amount,Currency,FX Rate\n"
    "BUY,IE00B4L5Y983,10,25,250,EUR,1\n")
assert any("ISIN" in n for n in isin["notes"]), isin["notes"]
print("a bare European ticker and an ISIN are both flagged rather than looked "
      "up as US symbols")

print("\nALL PORTFOLIO-IMPORT TESTS PASSED")
