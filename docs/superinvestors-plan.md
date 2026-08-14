# Superinvestor holdings (13F) — plan for the developers

Requested 2026-08-14: "identify stocks based on where super investors put
their money — Dataroma, for example." This document is the research and
the plan, per the CLAUDE.md rules (search first; record the decision).
Nothing below is implemented yet.

## What this adds, in one sentence

A page and a per-ticker line that answer, from SEC filings: *which of
the ~30 tracked managers held this stock at quarter end, and what did
they do with it last quarter* — facts with dates on them, not a signal.

## The honesty box (write it before the code)

These four sentences must ship on every surface that renders this data,
because each one is a way the data lies to a casual reader:

- **It is old on arrival.** A 13F shows positions as of quarter end,
  filed up to 45 days later. On its freshest day the snapshot is 45
  days stale; the day before the next batch lands it is ~135. Every
  render carries both dates (`period`, `filed`), never "current".
- **It is long-US-equity only.** No shorts, no cash, no bonds, no
  non-US listings. A manager hedged flat against a name renders
  identically to a true believer. The page says so.
- **Absence proves nothing.** Below-threshold positions, non-US
  listings and confidential-treatment holdings are all invisible.
  A ticker no tracked manager holds gets *no line*, never "no
  superinvestor owns this".
- **It scores nothing.** Like the pattern badge: informational only,
  zero points, until the existing holdout framework measures a cloned
  edge on data it was not chosen on. The academic "copycat" literature
  is mixed, and this project does not import other people's p-values.
  That backtest is explicitly out of scope for this round.

## Research — what exists (decision table)

