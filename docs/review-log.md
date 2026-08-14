# Site review log

Each round: an independent scorer grades the LIVE deployment against
`.claude/skills/site-review/SKILL.md`. Below 80 → fix, deploy through the
release gate, re-score with a fresh scorer.

## Round 1 — 2026-08-12, build 40a6756 — **51/100**

| Section | Score | The evidence that cost the points |
|---|---|---|
| Decisive answers | 20/25 | ZZZZ (nonexistent) collected a green "no earnings due" |
| Clarity | 6/20 | BRK.B "not a US filer" vs BRK-B "not modelled"; 179 vs 180 measured, same minute; "figures below" that never render |
| Value density | 10/20 | first screen was questions; nothing framed as changed today |
| Honesty w/o hedging | 12/15 | the same 30-word no-percentage paragraph on every check |
| Browser correctness | 2/10 | BABAF #1 "closest to trouble" on an 8× unit error; favicon 404; /full sideways scroll |
| Coverage | 1/10 | GOOGL, GOOG, META, XOM, V all "cannot assess"; SJM (its own pick) unmeasurable |

Root causes found while fixing: the universe cap cut positionally across
sector blocks (all of Communication Services and Energy dropped while
every Technology name stayed); no unit cross-check between the SEC share
count and the price line.

Changes for round 2: size-ordered universe cut with caps captured from
the screen; equity-vs-cap cross-check plus OTC-foreign-line refusal
(measured entries without the check re-measured, not carried); BRK.B/
BRK-B aliasing; unknown symbols refused in /check; one measured-count
everywhere; held-only wording fixed; favicon; overflow fixed; per-check
disclaimer replaced by the report's single foldable one; changelog
filtered to entries a visitor can use.

## Round 2 — 2026-08-12, build 6f2d0166 — **89/100** (self-scored, see caveat)

| Section | R1 | R2 | Evidence on live |
|---|---|---|---|
| Decisive answers | 20 | 24 | every check opens with a verdict; ghost tickers refused |
| Clarity | 6 | 17 | 389 home vs "388 other" agree; BRK.B=BRK-B; leverage denominator on the page |
| Value density | 10 | 17 | first screen is "Reporting this week"; holdings ask is a 31-word line |
| Honesty w/o hedging | 12 | 14 | per-check disclaimer removed; one foldable explanation on the report |
| Browser correctness | 2 | 9 | BABAF refused not ranked; favicon 200; no overflow; no JS errors; gate green |
| Coverage | 1 | 8 | 1,794 priced (was 1,488), 828 credit entries, GOOGL/META/XOM measured |

All eleven round-1 defects verified fixed against live responses.

Found and fixed DURING round 2, all self-inflicted:
- cold start: the instance served "the SEC's list could not be read" for a
  minute after every deploy while the same data sat on its own disk;
  books now load from the shipped copies at import
- the changelog filter emptied the page (a shallow clone holds only
  merges); now falls back to raw history and says so
- the unknown-symbol gate called AAPL a typo during warm-up

**Caveat on this score:** it is self-assessed. The independent scorer
terminated on an API session limit partway through (it had verified the
count consistency). The skill requires an independent scorer, so this
number should be re-taken by a fresh reviewer before it is trusted as
the round-2 result.

Known and deliberate: the credit model refuses banks and insurers by
SIC, so a bank-heavy portfolio gets overlap and earnings answers but no
credit standings — correct for Merton, and a real coverage limit.

---

## Round 3 — the pattern sweep, and the decision page

Not a site score. This records what the pattern framework returned the
first time it was run properly, because the number is the deliverable.

### What was run

400 US companies, 501 trading sessions (2024-08-13 to 2026-08-12), 11
price shapes at 3 holding periods = 33 combinations. The universe was
ranked by dollar volume over the FIRST twenty sessions of the window,
so it is roughly what someone could have screened on that morning
rather than a list chosen knowing how the two years turned out.

### What it found

**Nothing.** Exactly one combination survived the search — this
project's own falsified pullback rule, at +0.166% over ten sessions —
and the held-back half of the history refused it at +0.04%, p = 0.24.

