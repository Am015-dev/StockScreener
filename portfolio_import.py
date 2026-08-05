"""Reconstruct current holdings + cash from a broker transaction-history CSV.

Supports the Revolut export format:
    Date,Ticker,Type,Quantity,Price per share,Total Amount,Currency,FX Rate
with event types BUY/SELL (market/limit/stop), CASH TOP-UP/WITHDRAWAL,
DIVIDEND, CUSTODY FEE (+ reversal), REWARD, STOCK SPLIT (quantity delta),
MERGER - STOCK, CORRECTION, and broker-migration TRANSFER rows (restatements
of existing positions — ignored, otherwise every position double-counts).

Also accepts a simple holdings CSV (ticker,shares,cost columns) as a shortcut.

Average-cost method: sells reduce the cost pool proportionally; splits and
mergers adjust quantity but not the cost pool. Nothing is persisted — the
caller gets holdings + cash back and the UI keeps them client-side.
"""

import csv
import io
import re

_NUM = re.compile(r"-?\d+(?:\.\d+)?")
_ISIN = re.compile(r"^[A-Z]{2}[A-Z0-9]{9}\d$")


def _amount(s) -> float:
    m = _NUM.search(str(s or ""))
    return float(m.group()) if m else 0.0


def _norm(s) -> str:
    return re.sub(r"[^a-z0-9]", "", str(s or "").lower())


def parse_portfolio_csv(text: str) -> dict:
    """Returns {"holdings": [{ticker, shares, cost}], "cash_eur": float,
    "notes": [str], "stats": {...}}. Raises ValueError on unusable input."""
    reader = csv.reader(io.StringIO(text))
    rows = [r for r in reader if any(c.strip() for c in r)]
    if not rows:
        raise ValueError("empty file")
    header = [_norm(c) for c in rows[0]]

    def col(*names):
        for n in names:
            if n in header:
                return header.index(n)
        return None

    i_type = col("type")
    i_ticker = col("ticker", "symbol")
    i_qty = col("quantity", "shares", "qty")
    i_price = col("pricepershare", "price", "cost", "costbasis", "avgcost")
    i_total = col("totalamount", "total", "amount")
    i_ccy = col("currency")
    i_fx = col("fxrate", "fx")

    if i_ticker is None or i_qty is None:
        raise ValueError("no ticker/shares columns found — expected a Revolut "
                         "transaction export or a simple ticker,shares,cost CSV")

    # ---- simple holdings list (no Type/Total Amount columns) ----
    if i_type is None or i_total is None:
        holdings = []
        for r in rows[1:]:
            t = (r[i_ticker] or "").strip().upper()
            sh = _amount(r[i_qty]) if i_qty < len(r) else 0.0
            cb = _amount(r[i_price]) if i_price is not None and i_price < len(r) else 0.0
            if t and sh > 0:
                holdings.append({"ticker": t, "shares": round(sh, 6), "cost": round(cb, 4)})
        if not holdings:
            raise ValueError("no positive positions found")
        return {"holdings": holdings, "cash_eur": 0.0,
                "notes": ["Parsed as a simple holdings list (no cash information)."],
                "stats": {"rows": len(rows) - 1, "format": "simple"}}

    # ---- full transaction history ----
    pos: dict[str, dict] = {}      # ticker -> {qty, cost_total, ccy}
    cash: dict[str, float] = {}    # currency -> amount
    last_fx: dict[str, float] = {} # currency -> last seen FX rate (ccy per EUR)
    notes, unknown_types = [], set()
    n_events = 0

    for r in rows[1:]:
        if len(r) <= max(i_type, i_ticker, i_qty, i_total):
            continue
        typ = (r[i_type] or "").strip().upper()
        ticker = (r[i_ticker] or "").strip().upper()
        qty = _amount(r[i_qty])
        total = _amount(r[i_total])
        ccy = (r[i_ccy] or "").strip().upper() if i_ccy is not None and i_ccy < len(r) else "USD"
        if i_fx is not None and i_fx < len(r):
            fx = _amount(r[i_fx])
            if fx > 0:
                last_fx[ccy] = fx
        n_events += 1

        def cash_add(delta):
            cash[ccy] = cash.get(ccy, 0.0) + delta

        if typ.startswith("TRANSFER"):
            continue  # custody migration: restates positions, moves no value
        if typ.startswith("BUY"):
            price = _amount(r[i_price]) if i_price is not None else 0.0
            p = pos.setdefault(ticker, {"qty": 0.0, "cost_total": 0.0, "ccy": ccy})
            p["qty"] += qty
            p["cost_total"] += qty * price
            p["ccy"] = ccy
            cash_add(-abs(total))
        elif typ.startswith("SELL"):
            p = pos.setdefault(ticker, {"qty": 0.0, "cost_total": 0.0, "ccy": ccy})
            if qty > p["qty"] + 1e-9:
                notes.append(f"{ticker}: sold more than tracked ({qty:.4f} > "
                             f"{p['qty']:.4f}) — history may be incomplete")
                qty = p["qty"]
            if p["qty"] > 0:
                p["cost_total"] *= (1 - qty / p["qty"])
            p["qty"] -= qty
            cash_add(abs(total))
        elif typ.startswith(("STOCK SPLIT", "MERGER", "CORRECTION")):
            if ticker:
                p = pos.setdefault(ticker, {"qty": 0.0, "cost_total": 0.0, "ccy": ccy})
                p["qty"] += qty  # recorded as a signed quantity delta
        elif typ.startswith(("CASH TOP-UP", "CASH WITHDRAWAL", "CUSTODY FEE",
                             "DIVIDEND", "REWARD")):
            cash_add(total)  # already signed in the export
        else:
            unknown_types.add(typ)

    if unknown_types:
        notes.append("Ignored event types: " + ", ".join(sorted(unknown_types)))

    holdings, suffix_watch = [], []
    for t, p in sorted(pos.items(), key=lambda kv: -kv[1]["cost_total"]):
        if p["qty"] < 1e-6:
            continue
        avg_cost = p["cost_total"] / p["qty"] if p["qty"] > 0 else 0.0
        holdings.append({"ticker": t, "shares": round(p["qty"], 6),
                         "cost": round(avg_cost, 4)})
        if _ISIN.match(t):
            notes.append(f"{t}: looks like an ISIN — replace with its Yahoo ticker")
        elif p["ccy"] != "USD" and "." not in t:
            suffix_watch.append(t)
    if suffix_watch:
        notes.append("Non-US listings may need a Yahoo suffix (e.g. BAYN → BAYN.DE, "
                     "VUAA → VUAA.DE): " + ", ".join(suffix_watch))

    cash_eur = 0.0
    for ccy, amt in cash.items():
        if ccy == "EUR":
            cash_eur += amt
        else:
            fx = last_fx.get(ccy)  # ccy per EUR, e.g. EURUSD for USD
            cash_eur += amt / fx if fx else amt
    cash_eur = round(max(cash_eur, 0.0), 2)

    if not holdings and cash_eur <= 0:
        raise ValueError("parsed the file but found no open positions or cash")
    return {"holdings": holdings, "cash_eur": cash_eur, "notes": notes,
            "stats": {"rows": n_events, "open_positions": len(holdings),
                      "cash_by_ccy": {k: round(v, 2) for k, v in cash.items()},
                      "format": "transactions"}}
