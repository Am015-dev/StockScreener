"""Is today's volatility ordinary, or a regime a reader should size for?

The site already flags one backdrop condition — SPY/Euro Stoxx below its
200-day average (see app.py's _regime_notes) — from data screener.py was
already pulling. This adds the other standard one: the VIX itself, which
this project had never read at all.

CBOE publishes the VIX's daily history as a public CSV, no key, no auth:
https://cdn.cboe.com/api/global/us_indices/daily_prices/VIX_History.csv
(verified 2026-08-14 — DATE,OPEN,HIGH,LOW,CLOSE back to 1990-01-02).
Investigated while reviewing Fincept Terminal
(github.com/Fincept-Corporation/FinceptTerminal, AGPL-3.0) for ideas at
the operator's request: its cboe_vix_data.py hits this same CDN endpoint.
The URL is borrowed with attribution; nothing else is — Fincept is a
C++20/Qt6 desktop app, a different language and runtime entirely, and
this module is a fresh implementation against the public CSV, not a port
of theirs.

Deliberately no fixed "VIX > 30 = fear" band. This project already has a
rule against invented thresholds dressed up as calibration (see
credit.band()'s docstring) and a working answer to the same problem
(credit.percentile()) — so today's level is placed against its own
trailing history instead, the same way a distance-to-default is placed
against its peers rather than a round number picked to sound right.
"""
from __future__ import annotations

import csv
import io
from datetime import datetime

import credit

VIX_CSV_URL = "https://cdn.cboe.com/api/global/us_indices/daily_prices/VIX_History.csv"

# ~5 trading years: long enough that today is not judged against a
# handful of sessions, short enough that a single 2008- or 2020-scale
# spike does not sit in the window forever and permanently raise the bar
# for what counts as "elevated" to a level nobody trading today lived
# through.
LOOKBACK_SESSIONS = 1260

# Only the tails are worth a reader's attention — see _regime_notes()'s
# own rule ("silent on uptrend ... never flagging the normal case"). A
# VIX sitting at its ordinary median says nothing a reader needs told.
HIGH_PCTL = 90
LOW_PCTL = 10


def parse_vix_csv(text: str) -> list[tuple[str, float]]:
    """[(iso_date, close), ...] in file order. Skips any row that will
    not parse rather than raising — a header change or a blank trailing
    line should degrade to "fewer rows read", not a crash."""
    out = []
    for row in csv.DictReader(io.StringIO(text or "")):
        try:
            d = datetime.strptime(row["DATE"].strip(), "%m/%d/%Y").date().isoformat()
            c = float(row["CLOSE"])
        except (KeyError, ValueError, AttributeError, TypeError):
            continue
        if c > 0:
            out.append((d, c))
    return out


def regime(get_text) -> dict | None:
    """Today's VIX close and where it sits against the last ~5 years.

    get_text(url) -> str, or raises — injected so this is testable
    without a network call, the same discipline credit.py's
    fetch_balance_sheet() uses for get_json.

    Returns None on any failure (network, empty file, too little
    history) rather than a partial or zero-filled reading — the caller
    already treats "no regime data" as silence, never as "calm."

    percentile_5y is credit.percentile()'s existing, already-tested
    definition, reused rather than reimplemented: the share of the
    lookback window that closed BELOW today. Direction matters here in
    the opposite way it does on /credit — there, higher percentile means
    safer; here, higher percentile means more days were calmer than
    today, i.e. today is the more stressed one. The caller has to say
    that, this function only measures it.
    """
    try:
        text = get_text(VIX_CSV_URL)
    except Exception:
        return None
    rows = parse_vix_csv(text)
    if len(rows) < 60:
        return None
    window = rows[-LOOKBACK_SESSIONS:]
    as_of, level = window[-1]
    history = [c for _, c in window[:-1]]
    if len(history) < 60:
        return None
    pct = credit.percentile(level, history)
    if pct is None:
        return None
    return {"level": round(level, 2), "as_of": as_of,
            "percentile_5y": pct, "n_obs": len(history)}


def note(v: dict | None) -> str | None:
    """A plain-language line for the front page's regime card, or None
    when today is not in either tail — the same silence-on-normal rule
    _regime_notes() already applies to the SPY/Stoxx flag."""
    if not v or v.get("percentile_5y") is None:
        return None
    pct, level = v["percentile_5y"], v["level"]
    if pct >= HIGH_PCTL:
        return (f"Volatility is elevated — the VIX closed at {level}, higher "
                f"than {pct}% of the last {v['n_obs']} trading days. The "
                f"usual playbook is smaller size and faster exits; nothing "
                f"below enforces that for you.")
    if pct <= LOW_PCTL:
        return (f"Volatility is unusually calm — the VIX closed at {level}, "
                f"lower than {100 - pct}% of the last {v['n_obs']} trading "
                f"days. That describes what just happened, not what happens "
                f"next.")
    return None