That is the framework reproducing a result it already knew, which is
the property its own tests demand of it. It also caught, unprompted,
precisely the kind of false positive it exists to catch: a shape that
looked significant because it had been searched for.

### How the number moved as controls were added

The same shape, "a single session down 3% or more" at five sessions:

| Control added | Edge | p |
|---|---|---|
| date-matched null only, 1 year, universe ranked today | **+1.079%** | below 1e-06 |
| ...plus volatility-matched comparison group | +0.745% | 0.007 |
| ...plus 2 years and a start-of-window universe | **-0.115%** | 0.96 |

Every step was a control that should have been there from the start,
and each one removed roughly half of what looked like an edge. The
first row is what this project would have published a week ago.

### What shipped alongside it

`/today` — five names, each with an entry, a stop derived from measured
volatility, a share count sized to a stated risk budget, a deadline, the
report date, and the condition that would make it wrong. It ranks on
survivability, not direction, and twenty of its hundred points sit
unused at zero because no shape has earned them. The page says so.

## Round 4 — research-driven enhancements: adopt what exists

Per the new CLAUDE.md rule ("never reinvent the wheel"): searched GitHub
for each gap before writing code.

- **Sortable tables + per-row score breakdown** (closed a KNOWN_ISSUES
  "not implemented" bullet). No external library — `/full` already had a
  working sort pattern on its main results table; extracted it into a
  shared `sortRows`/`wireSortable` helper and applied it to "Best of the
  rest", both journal tables, and the rejection-summary table.
  `screener.py::score_row` now returns the exact arithmetic behind each
  score (penalties and the 0-100 clamp included, summing to the displayed
  number by construction) as a third return value, shown on click.
  **Built, not borrowed** — nothing on GitHub fits a bespoke scoring
  formula's own weights.
- **`exchange_calendars`** (github.com/gerrymanoim/exchange_calendars)
  — **adopted**, replacing `market_clock.py`'s hand-typed HOLIDAYS table
  (a fixed 20-entry dict, hard-coded through 24 Dec 2027, no half-day
  handling — the module's own docstring had previously rejected
  `pandas_market_calendars` as "a poor trade on a 512MB instance" for
  a fact that small; that judgement is reversed here because the gap
  (half-days silently treated as full sessions) turned out to matter and
  the library's cost is bounded to near-zero by scoping the calendar to
  a ~2.5-year window instead of its full multi-decade history — measured
  in `tests/test_memory.py`, negligible incremental RSS on top of the
  pandas/numpy the process already carries). Every `market_clock.py`
  function falls back to the original table if the import fails, so a
  broken wheel on Render degrades to the old behaviour rather than a
  500. `KNOWN_THROUGH` now moves forward on every process start instead
  of sitting fixed at a date someone has to remember to extend.
- **Real dollar-volume liquidity on /today** — **built, not borrowed**;
  no library needed. `scripts/scheduled_scan.py::dollar_volume_book()`
  computes 30-day average dollar volume per ticker from the same OHLCV
  frame the scan already downloads (`screener._cache["ohlc"]`), converts
  through the existing `_to_eur`/FX-rate machinery so a GBp-quoted London
  line is not read as pounds (verified in `tests/test_scheduled_scan.py`
  with a synthetic `.L` fixture — a forgotten pence conversion would show
  up as a ~100x inflated figure, and the test asserts it stays within
  2x). `ranking.py::filters()` gates on it directly at $50M/day — not an
  invented number, it is the floor the loosest published preset
  (wide-net) already enforces — falling back to the pre-existing $2B
  market-value proxy, flagged, for the names `liquidity.json` has no
  figure for.
