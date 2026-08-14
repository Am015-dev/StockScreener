"""Where superinvestors put their money — read straight from SEC 13F-HR
filings, never from a curator's scrape.

Borrowed with attribution, per CLAUDE.md's never-reinvent-the-wheel rule:
the IDEA of a small, curated manager roster (not the code, not the data)
comes from Dataroma (dataroma.com). Every filing, every number and every
mapping in this module is fetched fresh from SEC EDGAR directly — Dataroma
is not a data source here, it is where the idea to build this came from.

What a 13F actually is, and what it is not:
  - Filed by any US-registered investment manager holding >$100M in
    US-listed equities, within 45 days of quarter end. It lists LONG
    positions only — no shorts, no cash, no bonds, no non-US listings.
    A manager hedged flat against a name renders identically to a true
    believer, because the filing cannot tell the two apart.
  - It is old on arrival. Freshest possible: 45 days after quarter end.
    Stalest, the day before the next quarter's filings land: ~135 days.
  - Absence proves nothing. Below-threshold positions, non-US listings
    and confidential-treatment holdings are all invisible to this filing.

This module fetches, parses and rolls the data up. It computes nothing
that resembles a signal, a score or a recommendation — see ranking.py and
KNOWN_ISSUES.md for why that boundary is enforced project-wide.
"""
from __future__ import annotations

import io
import xml.etree.ElementTree as ET
import zipfile

INFOTABLE_NS = "{http://www.sec.gov/edgar/document/thirteenf/informationtable}"

# A small, hand-curated roster, in the spirit of Dataroma's — not a copy
# of it. Every CIK below was looked up and cross-checked against SEC
# EDGAR's own submissions record (name match, and a 13F-HR on file within
# roughly the last year) before being checked in here; several
# plausible-looking CIKs found via SEC's company search were REJECTED
# during that check because their most recent 13F was years stale (a
# firm renamed or re-registered under a new CIK, and the old one just
# stopped filing) — Appaloosa is the clearest case: CIK 1006438
# ("APPALOOSA MANAGEMENT LP") last filed a 13F in 2016; the fund now
# files as "Appaloosa LP" under CIK 1656456, which is the one below.
# fetch_manager() re-checks the filer name on every run for exactly this
# reason — a CIK that is right today can be reassigned or renamed later,
# and a silent mismatch would misattribute someone else's holdings.
MANAGERS = [
    {"cik": 1067983, "name": "BERKSHIRE HATHAWAY INC"},
    {"cik": 1336528, "name": "Pershing Square Capital Management, L.P."},
    {"cik": 1061768, "name": "BAUPOST GROUP LLC/MA"},
    {"cik": 1536411, "name": "Duquesne Family Office LLC"},
    {"cik": 1656456, "name": "Appaloosa LP"},
    {"cik": 1040273, "name": "Third Point LLC"},
    {"cik": 1167483, "name": "TIGER GLOBAL MANAGEMENT LLC"},
    {"cik": 1350694, "name": "Bridgewater Associates, LP"},
    {"cik": 1112520, "name": "AKRE CAPITAL MANAGEMENT LLC"},
    {"cik": 1036325, "name": "DAVIS SELECTED ADVISERS"},
    {"cik": 1510387, "name": "Gotham Asset Management, LLC"},
    {"cik": 1709323, "name": "Himalaya Capital Management LLC"},
    {"cik": 1061165, "name": "LONE PINE CAPITAL LLC"},
    {"cik": 1103804, "name": "VIKING GLOBAL INVESTORS LP"},
    {"cik": 807985, "name": "SOUTHEASTERN ASSET MANAGEMENT INC/TN/"},
    {"cik": 949509, "name": "OAKTREE CAPITAL MANAGEMENT LP"},
    {"cik": 883965, "name": "WEITZ INVESTMENT MANAGEMENT, INC."},
    {"cik": 1325447, "name": "First Eagle Investment Management, LLC"},
    {"cik": 1096343, "name": "MARKEL GROUP INC."},
    {"cik": 1135730, "name": "COATUE MANAGEMENT LLC"},
    {"cik": 1037389, "name": "RENAISSANCE TECHNOLOGIES LLC"},
    {"cik": 1423053, "name": "CITADEL ADVISORS LLC"},
    {"cik": 1217541, "name": "Diamond Hill Capital Management, LLC (Investment Advisor)"},
    {"cik": 1427008, "name": "Smead Capital Management, Inc."},
]