| Candidate | What it is | Decision |
|---|---|---|
| Dataroma + its GitHub scrapers ([op7ic/Dataroma-Analyzer](https://github.com/op7ic/Dataroma-Analyzer), [Destruct-Portfolio/Dataroma-Scraper](https://github.com/Destruct-Portfolio/Dataroma-Scraper), [J700070/WhalePortfolioAnalyzer](https://github.com/J700070/WhalePortfolioAnalyzer)) | Hand-curated roster of ~81 managers, HTML only, no API, data itself is third-hand from EDGAR | **Reject as a source; borrow the one thing it adds** — the idea of a small curated manager list, checked in as CIKs. The filings come from EDGAR, the primary source, which this repo already queries politely (credit.py sets the UA and paces itself). Scraping a curator is fragile and inherits their errors invisibly. |
| [edgartools](https://github.com/dgunning/edgartools) `ThirteenF` | Full 13F parsing incl. pre-2013 fixed-width TXT | **Borrow with attribution, again** — already rejected as a dependency this round (too heavy for the 512MB instance, see review-log round 4). We need only current-quarter filings, which are structured XML; the infotable field layout borrowed into ~40 lines of stdlib `xml.etree`. |
| sec-api.io, WhaleWisdom | Paid APIs | Out of scope (paid). |
| CUSIP→ticker via [OpenFIGI](https://www.openfigi.com/api/documentation) | Free API, key required; accepts CUSIP as input, returns ticker | Upgrade path only. A new key and a new external dependency for a mapping the SEC gives away (next row). |
| CUSIP→ticker via [SEC fails-to-deliver files](https://www.sec.gov/data-research/sec-markets-data/fails-deliver-data) | Pipe-delimited half-month text files: settlement date \| CUSIP (9) \| ticker \| qty \| name \| price. Free, no key, same origin and politeness rules as everything else we fetch from the SEC | **Adopt as the mapping source.** 13F rows carry CUSIPs, our books carry tickers; one recent FTD file maps nearly everything that actually trades. Measure the unmapped remainder on the first real run and name the number; if it disappoints, OpenFIGI is the documented fallback. An unmapped CUSIP renders by issuer name from the filing itself — never a guessed ticker. |

**Build fresh** (nothing suitable exists at this repo's weight class):
the fetch-parse-diff-publish pipeline itself. It is small, and the
pieces it composes are all primary sources.

## Design

### 1. `superinvestors.py` — new pure module

- `MANAGERS`: a checked-in list of ~25–35 `{cik, name, note}` dicts
  (Berkshire Hathaway CIK 1067983, Pershing Square, Scion, Baupost,
  Fundsmith, Himalaya, …). Curated by hand from Dataroma's public
  roster, once; manager lists change rarely. A wrong CIK is caught by
  cross-checking the filer name in the submissions JSON against the
  checked-in name — mismatch refuses the manager for that run, loudly.
- `latest_13f(cik)`: `data.sec.gov/submissions/CIK##########.json` →
  the two newest 13F-HR periods (amendments preferred over originals
  for the same `periodOfReport`) → filing index → infotable XML →
  rows `{cusip, issuer, class, value_usd, shares, put_call}`.
- `cusip_map()`: newest FTD half-month file → `{cusip9: ticker}`.
- `book(managers, mapping)`: per manager `{name, cik, period, filed,
  n_positions, positions[...]}` and per ticker `{holders, added,
  increased, trimmed, exited}` — the deltas are why two quarters are
  fetched. Rows with `put_call` set are **excluded from holder counts**
  and listed separately, labelled — a put is not a holding.

### 2. The runner — a separate weekly workflow, not the daily scan

`thirteenf.yml`, weekly (13F data moves in bursts around the four
45-day deadlines — Feb/May/Aug/Nov 14; today, 14 Aug, is the Q2'26
deadline, so the first real run lands on maximally fresh data).
~35 managers × 2 filings × 3 small GETs + one FTD file ≈ ~200 requests
— minutes, inside the pacing credit.py already implements. Publishes
`investors13f.json` to the screener-data branch; `index.json` gains a
key. **Check, don't assume, that the carry-forward loop covers the new
file** (per MISTAKES.md: the workflow's `ref:` pin and the publish loop
have both surprised an agent before). Keeping it out of
`scheduled_scan.py` leaves the daily scan's time budget untouched.

### 3. The web app

- `_inv13f_pub` / `_inv13f_book()` cloned from the `_liq_book` warm-book
  pattern; wired into the warm loop and `/published`'s `loaded` dict.
- `/investors`: per-manager cards (period + filed dates at the top,
  top-10 by value, the quarter's adds/trims), one per-ticker roll-up
  table ("held by N of M tracked; K added last quarter") using the
  existing shared `sortRows`/`wireSortable` helpers, and the honesty
  box rendered on the page itself.
- `/check` and `/today` rows: one **note-level** line — "held by N
  tracked managers (13F as of 30 Jun, filed by 14 Aug — up to 45 days
  stale)". Zero score effect. No line when N would be zero.

### 4. Tests (offline fixtures, `fake_sec`-style like test_credit.py)

- Infotable XML fixture parses to pinned rows; amendment beats original
  for the same period; `put_call` rows excluded from counts, labelled.
- FTD fixture maps; an unmapped CUSIP renders the issuer name and never
  a guessed ticker.
- Delta math pinned on a two-quarter fixture (added/increased/trimmed/
  exited, one case each).
- Filer-name mismatch against `MANAGERS` refuses that manager loudly.
- Page robustness: `/investors` returns 200 on an empty/absent book;
  a ticker absent from the roll-up produces no line on `/check`.
- Staleness: a book whose newest `filed` is > ~120 days old renders
  with a stale-window banner, not silently.

### 5. Docs that ship with it

- `KNOWN_ISSUES.md`: the four honesty-box facts, written only when the
  published data exists (data first, wording after — round-4 rule).
- `docs/review-log.md`: the decision table above, appended.

## What this deliberately does not do

- No conviction scores, no cloning weights, no per-manager performance
  claims.
- No Dataroma scraping — their curation is a person; the filings are
  the source.
- No EU equivalent — no EU 13F analogue exists, and the page says so
  rather than implying the roll-up covers European managers.

## Order of work (each step ships alone, full suite green before each)

1. `superinvestors.py` + fixtures + tests — offline, zero deploy risk.
2. `thirteenf.yml` + first published `investors13f.json`; verify on the
   data branch and via a **forced** `/published/refresh` (MISTAKES.md:
   a cache hit and "the fix didn't apply" look identical from outside).
3. Loader + `/investors` page.
4. The `/check` + `/today` note line.
5. KNOWN_ISSUES + review-log wording.

## End-to-end verification

- Dispatch `thirteenf.yml`; confirm `investors13f.json` on the data
  branch; forced refresh; `/investors` renders a real manager whose
  `period`/`filed` dates match EDGAR's own filing page for that CIK.
- Berkshire sanity pin: its current top holding appears with a value in
  the right order of magnitude against EDGAR's own rendering.
- A ticker held by k managers shows the same k on `/check`.