- **IFRS credit coverage** — [`edgartools`](https://github.com/dgunning/edgartools)
  (MIT) **rejected as a dependency** (too heavy for a 512MB instance and
  the short requirements.txt) but its knowledge of which `ifrs-full` XBRL
  concepts line up with which `us-gaap` ones **borrowed with
  attribution** into `credit.py`. `balance_sheet()` now tries us-gaap
  first (unchanged precedence — a dual-tagger's route never flips between
  runs), then ifrs-full via three routes with the same same-period-end
  discipline the us-gaap path already enforced (direct total, sum of
  current+noncurrent for filers who don't tag a combined total, and the
  assets-minus-equity identity). `fetch_balance_sheet()` tries the cheap
  ifrs-full companyconcept calls before paying for the 3.7MB
  companyfacts endpoint — verified in `tests/test_credit.py` with a
  call-count pin. **Currency honesty, not conversion**: a filer whose
  tags exist only in a non-USD currency is refused with that currency
  named (`currency_only` → `report(currency_refused=...)`), never
  converted at a guessed rate — `KNOWN_ISSUES.md` says "read when they
  report in USD," not "IFRS covered," because the non-USD majority is
  still refused, honestly.
- **EU earnings verification** — **built, not borrowed**; reuses the
  Yahoo per-ticker client the project already ships (`investpy` is dead,
  Investing.com blocked it; paid calendars were out of scope).
  `scripts/scheduled_scan.py::eu_earnings_book()` walks EU tickers on the
  runner's fresh IP within a time budget and publishes their dates under
  `earnings.json`'s new `eu` key, separately labelled from the US bulk
  calendar. **A real fail-open bug was found and closed in the same
  change**: `ranking.py::filters()` keyed the "absence is the all-clear"
  logic on a single global `cal_complete` flag, so once the (US-only)
  bulk calendar loaded, every candidate absent from it — European names
  included — read as verified-clear. It now reads a per-candidate
  `cal_covered` (true only for a US name under a complete calendar) and
  `earnings_single_source` (true for a name whose date came from the EU
  book alone), so a EU name's absence never borrows the US calendar's
  completeness, and a EU name WITH a date is labelled single-source
  everywhere it renders (`/today`'s flag pills and earnings text,
  `/full`'s flag tooltip, the pre-trade check). The now-fully-superseded
  `cal_complete` parameter was removed from `ranking.filters()`/`score()`
  rather than left as dead, ignored input. A stale-memo bug in the new
  test itself (`tests/test_today_gate.py` reused an identical `ts=9e9`
  sentinel across two fixture loads, so `_credit_view()`'s cache key
  never changed) is logged in `MISTAKES.md`.
  **Two more real bugs surfaced verifying this live, both logged in
  `MISTAKES.md`**: `app.py::_published_earnings()`'s 2-to-3-tuple rewrite
  dropped the staleness gate that used to fold into `complete` (caught by
  `tests/test_server_robustness.py`'s pre-existing pin, not by anything
  written for this feature); and `/check` never actually reached the `eu`
  book at all, because its earnings lookup prefers a separate, US-only,
  disk-cached live calendar (`screener._earnings_calendar`) and only
  fell back to `_published_earnings()` when that live map was completely
  empty — never true on a warm instance. Confirmed against production
  (`POST /check {"ticker":"TYT.L"}` blocked as unverified despite a
  published date) before fixing: `/check` now merges the `eu` book in
  regardless of whether the live US calendar is warm, and
  `pretrade.check()` takes a `single_source` flag so the pre-trade
  check's earnings finding carries the same caveat `/today` and `/full`
  already did.

## Round 5 — superinvestor 13F tracking

Requested directly: "identify stocks based on where super investors put
their money — Dataroma, for example." Plan and research written up
first in `docs/superinvestors-plan.md`; this entry records what shipped
against it.

- **Dataroma itself — rejected as a data source.** Its GitHub scrapers
  ([op7ic/Dataroma-Analyzer](https://github.com/op7ic/Dataroma-Analyzer),
  [Destruct-Portfolio/Dataroma-Scraper](https://github.com/Destruct-Portfolio/Dataroma-Scraper))
  read HTML that is itself derived from EDGAR — a curator's copy of a
  primary source, with no API and no versioning. **Borrowed instead**:
  the idea of a small, hand-curated manager roster, attributed in
  `superinvestors.py`'s own module docstring. Every filing this project
  reads comes from `data.sec.gov` directly.
- **`edgartools`' 13F parsing — borrowed-with-attribution again, not
  adopted**, consistent with round 4's decision on the same library (too
  heavy for this instance's 512MB / short requirements.txt). Only
  current-quarter infoTable XML is needed, which is ~150 lines of
  `xml.etree.ElementTree` against a schema confirmed against a real
  filing before writing the parser (Berkshire Hathaway's 2026-03-31
  13F-HR) rather than assumed from the SEC's own docs alone. That check
  caught two real shapes a naive parser would have gotten wrong: a
  single issuer can appear across MULTIPLE infoTable rows in one filing
  (a combined 13F across several accounts — Berkshire's Ally Financial
  position was six separate rows, correctly summed), and a `putCall`
  row reuses the underlying equity's own CUSIP rather than a distinct
  identifier, so it is parsed into a separate `options` list rather than
  silently inflating the equity position it shares a CUSIP with.
- **CUSIP-to-ticker mapping — built, not borrowed**, from the SEC's own
  [fails-to-deliver data](https://www.sec.gov/data-research/sec-markets-data/fails-deliver-data)
  (free, no key, pipe-delimited, already carries both fields). OpenFIGI
  was the documented fallback in the plan but proved unnecessary: one
  half-month file mapped 27 of Berkshire's 29 CUSIPs (measured); merging
  the three most recent half-months reached 29 of 29. `cusip_map()`
  takes the majority symbol across however many files are merged, so one
  miscoded row (34 of 13,024 CUSIPs in one real file carried more than
  one distinct symbol) cannot flip a mapping on its own.
- **The manager roster — verified, not guessed.** Every CIK candidate
  found via SEC's company-search endpoint was cross-checked against its
  own `submissions/CIK….json` record (filer name match, a 13F-HR filed
  within roughly the last year) before being checked in. Several
  plausible-looking CIKs were rejected during that check because their
  most recent 13F was years stale — Appaloosa's old CIK (1006438) last
  filed in 2016; the fund now files as "Appaloosa LP" under CIK 1656456,
  which is the one shipped. `fetch_manager()` re-runs the same
  name-match check on every scheduled run, not just at curation time, so
  a CIK reassigned or renamed later refuses loudly instead of silently
  misattributing someone else's holdings.
- **A separate weekly workflow (`thirteenf.yml`), not folded into
  `scheduled_scan.py`.** 13F data moves in bursts around the four
  ~45-day filing deadlines and is otherwise static for weeks — a weekly
  cadence loses nothing a reader would notice, and keeps this off the
  daily scan's own time and SEC-request budget entirely. It also
  publishes only `investors13f.json`, deliberately never touching
  `index.json`: two independently-scheduled workflows both
  force-pushing a read-modify-write of the same shared index file would
  race each other on any week the schedules happened to overlap, so this
  avoids that by construction — `app.py` reads `investors13f.json`'s own
  `as_of` field directly, the same way it already reads `liquidity.json`
  and `vol.json` without an `index.json` detour.
- **Zero score effect, verified structurally and by test.**
  `ranking.py`'s `filters()`/`score()` never read `held_by_investors` —
  the field only flows through `_today_candidates()`'s passthrough dict
  into the template and into `pretrade.check()`'s note-level findings,
  which `pretrade.py`'s own `bottom_line` synthesis builds only from
  `block`/`warn` findings, never `note`. `tests/test_investors_page.py`
  pins both directions: the field reaches every candidate, and never
  appears in a `filters()` flag.
- **A real display bug caught by the first run of its own test**: the
  ticker roll-up's internal dict key for an unmapped CUSIP
  (`"(unmapped) <issuer>"`, used only to keep the roll-up dict's keys
  unique) was rendering verbatim on `/investors` instead of the plain
  issuer name — `display: ticker or key` should have read
  `display: ticker or issuer or key`. Fixed before this shipped; not
  logged in `MISTAKES.md` because it was caught by the test written
  alongside the code in the same session, not by a later live check.