def _group_periods(recent: dict, n_periods: int) -> list[dict]:
    """The submissions JSON's `recent` block, collapsed to at most
    n_periods {period, filed, form, accession} entries, newest first.

    An amendment (13F-HR/A) supersedes the original for the same
    reportDate. Rather than special-case "amendment vs original", this
    just keeps whichever filing for a given reportDate has the LATEST
    filingDate — confirmed against a real case (Berkshire Hathaway,
    reportDate 2025-03-31: a 13F-HR filed 2025-05-15 and a 13F-HR/A for
    the same period filed 2025-08-14) that this picks the amendment
    correctly with no extra logic.
    """
    forms = recent.get("form") or []
    dates = recent.get("filingDate") or []
    report_dates = recent.get("reportDate") or []
    accessions = recent.get("accessionNumber") or []
    by_period: dict[str, dict] = {}
    for f, filed, period, acc in zip(forms, dates, report_dates, accessions):
        if not f.startswith("13F-HR"):
            continue
        cur = by_period.get(period)
        if cur is None or filed > cur["filed"]:
            by_period[period] = {"period": period, "filed": filed,
                                 "form": f, "accession": acc}
    return sorted(by_period.values(), key=lambda r: r["period"],
                 reverse=True)[:n_periods]


def _infotable_document(cik: int, accession: str, get_json) -> str:
    """The filename of the raw infoTable XML inside a 13F filing's
    archive.

    NOT `primary_doc.xml` — that is the cover page the submissions JSON
    itself points at (`xslForm13F_X02/primary_doc.xml`), and it holds no
    holdings at all. Confirmed against a real filing (Berkshire
    Hathaway, accession 0001193125-26-226661): the archive root holds
    exactly `primary_doc.xml` plus one arbitrarily-named XML file
    (there, `53405.xml`) that IS the holdings table. This takes the
    first XML document that is not `primary_doc.xml`; more than one
    such candidate has never been observed and is refused rather than
    guessed at.
    """
    acc_nodash = accession.replace("-", "")
    idx = get_json(f"https://www.sec.gov/Archives/edgar/data/"
                   f"{int(cik)}/{acc_nodash}/index.json")
    items = ((idx or {}).get("directory") or {}).get("item") or []
    candidates = [it["name"] for it in items
                 if it.get("name", "").lower().endswith(".xml")
                 and it["name"].lower() != "primary_doc.xml"]
    if not candidates:
        raise RuntimeError(f"no infoTable document in accession {accession}")
    if len(candidates) > 1:
        raise RuntimeError(
            f"accession {accession} has {len(candidates)} candidate "
            f"infoTable documents — refusing to guess which one")
    return candidates[0]


def parse_infotable(xml_text: str) -> dict:
    """Parse one 13F infoTable XML into positions (by CUSIP, summed) and
    options (kept separate, never summed into a position).

    Real filings can list the SAME CUSIP on multiple rows: a manager
    filing one combined 13F across several accounts or subsidiaries
    reports one infoTable row per account. Confirmed against Berkshire
    Hathaway's own 2026-03-31 filing: 90 infoTable rows, 29 unique
    CUSIPs. Rows are summed by CUSIP so a "position" here means the
    whole filer's stake, not one account inside it.

    A row carrying a putCall value is an OPTIONS position, not equity —
    the SEC's own instructions have it reuse the underlying security's
    CUSIP, so summing it into `positions` would silently inflate the
    reported equity value with a derivative's notional. It is kept in
    `options` instead, labelled by direction.
    """
    root = ET.fromstring(xml_text)
    positions: dict[str, dict] = {}
    options: list[dict] = []

    def text(row, tag):
        el = row.find(f"{INFOTABLE_NS}{tag}")
        return el.text.strip() if el is not None and el.text else None

    for row in root.iter(f"{INFOTABLE_NS}infoTable"):
        cusip = text(row, "cusip")
        if not cusip:
            continue
        issuer = text(row, "nameOfIssuer") or ""
        cls = text(row, "titleOfClass") or ""
        value_raw = text(row, "value")
        value_usd = int(value_raw) if value_raw else 0
        shares, share_type = None, None
        shrs_el = row.find(f"{INFOTABLE_NS}shrsOrPrnAmt")
        if shrs_el is not None:
            amt_el = shrs_el.find(f"{INFOTABLE_NS}sshPrnamt")
            typ_el = shrs_el.find(f"{INFOTABLE_NS}sshPrnamtType")
            shares = (int(amt_el.text) if amt_el is not None and amt_el.text
                     else None)
            share_type = typ_el.text if typ_el is not None else None
        put_call = text(row, "putCall")
        if put_call:
            options.append({
                "cusip": cusip, "issuer": issuer, "class": cls,
                "type": put_call.upper(), "value_usd": value_usd,
                "shares": shares if share_type == "SH" else None})
            continue
        pos = positions.setdefault(cusip, {
            "issuer": issuer, "class": cls, "value_usd": 0,
            "shares": 0, "share_type": share_type})
        pos["value_usd"] += value_usd
        if share_type == "SH" and shares is not None:
            pos["shares"] += shares
    return {"positions": positions, "options": options}


