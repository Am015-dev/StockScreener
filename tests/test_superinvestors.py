"""superinvestors.py: 13F parsing, CUSIP mapping, and the delta math that
turns two quarters of filings into added/increased/trimmed/exited.

Every fixture shape below was chosen because a real filing has it, not
because it is convenient to test: multiple infoTable rows sharing one
CUSIP (a manager filing one combined 13F across several accounts),
options rows reusing the underlying's CUSIP, and an amendment filed
weeks after the original for the same reportDate. Confirmed against real
SEC data (Berkshire Hathaway's 2026-03-31 13F-HR and a real FTD file)
before writing this file, not assumed.
"""
import os
import sys
import zipfile
import io
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import superinvestors as si                                     # noqa: E402

NS = "http://www.sec.gov/edgar/document/thirteenf/informationtable"


def infotable_xml(rows: list[dict]) -> str:
    """Build a minimal real-shaped infoTable XML from row dicts."""
    parts = [f'<informationTable xmlns="{NS}">']
    for r in rows:
        put_call = (f"<putCall>{r['put_call']}</putCall>"
                   if r.get("put_call") else "")
        shares_block = ""
        if "shares" in r:
            shares_block = (
                "<shrsOrPrnAmt>"
                f"<sshPrnamt>{r['shares']}</sshPrnamt>"
                f"<sshPrnamtType>{r.get('share_type', 'SH')}</sshPrnamtType>"
                "</shrsOrPrnAmt>")
        parts.append(
            "<infoTable>"
            f"<nameOfIssuer>{r['issuer']}</nameOfIssuer>"
            f"<titleOfClass>{r.get('cls', 'COM')}</titleOfClass>"
            f"<cusip>{r['cusip']}</cusip>"
            f"<value>{r['value']}</value>"
            f"{shares_block}"
            f"{put_call}"
            "</infoTable>")
    parts.append("</informationTable>")
    return "".join(parts)


# ---- parse_infotable: rows sharing a CUSIP are summed ----
# Real shape: Berkshire's own 2026-03-31 13F-HR reports Ally Financial
# (CUSIP 02005N100) across SIX separate infoTable rows — one combined
# filing across several accounts. A parser that took the first row only
# would understate the position by a factor of six.
xml = infotable_xml([
    {"issuer": "ALLY FINL INC", "cusip": "02005N100", "value": 498992850,
     "shares": 12719675},
    {"issuer": "ALLY FINL INC", "cusip": "02005N100", "value": 109996016,
     "shares": 2803875},
    {"issuer": "ALLY FINL INC", "cusip": "02005N100", "value": 165872286,
     "shares": 4228200},
])
parsed = si.parse_infotable(xml)
pos = parsed["positions"]["02005N100"]
assert pos["value_usd"] == 498992850 + 109996016 + 165872286, pos
assert pos["shares"] == 12719675 + 2803875 + 4228200, pos
assert pos["issuer"] == "ALLY FINL INC"
assert parsed["options"] == []
print("multiple infoTable rows for one CUSIP (a combined filing across "
      "several accounts) are summed into one position")

# ---- a putCall row is excluded from positions, kept separately ----
xml2 = infotable_xml([
    {"issuer": "APPLE INC", "cusip": "037833100", "value": 900000000,
     "shares": 5000000},
    {"issuer": "APPLE INC", "cusip": "037833100", "value": 50000000,
     "put_call": "PUT", "shares": 300000},
])
parsed2 = si.parse_infotable(xml2)
assert parsed2["positions"]["037833100"]["value_usd"] == 900000000, \
    "a PUT row's value leaked into the equity position"
assert len(parsed2["options"]) == 1
assert parsed2["options"][0]["type"] == "PUT"
assert parsed2["options"][0]["cusip"] == "037833100"
print("a putCall row never inflates the equity position; kept separate, labelled")

# ---- _group_periods: an amendment supersedes the original for the SAME
# reportDate, picked by taking the latest filingDate per period, not by
# special-casing the form name ----
recent = {
    "form": ["13F-HR", "13F-HR/A", "13F-HR"],
    "filingDate": ["2025-05-15", "2025-08-14", "2025-08-14"],
    "reportDate": ["2025-03-31", "2025-03-31", "2025-06-30"],
    "accessionNumber": ["0000950123-25-005701", "0000950123-25-008361",
                        "0000950123-25-008343"],
}
periods = si._group_periods(recent, n_periods=2)
assert len(periods) == 2
assert periods[0]["period"] == "2025-06-30"
assert periods[1]["period"] == "2025-03-31"
assert periods[1]["accession"] == "0000950123-25-008361", \
    "the amendment (filed later) must win over the original for the same period"