def fetch_manager(manager: dict, get_json, get_text, n_periods: int = 2) -> dict:
    """One manager's last n_periods of 13F holdings.

    get_json(url) -> dict, get_text(url) -> str; both raise on failure.
    Returns {cik, name, filings: [...]} on success. On any failure the
    result instead carries an "error" string and no filings — this
    never raises, so one bad manager cannot abort a whole run (the same
    discipline credit.py's per-ticker fetches already follow).

    The filer name on record is checked against what is checked into
    MANAGERS on every call, not just once at roster-curation time — a
    CIK that answers correctly today can be renamed or reassigned
    later, and reading a mismatch as "fine, just use the CIK" would
    silently attribute someone else's holdings to the checked-in name.
    """
    cik = manager["cik"]
    try:
        subs = get_json(f"https://data.sec.gov/submissions/CIK{int(cik):010d}.json")
    except Exception as e:
        return {"cik": cik, "name": manager["name"],
                "error": f"submissions: {type(e).__name__}"}
    filer_name = (subs or {}).get("name") or ""
    if filer_name.strip().upper() != manager["name"].strip().upper():
        return {"cik": cik, "name": filer_name or manager["name"], "error":
                f"filer name mismatch: checked in as {manager['name']!r}, "
                f"SEC has {filer_name!r} — CIK likely wrong or reassigned"}
    recent = (subs.get("filings") or {}).get("recent") or {}
    periods = _group_periods(recent, n_periods)
    if not periods:
        return {"cik": cik, "name": filer_name,
                "error": "no 13F-HR filings on record"}
    filings = []
    for meta in periods:
        entry = dict(meta)
        try:
            doc = _infotable_document(cik, meta["accession"], get_json)
            acc_nodash = meta["accession"].replace("-", "")
            xml_text = get_text(
                f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/"
                f"{acc_nodash}/{doc}")
            entry.update(parse_infotable(xml_text))
        except Exception as e:
            entry["error"] = f"{type(e).__name__}"
        filings.append(entry)
    return {"cik": cik, "name": filer_name, "filings": filings}


def parse_ftd_file(text: str) -> dict[str, list[str]]:
    """One raw pipe-delimited fails-to-deliver text file ->
    {cusip: [symbol, symbol, ...]} — every observed symbol, not deduped.

    cusip_map() picks the most common symbol per CUSIP across however
    many files are merged, so one miscoded row in one file cannot flip
    a mapping on its own: measured against a real file
    (cnsfails202607a.txt), 34 of 13,024 CUSIPs carried more than one
    distinct symbol across its rows.
    """
    out: dict[str, list[str]] = {}
    lines = text.splitlines()
    for line in lines[1:]:      # header: SETTLEMENT DATE|CUSIP|SYMBOL|...
        parts = line.split("|")
        if len(parts) < 3:
            continue
        cusip, sym = parts[1].strip(), parts[2].strip()
        if cusip and sym:
            out.setdefault(cusip, []).append(sym)
    return out


def unzip_ftd(raw_zip: bytes) -> str:
    """The SEC ships each FTD file as a single-entry zip. Kept separate
    from parse_ftd_file so tests can exercise the parser on plain text
    without building a zip."""
    with zipfile.ZipFile(io.BytesIO(raw_zip)) as zf:
        name = zf.namelist()[0]
        return zf.read(name).decode("utf-8", "replace")