assert periods[1]["form"] == "13F-HR/A"
print("an amendment filed after the original supersedes it for the same period")


# ---- fetch_manager: filer-name mismatch refuses, never silently attributes ----
def fake_get_json(recent_block):
    def get_json(url):
        assert "submissions/CIK" in url or "index.json" in url, url
        if "submissions/CIK" in url:
            return {"name": "SOME OTHER ENTITY ENTIRELY",
                    "filings": {"recent": recent_block}}
        raise AssertionError("should never reach index.json after a name mismatch")
    return get_json


def fail_get_text(url):
    raise AssertionError("should never fetch an infoTable after a name mismatch")


res = si.fetch_manager({"cik": 1656456, "name": "Appaloosa LP"},
                       fake_get_json(recent), fail_get_text)
assert "error" in res, res
assert "mismatch" in res["error"], res
assert "filings" not in res, \
    "a name mismatch must not carry through to holdings"
print("a filer-name mismatch refuses the manager loudly, matching the real "
      "Appaloosa case (old CIK stale since 2016, current CIK verified by name)")


# ---- fetch_manager: a real success path, end to end with fakes ----
def fake_get_json_ok(url):
    if "submissions/CIK" in url:
        return {"name": "Appaloosa LP", "filings": {"recent": {
            "form": ["13F-HR"], "filingDate": ["2026-05-15"],
            "reportDate": ["2026-03-31"],
            "accessionNumber": ["0001656456-26-000001"]}}}
    if "index.json" in url:
        return {"directory": {"item": [
            {"name": "primary_doc.xml"}, {"name": "998877.xml"}]}}
    raise AssertionError(url)


def fake_get_text_ok(url):
    assert url.endswith("998877.xml"), url
    return infotable_xml([
        {"issuer": "AMAZON COM INC", "cusip": "023135106", "value": 700000000,
         "shares": 4000000}])


ok = si.fetch_manager({"cik": 1656456, "name": "Appaloosa LP"},
                      fake_get_json_ok, fake_get_text_ok)
assert "error" not in ok, ok
assert len(ok["filings"]) == 1
assert ok["filings"][0]["positions"]["023135106"]["shares"] == 4000000
print("fetch_manager end to end: submissions -> index.json -> the non-cover "
      "XML document -> parsed positions")

# ---- _infotable_document: never guesses between two candidates ----
try:
    si._infotable_document(1, "0001-26-000001",
                           lambda u: {"directory": {"item": [
                               {"name": "primary_doc.xml"},
                               {"name": "a.xml"}, {"name": "b.xml"}]}})
    raise AssertionError("should have refused with two ambiguous candidates")
except RuntimeError as e:
    assert "refusing to guess" in str(e), e
print("two non-cover XML candidates in one filing refuses rather than guessing")


# ---- CUSIP -> ticker mapping ----
ftd1 = ("SETTLEMENT DATE|CUSIP|SYMBOL|QUANTITY (FAILS)|DESCRIPTION|PRICE\n"
       "20260701|02005N100|ALLY|100|ALLY FINL INC|39.00\n"
       "20260701|023135106|AMZN|50|AMAZON COM INC|200.00\n")
ftd2 = ("SETTLEMENT DATE|CUSIP|SYMBOL|QUANTITY (FAILS)|DESCRIPTION|PRICE\n"
       "20260615|023135106|AMZN|10|AMAZON COM INC|198.00\n"
       # a miscoded row: same cusip, wrong symbol once — must not win
       # against two correct observations of AMZN across the two files
       "20260615|023135106|WRONG|1|AMAZON COM INC|198.00\n")
mapping = si.cusip_map([ftd1, ftd2])
assert mapping["02005N100"] == "ALLY", mapping
assert mapping["023135106"] == "AMZN", \
    ("a single miscoded row flipped the majority-vote mapping: " + str(mapping))
print("cusip_map takes the majority symbol across merged files, immune to "
      "one miscoded row")

# ---- unzip_ftd round-trips a real-shaped single-entry zip ----
buf = io.BytesIO()
with zipfile.ZipFile(buf, "w") as zf:
    zf.writestr("cnsfails202607a.txt", ftd1)