def cusip_map(ftd_texts: list[str]) -> dict[str, str]:
    """Merge several FTD files into one CUSIP -> ticker map, keyed by
    whichever symbol appears most often for that CUSIP across all of
    them.

    One recent file alone maps most, not all, of a large filer's book:
    measured against Berkshire Hathaway's 2026-03-31 13F (29 unique
    CUSIPs), one half-month FTD file mapped 27 of 29; three consecutive
    half-months mapped 29 of 29. Call this with the last ~3 files, not
    one — a CUSIP that fails to map after that renders by issuer name
    from the filing itself, never a guessed ticker.
    """
    tally: dict[str, dict[str, int]] = {}
    for text in ftd_texts:
        for cusip, syms in parse_ftd_file(text).items():
            counts = tally.setdefault(cusip, {})
            for s in syms:
                counts[s] = counts.get(s, 0) + 1
    return {cusip: max(counts, key=counts.get) for cusip, counts in tally.items()}


def _classify(newest: dict | None, prior: dict | None,
             threshold: float = 0.05) -> str:
    """added / increased / trimmed / exited / held, for one CUSIP across
    two quarters of ONE manager's positions.

    Compares SHARE COUNT, not dollar value, whenever both quarters
    report one. A 13F's `value` moves with the stock's price as well as
    with what the manager actually did — a flat share count in a name
    that rallied 30% would misreport as "increased 30%" under a
    value-based comparison, crediting the market for the manager's
    inaction. Falls back to value only when a share count is missing or
    not comparable (the position's `sshPrnamtType` was not "SH" in one
    of the two quarters — principal-amount holdings, mainly).
    """
    if newest is None:
        return "exited"
    if prior is None:
        return "added"
    p_shares, n_shares = prior.get("shares"), newest.get("shares")
    if p_shares and n_shares is not None:
        if p_shares <= 0:
            return "added" if n_shares > 0 else "held"
        change = (n_shares - p_shares) / p_shares
    else:
        pv, nv = prior["value_usd"], newest["value_usd"]
        if pv <= 0:
            return "added"
        change = (nv - pv) / pv
    if change > threshold:
        return "increased"
    if change < -threshold:
        return "trimmed"
    return "held"


def book(manager_results: list[dict], cusip_to_ticker: dict) -> dict:
    """Turn each manager's raw filings into the published shape.

    manager_results: fetch_manager() output for however many managers
    were fetched. Entries carrying an "error" (a bad CIK, a filer-name
    mismatch, no filings) are skipped here — a dead manager contributes
    nothing rather than a wrong or stale one silently counting.

    Returns {"managers": [...], "tickers": {...}}. A ticker's key is its
    mapped symbol when cusip_to_ticker has one, else
    "(unmapped) <issuer name from the filing>" — never a guessed symbol.
    """
    managers_out = []
    tickers: dict[str, dict] = {}

    for m in manager_results:
        filings = [f for f in (m.get("filings") or []) if "error" not in f]
        if not filings:
            continue
        newest, prior = filings[0], (filings[1] if len(filings) > 1 else None)
        newest_pos = newest.get("positions") or {}
        prior_pos = (prior or {}).get("positions") or {}
        ranked = sorted(newest_pos.items(), key=lambda kv: kv[1]["value_usd"],
                        reverse=True)
        managers_out.append({
            "cik": m["cik"], "name": m["name"],
            "period": newest["period"], "filed": newest["filed"],
            "n_positions": len(newest_pos),
            "n_options": len(newest.get("options") or []),
            "top": [{"issuer": p["issuer"], "class": p["class"],
                    "value_usd": p["value_usd"], "shares": p["shares"],
                    "ticker": cusip_to_ticker.get(c)}
                   for c, p in ranked[:10]],
        })
        for cusip in set(newest_pos) | set(prior_pos):
            np_, pp_ = newest_pos.get(cusip), prior_pos.get(cusip)
            issuer = (np_ or pp_)["issuer"]
            status = _classify(np_, pp_)
            ticker = cusip_to_ticker.get(cusip)
            key = ticker if ticker else f"(unmapped) {issuer}"
            row = tickers.setdefault(key, {
                "issuer": issuer, "ticker": ticker, "holders": [],
                "added": [], "increased": [], "trimmed": [], "exited": []})
            if status != "exited":
                row["holders"].append(m["name"])
            # "held" (unchanged within the +/-5% band) is not one of the
            # four tracked transitions — it contributes to `holders`
            # above and nothing else, matching the plan's shape
            if status in ("added", "increased", "trimmed", "exited"):
                row[status].append(m["name"])
    return {"managers": managers_out, "tickers": tickers}