extracted = si.unzip_ftd(buf.getvalue())
assert extracted == ftd1
print("unzip_ftd extracts the single text file the SEC ships inside each zip")


# ---- _classify: shares, not dollar value, decide the delta ----
# The case this exists to catch: a flat share count in a stock whose
# PRICE rallied must read as "held", not "increased" — a value-only
# comparison would credit the market's move to the manager.
newest = {"value_usd": 1_300_000, "shares": 10_000}
prior = {"value_usd": 1_000_000, "shares": 10_000}       # price +30%, shares flat
assert si._classify(newest, prior) == "held", \
    "a 30% price move with a flat share count must not read as 'increased'"
print("a price move alone (flat shares) reads as 'held', never 'increased'")

assert si._classify({"value_usd": 1_100_000, "shares": 11_000},
                    {"value_usd": 1_000_000, "shares": 10_000}) == "increased"
assert si._classify({"value_usd": 800_000, "shares": 8_000},
                    {"value_usd": 1_000_000, "shares": 10_000}) == "trimmed"
assert si._classify({"value_usd": 500_000, "shares": 5_000}, None) == "added"
assert si._classify(None, {"value_usd": 500_000, "shares": 5_000}) == "exited"
# no share count on either side: falls back to value
assert si._classify({"value_usd": 1_200_000, "shares": None},
                    {"value_usd": 1_000_000, "shares": None}) == "increased"
print("added / increased / trimmed / exited pinned; falls back to value only "
      "when neither quarter has a comparable share count")


# ---- book(): end-to-end roll-up across two managers, one quarter each ----
def m(name, cik, newest_positions, prior_positions=None, options=None):
    filings = [{"period": "2026-03-31", "filed": "2026-05-15",
               "positions": newest_positions, "options": options or []}]
    if prior_positions is not None:
        filings.append({"period": "2025-12-31", "filed": "2026-02-15",
                        "positions": prior_positions, "options": []})
    return {"cik": cik, "name": name, "filings": filings}


mgr_a = m("Manager A", 1, {
    "023135106": {"issuer": "AMAZON COM INC", "class": "COM",
                 "value_usd": 1_300_000, "shares": 10_000, "share_type": "SH"},
}, prior_positions={
    "023135106": {"issuer": "AMAZON COM INC", "class": "COM",
                 "value_usd": 1_000_000, "shares": 10_000, "share_type": "SH"},
})
mgr_b = m("Manager B", 2, {
    "023135106": {"issuer": "AMAZON COM INC", "class": "COM",
                 "value_usd": 500_000, "shares": 5_000, "share_type": "SH"},
    # unmapped: no ticker for this cusip in the mapping below
    "999999999": {"issuer": "OBSCURE HOLDCO", "class": "COM",
                 "value_usd": 200_000, "shares": 1_000, "share_type": "SH"},
})
mgr_c_dead = {"cik": 3, "name": "Manager C",
             "error": "filer name mismatch: ..."}   # must contribute nothing

result = si.book([mgr_a, mgr_b, mgr_c_dead], {"023135106": "AMZN"})
assert len(result["managers"]) == 2, \
    "the errored manager must not appear in the roll-up"
amzn = result["tickers"]["AMZN"]
assert set(amzn["holders"]) == {"Manager A", "Manager B"}, amzn
assert amzn["holders"].count("Manager A") + amzn["holders"].count("Manager B") == 2
assert "Manager A" not in amzn["increased"], \
    "Manager A's flat-shares/price-up case must not read as increased"
assert not any("Manager A" in bucket for bucket in
              (amzn["added"], amzn["trimmed"], amzn["exited"])), \
    "an unchanged (held) position must not land in any transition bucket"
obscure_key = "(unmapped) OBSCURE HOLDCO"
assert obscure_key in result["tickers"], \
    "an unmapped CUSIP must render by issuer name, never a guessed ticker"
assert result["tickers"][obscure_key]["ticker"] is None
print("book() rolls up two managers into a per-ticker table; a dead/errored "
      "manager contributes nothing; an unmapped CUSIP renders by issuer name")

# managers_out carries top holdings with tickers attached where mapped
a_out = next(x for x in result["managers"] if x["name"] == "Manager A")
assert a_out["n_positions"] == 1
assert a_out["top"][0]["ticker"] == "AMZN"
print("per-manager card carries top holdings with tickers where mapped")

print("\nALL SUPERINVESTORS TESTS PASSED")
