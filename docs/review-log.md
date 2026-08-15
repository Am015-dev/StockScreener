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

## Round 6 — 2026-08-14, build 564f19a — **85/100** (independent scorer)

Reported directly: "the full website is really awful... impossible to
understand." Read UX/readability research first (Nielsen Norman on
plain-language scanning, sentence-length guidance, fintech dashboard
practice) before auditing, per the standing request to ground design
work in real sources rather than intuition. Audited every live page —
curl for raw HTML/headers, WebFetch for a first-time-reader text
extraction, and a local Flask instance mirroring the real published data
branch, screenshotted with Playwright at 390px (headless Chromium in
this environment cannot reach the deployed Render URL directly, a
known sandbox limitation — curl and a local mirror serving identical
code are the substitute this project has used before).

Two defects, both confirmed live before fixing, accounted for nearly
all of it:

- **`/limits` and `/changelog` served raw markdown source with
  `mimetype="text/markdown"`.** Every browser renders that as unstyled
  plain text — headings, tables and bold shown as the literal
  characters `#`, `|---|`, `**`. `/limits` is the site's own methodology
  page, linked from every other page's footer, so this alone plausibly
  explains most of the complaint. Confirmed with `curl -I`:
  `Content-Type: text/markdown`.
- **Every jargon term on `/full`** (RSI, ATR, profit factor, R, Sortino,
  stop room) already carried a genuinely well-written plain-language
  explanation — in a `data-tip` attribute shown only on CSS `:hover`,
  which does not exist on a touchscreen, the primary way anyone reads
  this site. The writing was mostly fine; the explanations were simply
  unreachable on a phone.

Fixes, and the adopt/borrow/build calls behind them:

- **[`mistune`](https://github.com/lepture/mistune) — adopted** for
  `/limits`: pure Python, zero transitive dependencies, ~65KB wheel,
  CommonMark-compliant with a table plugin — checked against every
  markdown feature `KNOWN_ISSUES.md` actually uses (headings, lists,
  code spans, bold, pipe tables) before adopting, not assumed. `/limits`
  now renders through it into real HTML in a new `markdown_page.html`
  template matching the site's existing CSS token system.
- **`/changelog` — built, not routed through markdown.** A commit
  subject is uncontrolled text that could itself contain
  underscores/asterisks/backticks; running it through a markdown parser
  would let a commit message's own characters be silently reinterpreted
  as formatting. Built as a plain HTML list directly in a new
  `changelog.html` template instead, with Jinja's autoescaping doing
  both the safety and the correct literal rendering in one step
  (verified with a fixture commit subject
  `` "Rename `_credit_for()` to use __slots__ and *args safely" `` —
  every special character renders literally, none reinterpreted).
- **Tap-to-open tooltips — built.** One delegated click listener toggles
  a `.tip-active` class that the existing tooltip CSS already responds
  to (it already had a `:focus` rule alongside `:hover`, this just adds
  the third trigger) — fixes every current and future `data-tip` element
  on `/full` in one change. Confirmed against a real element in a real
  browser context: computed `::after` content was `"none"` before tap,
  the actual explanation text after.
- **A CSS-only scroll shadow** on every `.scroll` table container
  (credit, investors, patterns, today) — `overflow-x:auto` alone gave no
  on-screen sign a wide table was swipeable at 390px, which read as
  missing columns rather than "there's more here."
- **A freshness line on `/today`**, found by the independent scorer
  (below): the flagship daily page had no answer anywhere to "is this
  today's close or three days old" — zero hits searching the rendered
  page for "as of"/"updated"/"scanned". Now states the published price
  book's own latest session date plus `market_clock.state()`'s label.

`scripts/release_gate.py` gained checks for the first two fixes (real
HTML tags on `/limits`, no leaked markdown syntax, tap-reveals-tooltip
on `/full`), so a regression to either the old mimetype or the old
hover-only behavior fails the gate. Full local run: 41/41 passed, zero
console errors across every page. The full Python suite (33 files,
including two new files this round) passed throughout.

**Independent score: 85/100** (a fresh agent with no prior context on
this session, auditing the live site via curl/WebFetch only): decisive
answers 23/25, clarity 16/20, value density 17/20, honesty 14/15,
correctness 10/10, coverage/freshness 5/10 — the freshness gap above was
the scorer's top-named defect and is fixed in this same round. The
scorer also flagged `/changelog` showing only a raw merge-commit
message on this build; checked directly and found the pre-existing
"shallow slice of history" fallback note correctly present alongside
it — Render's shallow git clone genuinely holds nothing but merge
commits at this point in the branch's history, a known, already-handled
edge case (see the code's own comment on it), not a regression from this
round's changes.

### Round 6, follow-up — /today's pick cards rewritten from prose to stat chips

Told directly, after the round above shipped: "the page is still full
of prose text horrible." Right — the round above fixed BROKEN rendering
and made explanations reachable on a phone; it never touched the
underlying layout, and Today's Five's pick cards were still six
full-sentence `<dd>` paragraphs per name (`"$159.08 — 6.2% below.
Anything closer and ordinary noise takes you out; this share moves
about 3.1% on an average day."` for the STOP row alone).

Also caught in the same pass, while checking why the live page showed
"Priced as of 2026-08-12" two days behind: a real but self-correcting
stale-data window right after a deploy (the boot-time published-index
fetch can land before that cycle's freshest scan is what gets adopted;
the background pollers — `books-refresh` every 5 minutes,
`results-poll` on its own interval — catch up within one cycle without
intervention). Confirmed the live instance was already showing
2026-08-14 by the time this was investigated — not a live defect to fix
here, but exactly the kind of gap the previous round's freshness line
was built to make *visible* rather than silent, which is what let it be
caught and confirmed self-healing in the first place.

The actual fix: every pick card's entry/stop/shares/earnings numbers
moved out of full-sentence `<dd>` rows into a four-chip stat grid
(`.stats`/`.stat`) with the number itself as the primary visual weight
— the sentences that used to carry that information were not deleted,
they moved into each chip's tap tooltip (the same tap-to-open mechanism
`/full` got in the round above, now also wired into `/today`), so the
full reasoning is one tap away instead of forced into the primary
reading path. "Usual move" and "Wrong if" — the two pieces of prose that
are genuinely sentences, not just numbers with a unit — stayed as short
one-line captions rather than becoming chips, but were trimmed from two
paragraphs to two short sentences each. The "Held by N tracked
managers... as of their last 13F (up to ~135 days stale, long-only). Not
a signal — informational only." row became a single pill,
`Held by N superinvestors`, with that same caveat moved to its own
tap tooltip.

`scripts/release_gate.py`'s three checks for "every name carries a
stop/share count/wrong-if" were matching the old `dl.plan dt` markup by
element and text; updated to match the new `.stat .l` / `.line`
structure — the checks failed on the first run after this change
precisely because they were still looking for markup that no longer
existed, which is exactly what they are for. 41/41 passed once updated;
full Python suite (33 files) unaffected, since none of it asserts on
`/today`'s specific HTML structure beyond the one pin already in
`tests/test_today_gate.py` (a negative assertion — "no pick card
rendered" — unaffected by what a rendered pick card looks like).

### Round 7 — 2026-08-14 — `design:anti-slop` Mode B audit, sitewide

Operator report: "full of shit unreadable and boring... a mess of data
and text not professional at all." Ran the installed `design-anti-slop`
skill's post-generation audit against `/`, `/full`, `/investors`,
`/patterns`, and `/credit/<ticker>`, checking each page against all
three pattern layers (V1-V9 visual, S1-S9 structural, C1-C7 conceptual)
rather than defending the prior round's 85/100 self-score.

Result: zero confirmed hits on the classic AI-slop template patterns —
no gradient hero, no fake testimonials, no logo soup, no bento grid, no
invented stats. The colour tokens in `index.html` carry a code comment
recording they were checked against a colourblind contrast validator;
the sparklines and equity curve are real price/backtest data, not
decoration; the copy states outright that the entry signal lost to a
coin flip twice, which is the opposite of a hollow claim (C1/C5 clean).

What *was* real: every page's `h1` sat at 1.5-1.75rem with nothing else
on the page above 1rem — a V2 hit (no typographic hierarchy) present
identically on all five pages, checked by grepping the `h1 {` rule in
every template. That flatness, not any AI-slop template pattern, is the
literal mechanism behind "boring": nothing on any page was ever allowed
to be visually bigger than anything else, so a stock pick, a
3,477-row 13F table, and a credit verdict all read at the same weight.
`/today` additionally opened with a ~55-word methodology paragraph
before the first number (a sequencing issue, not a copy-quality one —
the words were accurate and load-bearing, just first).

Fix shipped: `h1` bumped from 1.5-1.75rem (no explicit weight, i.e.
browser default ~700, except `index.html` which already set 680) to a
uniform 2.1-2.2rem at weight 780 across `today.html`, `index.html`
(incl. its own 720px breakpoint), `investors.html`, `patterns.html`,
`credit.html`, `changelog.html`. On `/today`, the intro paragraph
shrank to one sentence with the risk-budget explanation moved into a
tap-tooltip — the same `data-tip` mechanism the stat chips already use,
so nothing was deleted, only reordered into the pattern the page
already teaches the reader to tap.

Not fixed this round, flagged for a `design:design-system`-scale pass:
uniform card/row weight on `/today`'s five pick cards and `/investors`'s
multi-thousand-row table (S6-adjacent) — real, specific content
presented with no visual signal for what to read first. This needs a
cross-template pass to pick one element per page as an anchor, which is
more than a same-day patch.

Verified: full Python suite green (one flaky failure in
`test_server_robustness.py` under a throwaway parallel-run harness with
a stripped `PATH`, confirmed unrelated — passes standalone, and that
file asserts nothing about templates); `test_docs_pages.py` and
`test_investors_page.py` re-run directly, both green; live `h1` rule on
the deployed build confirmed via curl post-deploy.

### Round 8 — 2026-08-14 — real charts, restrained motion, paged tables

Operator, immediately after Round 7 shipped: use animation and images to
feel professional, and stop the unlimited scroll. Researched GitHub
first per CLAUDE.md's never-reinvent rule before writing anything.

**Adopt/borrow/reject ledger for this round:**

- Per-pick price chart. Candidates:
  [tradingview/lightweight-charts](https://github.com/tradingview/lightweight-charts)
  (~45KB, canvas, interactive) and [leeoniya/uPlot](https://github.com/leeoniya/uplot)
  (~50KB, canvas). **Rejected both as dependencies** — 45-50KB of vendored
  JS to draw five static 60-point lines is disproportionate, and this
  repo already has a working answer to the same problem. **Borrowed the
  repo's own pattern instead**: `sparkSVG()` in `templates/index.html`
  (the `/full` sparklines) is inline SVG built from the same published
  price series with no library at all — `pickChartSVG()` in
  `templates/today.html` is that same approach, sized for the pick cards,
  deliberately without the target/resistance line `/full`'s version
  draws, since `plan.py`'s entire premise is that this project does not
  predict a price.
- Entrance/hover motion. Considered a library (AOS.js and similar) vs.
  hand-rolling. **Rejected the library** — the standard
  IntersectionObserver reveal pattern documented on MDN/CSS-Tricks is
  ~15 lines of vanilla JS with zero dependencies; a library would add a
  script tag and a CDN dependency for something this small.
- Favicon. Checked `app.py` before building anything: `/favicon.ico`
  already serves a real inline SVG (a checkmark in the site's accent
  blue) — this item from the plan was already shipped in an earlier
  round and needed no work this time.

**What shipped:**

*Images* — every `/today` pick now draws its real last-60-session
price path as an inline SVG, the stop marked as a dashed line against
the path it actually took. `app.py::today_page()` attaches
`p["spark"]` from the same series `_today_candidates()` already scored
from — a dict lookup, no new network call, no new published book.

*Motion* — entrance-reveal (opacity + 10px rise, ~450ms, staggered per
card) and a 2px hover-lift, added to `today.html`, `investors.html`,
`patterns.html`, `credit.html`, and a reveal-only pass on `index.html`
(which already had button/card hover transitions from an earlier
round). The score-contribution bars on `/today` now grow into place
the first time their `<details>` opens, via a pure-CSS trick — forced
to 0 width while closed, real width transitions in on open — rather
than JS. Every animation is added by JavaScript only: the `.reveal`
class is never written by Jinja, so a reader with JS disabled, or a
crawler, sees the static page at full opacity from first paint, and
`prefers-reduced-motion` zeroes every transition — most pages reuse
`index.html`'s existing sitewide `* { transition: none !important }`
rule for that.

*Less scroll* — `/investors`' multi-holder table (up to 200 rows)
pages 25 at a time behind a "Show 25 more" button; every row still
ships in the static HTML, JS only ever hides what is beyond the
current page, so a no-JS reader loses nothing. `/today`'s "How to
trade this list" and "What decided this" cards — peer reference
material, not sequential reading — sit side by side at a >=720px
viewport instead of adding to the scroll; they still stack normally on
a phone.

**Tests added:** `test_investors_page.py` pins one chart per rendered
pick on `/today` (5-for-5 against the file's real-scoring fixture);
builds a 30-synthetic-row `/investors` fixture and pins that all 32
multi-holder rows (30 synthetic + AAPL + the unmapped holdco) ship in
the static HTML even though only 25 are visible at first, and that
`class="reveal"` never appears server-rendered. `test_page_robustness.py`
pins the same JS-only-`.reveal` guarantee on `/` and `/full`.
`scripts/release_gate.py` gained: a per-pick chart-SVG-count check on
`/today`; an `/investors` visit that clicks "Show more" and confirms
more rows actually became visible; a `prefers-reduced-motion` CSS rule
present on `/`, `/full`, `/investors`, `/patterns`.

**Verification:** full Python suite (32 files) green. Live-rendered via
a local Flask instance (`PUBLISHED_DIR` pointed at synthetic fixtures)
screenshotted with local Playwright at 390px and 900px — confirmed the
chart renders with a real green/red price line and dashed stop line,
and that the two-card pair stacks under 720px and sits side by side
above it. `/investors` pagination screenshotted and click-tested
locally (40 synthetic rows: 25 shown, "Show 25 more" reveals the rest
and removes itself). After push, confirmed live on the deployed build
via curl: `pickChartSVG` present, and all five of that day's real picks
carrying real `data-spark`/`data-stop` values pulled from the actual
published price book.

### Round 9 — 2026-08-14 — Fincept Terminal review: what to adopt, borrow, or reject

Operator asked to check the Fincept Terminal GitHub project
(Fincept-Corporation/FinceptTerminal) for anything worth including here.
Ran a multi-angle research pass — architecture/licensing, its "100+
data connectors," its "18 QuantLib modules"/"CFA-level analytics," and
its "37 AI agents"/"16 broker integrations" — before writing any code,
per CLAUDE.md's search-first rule.

**What it is:** a native C++20/Qt6 desktop terminal, AGPL-3.0-or-later,
30.2k stars. Not a Python package — its Python scripts run only as
C++-launched subprocesses, never imported. A `fincept-terminal` package
does exist on PyPI, but it is a separately-licensed (MIT), ~11-month-
stale, pre-rewrite legacy GUI app unrelated to the current codebase —
confirmed and rejected as irrelevant, not adopted.

**Rejected outright, with reasons:** the codebase itself (different
language and runtime; AGPL would require anything importing it to also
go AGPL, for no code this project actually needs); World Bank/IMF APIs
(no consumer — nothing here screens on sovereign macro data); DBnomics
(claimed in Fincept's README, but no working connector script could
actually be found — treated as a marketing count, not a verified
source); Treasury FiscalData (does not expose the daily par yield curve,
the one series that would have been useful); QuantLib-Python bindings
(a heavy dependency for derivatives pricing this project does not do,
sitting alongside — not replacing — the bespoke Merton/KMV solver
already built); Fincept's own "Portfolio," "Risk," and IFRS-9-ECL
modules (mean-variance optimisation, VaR-exception testing, and loan-
loss provisioning — none overlap with this project's Ledoit-Wolf
shrinkage, purged-CV backtester, or equity-only structural credit
model, which are simply a different, more specific stack); the 37 AI
agents (LLM investor/economist personas generating prose — a direct
violation of the house rule against invented or LLM-generated analysis
anywhere on this product, regardless of license); the 16 broker
integrations and paper-trading engine (order execution is out of this
product's stated scope by design — research-only, no positions ever
placed by this site).

**Adopted:** nothing as a dependency — no candidate cleared the bar.

**Borrowed with attribution (two, both shipped this round):**

1. **CBOE VIX regime signal** (`vix.py`, new module). Fincept's
   `cboe_vix_data.py` hits `cdn.cboe.com/api/global/us_indices/
   daily_prices/VIX_History.csv` directly — a public CSV, no key, no
   auth, verified working by curl before any code was written. The URL
   is borrowed with attribution; nothing else is. Deliberately no fixed
   "VIX > 30" band — this project already has a rule against invented
   thresholds (`credit.band()`'s own docstring) and a working answer to
   the same problem (`credit.percentile()`, reused here rather than
   reimplemented) — so today's close is placed against its own trailing
   ~5 years of history instead, and only the tails (>=90th, <=10th
   percentile) get a note, silent otherwise, matching the front page's
   existing SPY/Stoxx regime note's own rule. Published into the same
   `regime.json` the SPY/Stoxx flag already uses (one file, not a new
   one), computed in the scheduled scan and rendered through
   `_regime_notes()` — zero template changes needed, since /today's
   "Market backdrop" card already renders whatever that function
   returns.
2. **Altman Z''-Score** (`credit.py`: `altman_z()`,
   `fetch_altman_inputs()`, `with_altman()`). Fincept lists Altman
   Z-score among its analytics but ships no reusable code for a Flask
   project — a different language under a copyleft license. The formula
   itself is public, decades-old academic work (Altman, 1995), verified
   independently against Altman's own 2018 retrospective paper before
   writing any code — a first draft, based on a secondhand description,
   was missing the model's `+3.25` constant, which would have scored
   every company 3.25 points too low and silently misclassified almost
   everything as distressed. Independent verification caught this
   before it shipped, not after. Wired in as a genuinely SECOND,
   independent read: it needs no share price at all (working capital,
   retained earnings, operating income and book equity, all from the
   filed balance sheet and income statement), so it can disagree with
   the market-based Distance to Default, and when it does, that
   disagreement is itself informative. Scoped deliberately narrow this
   round: only computed alongside an ALREADY-successful Merton read
   (not as a fallback for a failed one), `us-gaap` only (no `ifrs-full`
   equivalent yet), and via its own isolated fetch function that never
   touches `fetch_balance_sheet()`'s existing, carefully-tested control
   flow — a fetch failure here costs the primary report nothing.

**Tests:** `tests/test_vix.py` (new) pins CSV parsing degrading on bad
rows rather than raising, the percentile direction, the lookback window
actually bounding history, and `note()`'s silence on the ordinary case
and inclusive tail boundaries. `tests/test_credit.py` gained the
hand-computed Z'' pin (the exact number the missing-constant bug would
have gotten wrong), zone-boundary pins at both cutoffs, and
`fetch_altman_inputs`/`with_altman` pins for same-period-end discipline,
partial-fetch refusal, and — critically — that a failed Merton report
costs zero SEC calls trying to add a second opinion to it.
`tests/test_scheduled_scan.py` gained VIX-merge, VIX-failure-isolation,
and VIX-only-publishes pins, each driven through the real subprocess
harness with `vix.regime` stubbed (a first draft of this test
accidentally hit the real CBOE endpoint from every subprocess in this
file, caught by re-reading the file's own "no network round trip"
docstring promise before trusting the first green run).
`tests/test_page_robustness.py` gained the same silent-on-normal /
speaks-on-the-tails pin the existing SPY/Stoxx regime block already
has. Full suite (34 files) green.

**Documented:** two new KNOWN_ISSUES.md sections naming what each
addition cannot tell a reader — Z'' only alongside a working Merton
read, `us-gaap` only, EBIT-as-operating-income, no retroactive backfill
for the VIX note; a fixed lookback window is a choice, and a CBOE outage
reads identically to an ordinary day, by design.

### Round 10 — 2026-08-15 — one shared nav, sitewide, and every emoji dropped

Operator: "The website is not easy to navigate drop emojis and prose
user can not navigate neither from browser or phone." Ran a full
read-only audit (file:line level, via a research subagent) before
touching anything, rather than guessing at what "hard to navigate"
meant.

**What the audit found, concretely:**

- **No shared navigation existed.** `grep -r "{% extends\|{% include"
  templates/` returned nothing — all 7 templates hand-rolled their own
  nav independently. Two different CSS classes (`.tiny` vs `.top-nav`)
  implemented the visually-identical back-link, and every page's footer
  link list carried a DIFFERENT set of "other pages" links — no two
  pages agreed on what else existed on the site.
- **`/` (the page every visitor lands on) had zero navigation until the
  very bottom** — after the h1, intro prose, up to 5 full pick cards,
  and two explainer cards (~700-900+ words). Four of six secondary
  pages (`/patterns`, `/credit/<ticker>`, `/limits`, `/changelog`) were
  dead ends: a single "back to home" link, no route to any OTHER page
  without returning to `/` first. `/changelog` was linked from
  nowhere at all — reachable only by typing the URL directly.
- **Nav links had no tap padding.** Every back-link/footer-nav link used
  `.tiny`/`.top-nav`/`.hint` (~13px text, zero padding) against real
  buttons on the same pages (`.55rem 1.1rem` padding) — footer link
  lists packed 3-4 links on one line separated only by `·`.
- **Real pictographic emoji were concentrated almost entirely in
  `templates/index.html`** (`/full`): a literal `medal()` function
  returning `🥇🥈🥉🏅`, plus `⛔⚠️📥↩▶✅📂🎯⏳❌📭📊` scattered through
  inline JS status messages. `today.html` had one `ICON` map (`✓⚠⛔`)
  feeding the credit-standing widget. `investors.html`, `changelog.html`,
  `markdown_page.html` were already emoji-free — proof the rest of the
  codebase already knew how to do this: `credit.py`/`plan.py` use plain
  words + CSS color only, never a glyph.

**What shipped:**

- **`templates/_nav.html`** (new): one shared partial, included via
  `{% include "_nav.html" %}` at both the top (right after `<div
  class="wrap">`, before any content) and bottom of all 7 templates.
  Same 5 primary links everywhere (Today's Five, Full screener,
  Superinvestors, Patterns tested, Limits), plus Changelog rendered
  visually smaller/demoted (now reachable for the first time) rather
  than given equal weight. The current page renders as bold, non-link
  text via `request.path`, with `/today` explicitly folded onto `/` —
  they render the identical template, and treating them as different
  paths would have made `/`'s and `/today`'s bytes differ for the first
  time ever, breaking `test_today_gate.py`'s own "/ and /today serve
  the same page" pin (caught by that exact test failing, not by
  inspection — fixed by computing `nav_path` once rather than reading
  `request.path` directly in the loop). CSS written against only the
  custom-property names spelled identically across all 7 templates
  (`--bg`/`--line`/`--ink`/`--ink-2`/`--ink-3`/`--accent`), deliberately
  avoiding `--card`/`--panel` (the one pair that is NOT spelled the
  same on `index.html` vs. the other six) so the nav needed zero
  per-page token edits to look right. Link padding matches this
  codebase's own existing button convention (`.55rem`-ish), not an
  invented tap-target number.
- **Every real emoji removed**, replaced with this codebase's own
  established pattern (plain word + existing CSS color class, matching
  `credit.py`/`plan.py`): `medal()` → `rankLabel()` returning `#1`/`#2`/
  etc.; every `⛔⚠️📥↩▶✅📂🎯⏳❌📭📊✓✗` in `index.html`'s JS dropped, in each
  case leaving the color-coded word that was already sitting next to
  it (or adding one, e.g. "Yes"/"No" for a bare `✓`/`✗` risk-budget
  check); `today.html`'s `ICON` map removed entirely, keeping the
  `COLOR` map beside it. Two non-website spots cleaned in the same
  pass: `screener.py`'s progress log line (renders verbatim into
  `/full`'s live log panel) and `app.py`'s `/alert` Slack-message
  builder. Kept deliberately: `←`/`→` (back-link and trend arrows),
  `▲▼` (sort-direction indicators), `▸` (expand-hint chips), `━`/`┄`
  (chart legend line styles), `·` (separator) — none of these are
  pictographic emoji, all are standard non-emoji typographic/UI
  symbols already used the same way elsewhere on the web.
  `scripts/sweep.py` (a local dev CI report generator, never served by
  Flask) was left untouched — out of scope for "the website."

**Tests:** `tests/test_page_robustness.py` gained a check that all 6
core pages (`/`, `/full`, `/investors`, `/patterns`, `/limits`,
`/changelog`) share the identical nav link set, positioned before the
first `<h1>`, and render zero emoji codepoints.
`tests/test_credit_endpoint.py` gained the same check for
`/credit/<ticker>`, reusing its existing complete `PUBLISHED_CREDIT["P05"]`
fixture rather than building a new minimal one that turned out to be
missing fields the "measured" branch of `credit.html` needs (caught by
a 500 on first run, not by inspection). Full suite (33 files) green.

### Round 11 — 2026-08-15 — why the Fincept round was invisible live (and correcting my own first diagnosis)

Operator: "I see nothing on the site from fincept, you did so much
bullshit analysis and improved anything." Investigated rather than
defending — the complaint turned out to be correct in effect, wrong in
the cause I first assumed, and the real cause needed correcting twice
before landing on the truth.

**First hypothesis (wrong):** that the scheduled scan was running
stale code because its GitHub Actions checkout was unpinned and
GitHub only reads `schedule:` triggers from the default branch
(`main`), which I found 266 commits behind the deploy branch with
`vix.py`/`with_altman`/`thirteenf.yml` entirely absent from it. This
was checked against a **stale local `main` ref** — `git show main:...`
without first fetching `origin/main`. A fresh `git fetch origin main`
showed the real `origin/main` was only 3 commits behind, and those 3
commits (`e264015`, `dd0e740`, `49d37f8`, dated Aug 12 and Aug 14) had
**already fixed exactly this problem** in an earlier round of this
same session: both `scheduled-scan.yml` and `thirteenf.yml` on `main`
already pin `ref: claude/pullback-uptrend-screener-vvlzeb` in their
checkout step, and one of those commits' own message documents the
original discovery ("the scheduled runs execute the scan script from
the DEFAULT branch and that copy does not know how to build either of
them [credit.json/vol.json]"). Caught before any unnecessary push to
`main` — the plan approved for this round was never executed, because
the premise it was built on turned out to be false. **Rule for next
time: `git fetch` the ref before reading it with `git show branch:path`
— a local branch ref can silently be stale relative to `origin`.**

**Second, correct diagnosis:** the pipeline was fine. The actual gap
was simpler and purely about timing. `scripts/scheduled_scan.py`'s
`credit_book()` only re-fetches a company whose `built` timestamp is
more than `CREDIT_REFRESH_S` (20 hours) old — a deliberate,
already-tested incremental design (see Round 3's SEC-budget reasoning)
that lets coverage widen without re-measuring the whole book every
run. The VIX/Altman code (commit `d8a1262`) shipped Friday 2026-08-14
23:52 UTC, **after** that day's last scheduled run (21:27 UTC). The
scan only runs weekdays (`cron: "5 13-21 * * 1-5"`), and 2026-08-15 is
a Saturday — so between shipping and this investigation, the scan had
not run even once with the new code.

**What was actually verified, live, with real data, this round:**
- Manually dispatched `scheduled-scan.yml` via `workflow_dispatch`
  (GitHub API) rather than waiting for Monday. Confirmed via the
  published `regime.json`: `{"vix": {"level": 14.25, "as_of":
  "2026-08-14", "percentile_5y": 16, "n_obs": 1259}}` — the VIX code
  ran, fetched real CBOE data, and computed a real percentile. It is
  silent on the front page **correctly**: 16th percentile is inside
  the ordinary range (`HIGH_PCTL=90`, `LOW_PCTL=10`), so `vix.note()`
  returning `None` here is the feature working as designed, not a bug
  — genuinely indistinguishable from "broken" to a reader without this
  investigation, which is itself worth remembering.
- `credit.json` after that same dispatch: 831 companies, 0 carrying an
  `"altman"` key — expected and correct, not a defect: every company
  was already measured within the last 10.5 hours (last successful run
  21:27 UTC the day before), so `CREDIT_REFRESH_S`'s 20-hour gate
  skipped re-fetching all 831 of them. Monday's first run (~63 hours
  after Friday's last one) will clear essentially the whole book past
  the threshold and should populate `altman` broadly.
- Attempted an immediate live proof via a not-yet-published ticker
  (`/credit/SOFI`, `/credit/LCID`, `/credit/DKNG` — confirmed absent
  from the published book first) to exercise the on-demand
  `with_altman()` path directly, bypassing the 20-hour gate entirely.
  Hit a **separate, pre-existing, unrelated** live issue: the Render
  instance's SEC ticker-list cache (`app.py`'s `_cik_map`) was
  returning "The SEC's company list could not be read just now" on
  every attempt (confirmed not code-specific: this blocks the CIK
  lookup every live report depends on, before Altman or even Merton
  ever runs; confirmed SEC.gov itself was reachable with a proper
  User-Agent from outside Render at the same time, so this reads as a
  transient Render-side condition — matching `_cik_for()`'s own
  documented behavior of retrying on every call with no cooldown,
  rather than a stuck cache). Not chased further — a live SEC-fetch
  hiccup unrelated to this round's code is a different problem from
  "does Altman work," which the unit tests in `tests/test_credit.py`
  and `tests/test_credit_endpoint.py` already answer with certainty.

**Correction to Round 9's own entry:** that entry's "Verification"
section said the live build was "confirmed live via curl" — true only
in the narrow sense that `/credit/AAPL` returned 200 and didn't crash.
It did not confirm the *data* had actually changed, because it
couldn't have: no scan had run with the new code yet at that point
either. The gap between "the page didn't error" and "the feature is
visibly live" is exactly what this round exists to name.

### Round 12 — 2026-08-15 — picks that visibly differ from each other, and from day to day

Operator: "Website is very basic stiff and boring showing the same
analysis all the time investigate similar sites and pick improvements
and include them." Researched real competitor products (Finviz,
TradingView, Simply Wall St, Zacks, Seeking Alpha, Koyfin, Yahoo
Finance) rather than guessing at generic redesign ideas, then audited
this codebase's actual output against what was found.

**What competitors do differently:** the common thread across all of
them is that the same underlying score renders in a genuinely
different visual *shape* per subject, not the same chart with
different numbers substituted in — Simply Wall St's five-axis
"Snowflake" radar is the clearest case: a strong-credit/weak-momentum
company draws a visibly different polygon than the opposite profile,
at a glance, no reading required. None of them fix "boring" with
layout tricks; they let real score variance produce real visual
variance, and surface what is specifically notable about *this* item
today instead of a fixed paragraph shape.

**What was actually repetitive here** (confirmed by direct audit,
file:line, before writing anything): `plan.py`'s `trade_plan()` built
`stop_text`, `typical_move_text`, `time_stop_text`, and `wrong_if` as
one fixed f-string per field — identical sentence structure every
pick, every day, only the numbers swapped in. And the one place cards
already *did* differ from each other — `ranking.py`'s 4 real score
components (`p.components`) — was hidden by default behind a collapsed
`<details>` ("Why it ranks where it does"). The most differentiating
real data on the page was the thing nobody saw without clicking.

**What shipped, all three grounded in data already computed —
nothing invented, no randomization, per this project's own house
rule:**

1. **A 4-axis radar of the real score components, surfaced by
   default.** `templates/today.html` gained `radarSVG()`, following the
   exact inline-SVG-no-library pattern `pickChartSVG()` already
   established in Round 8 (data embedded as a `data-*` JSON attribute,
   SVG built by JS at render time — explicitly rejecting a chart
   library again, same reasoning as Round 8's `lightweight-charts`/
   `uPlot` rejection). The collapsed exact-numbers bar list is
   unchanged and still there — the radar promotes the same data, it
   does not replace the one place it used to live.

2. **Content-adaptive branches in `plan.py`'s four previously-fixed
   fields.** Each now has 2–3 real branches keyed on thresholds already
   in hand: `stop_text` on whether measured volatility clears
   `credit.REFERENCE_VOL` (the existing, already-published median
   constant — reused, not reinvented); `typical_move_text` on whether
   the move-to-cost ratio is strong (≥5×) or marginal;
   `time_stop_text` on whether an earnings report falls inside the
   5-session window right after the hold ends; `wrong_if` on which
   score component actually put the name on the list. None of these
   are new measurements — they are new sentences describing
   measurements this project already made.

3. **`plan.standout(picks, i)`** — one line naming the single real,
   cross-pick fact that makes a pick different from its four peers that
   day: tightest stop, best edge (lowest cost-as-share-of-typical-move),
   or the only pick with an earnings date on the calendar. Deliberately
   *not* based on which score component won — `thesis()` already claims
   that ground, and duplicating it would just be the same repetition in
   a different sentence. A pick that is not the extreme on any of the
   three gets nothing, on purpose: not every pick is standout at
   something, and a fallback sentence here would be exactly the kind of
   filler this line exists to replace. A tie for an extreme belongs to
   neither side of the tie.

**Deliberately not this round** (real ideas from the research, each a
separate build): a Finviz/TradingView-style sector heatmap, and a
same-sector comparison strip.

**A real mistake, caught while writing the integration test, not
before:** the radar's first version wrote
`data-components="{{ p.components|tojson }}"` into a *double*-quoted
attribute. `data-spark="{{ p.spark|tojson }}"`, already in this same
template, uses the identical pattern and works — but `spark` is a list
of floats with no quotes in its JSON at all, so that precedent proved
nothing about a dict with string keys. Jinja's `tojson`
(`htmlsafe_json_dumps`) escapes `<`, `>`, `&`, and `'` because its
documented target is a `<script>` tag — it deliberately leaves `"`
alone, and every one of a dict's key-quotes closed the attribute
early. Caught by parsing the rendered `data-components` value back out
in `tests/test_today_visuals.py` and diffing it against `ranking.py`'s
real output: the double-quoted regex matched nothing, and the raw HTML
showed the attribute truncated at the first `{"`. Fixed by switching to
a single-quoted attribute (`tojson` does escape `'`); a regression pin
now asserts the broken double-quoted form never reappears. Logged in
`MISTAKES.md`, since this is exactly the kind of reasoning error —
reusing a same-file precedent without checking whether the precedent's
data shape made the pattern accidentally safe — that file exists to
catch.

**Also caught only by rendering the real page, not by the unit
tests:** the radar's first sizing (108×108px canvas, 16px margin) left
too little room for its own longest labels — "Credit" and "Pattern" —
to fit inside the SVG's viewBox before its default `overflow:hidden`
silently ate their leading or trailing characters ("Credit" rendered
as "it", "Calm" as "C"). Unit tests only ever saw the underlying data,
which was correct throughout; a screenshot at both breakpoints (via
the local Flask mirror + Playwright, `prefers-reduced-motion: reduce`
forced so every card's `.reveal` fires immediately instead of only on
scroll-into-view) is what actually caught it. Fixed by widening the
canvas to 150×150 with margin sized for the two longest label strings
specifically, plus `overflow:visible` on the SVG as a backstop, not a
fix in itself.

**Verification:** `SKIP_WARM=1 python3 tests/test_*.py` — full suite
green. New file `tests/test_today_visuals.py` pins: `trade_plan()`'s
four branches produce genuinely different sentences for genuinely
different inputs (not cosmetic — asserted on distinct substrings, e.g.
"genuinely calm" vs. "swings harder"); `standout()` picks the correct
extreme fact for a synthetic 5-pick fixture with one deliberately
extreme value per slot, returns nothing for the two picks holding no
extreme, and returns nothing for a tied extreme or a single-pick list;
`/today`'s rendered `data-components` for every real pick matches
`ranking.py`'s actual output exactly (parsed back out of the HTML, not
assumed); the exact-numbers bar list is confirmed still present
alongside the radar; a regression pin blocks the double-quoted-attribute
bug from coming back. Screenshotted a synthetic 5-pick day at 390px and
900px — radar shapes are visibly different from each other within the
same day's five cards, and standout lines appear only on the picks that
actually hold an extreme (2 of 5 in the synthetic fixture), none on the
other 3.

### Round 13 — 2026-08-15 — the same companies were appearing every day, and now they can't

Operator: "The same companies appear everyday." Investigated before
touching anything, because Round 12 had just shipped a radar chart and
adaptive sentences and it would have been easy to mistake a genuine
structural finding for "the operator hasn't seen the new visuals yet."
It wasn't that.

**Root cause, confirmed with live evidence, not assumed:** of
`ranking.py`'s 4 score components, 2 are structurally zero for every
anonymous visitor on Today's Five, always, not intermittently:
- `adds to what you own` — `app.py`'s `today_page()` calls
  `ranking.score(..., corr_by_ticker=None)`, hardcoded, because there's
  no way to know a random visitor's holdings without input. By
  `ranking.py`'s own design a `None` here is a real zero for everyone,
  never a placeholder.
- `confirmed pattern` — the live `/patterns` page states outright:
  "None of the 33 patterns tested is worth trading." Nothing has ever
  passed this project's own holdout, so this has never once been
  non-zero.

That leaves 60 of 100 possible points live: `credit headroom`
(distance-to-default) and `calm enough to size up` (trailing
volatility) — both slow-moving fundamentals for a large company.
Pulled the live page at the moment of investigation: ATO, KO, JNJ,
MPLX, MCD — Atmos Energy, Coca-Cola, J&J, MPLX, McDonald's — component
breakdown credit ≈28-30/30, calm ≈28-30/30, fit and pattern 0/20 across
every one of them. Ranking on two numbers that barely move week to week
will mechanically float the same handful of ultra-stable megacaps to
the top indefinitely. Asked the operator which direction to take (a
recency cooldown vs. widening the credit/vol scoring vs. just stating
the mechanism on the page) rather than guessing at a fix for a genuine
product-design question; the answer was the cooldown.

**What shipped — a real, data-grounded cooldown, no randomization, no
manufactured variety:**

`ranking.py` gained two new pieces:
- `build_candidates()` — the candidate-building logic that used to live
  only inside `app.py`'s `_today_candidates()`, moved out as a pure
  function so a second caller (the scheduled scan) can build the exact
  same candidate list from the exact same books with zero duplicated
  logic. `app.py::_today_candidates()` is now a thin wrapper.
- `select_daily_five(ranked, history, today, max_points_available)` —
  a name shown in the last `COOLDOWN_SESSIONS` (5, one trading week)
  sessions is pushed behind every fresh name UNLESS its score has moved
  by at least `COOLDOWN_MATERIAL_FRACTION` (15%) of that day's active
  point scale since it was last shown — a real, measured change, never
  a guess. If fewer than 5 names are fresh, the list is filled from the
  least-stale cooling names rather than coming up short — a real name
  with a note beats an incomplete list. With no published history
  (day one of this shipping), the function returns exactly
  `ranked[:5]` — today's behaviour, unchanged, until the first day of
  real data exists.

`scripts/scheduled_scan.py` gained `_record_todays_five()`, called once
per run after `earnings.json` is written. It re-reads the files this
run just published (not the in-memory variables from earlier in
`main()`) — so it always reflects exactly what the site itself will
read, including a run where one of the books failed to publish — builds
candidates, scores them, and calls `select_daily_five()` against the
prior state (`git show origin/screener-data:recent_picks.json`,
restored to `/tmp/prev_recent_picks.json` by a new line in
`scheduled-scan.yml`'s existing restore step, same pattern as
`credit.json`'s `prev` accumulation). The result — the actual 5 tickers
and scores that would be shown — is upserted into a rolling
`recent_picks.json` window (10 sessions kept) and published through the
same `screener-data` branch mechanism every other book already uses;
no workflow step needed to change beyond the one restore line, since
the branch's carry-forward loop already generalizes over every `.json`
file.

`app.py` reads `recent_picks.json` through a new `_recent_picks_book()`
(same pattern as `_regime_book()`), and `today_page()` calls
`ranking.select_daily_five()` in place of `res["ranked"][:5]`. A pick
kept only because too few fresh names cleared, or one that earned its
spot back on a real score move, carries a new flag pill on the card
(`shown recently, too — not enough fresh names today` /
`back — its numbers moved enough to matter`) — transparency about the
mechanism, not a silent reorder.

**A real edge case caught while testing, not by reasoning about it in
advance:** the first version of `select_daily_five()` found "the most
recent N distinct dates present in history" with no bound on how far
back those dates could actually be. A history containing a single
isolated entry from over a month ago — exactly the shape it will have
on day one after a scan outage, or immediately after this feature
ships with a sparse backfill — read as "the most recent session"
purely because nothing newer existed to outrank it, which would have
wrongly cooled a name that was never actually shown recently. Caught by
a test asserting a 45-day-old isolated entry has zero effect; fixed by
adding `COOLDOWN_MAX_CALENDAR_GAP_DAYS` (14, generously covering 5
trading sessions plus a holiday weekend) as a second, independent bound
alongside "one of the most recent 5 distinct dates."

**Verification:** `SKIP_WARM=1 python3 tests/test_*.py` — full suite
green. New file `tests/test_cooldown.py` pins all three layers:
`select_daily_five()`'s behaviour in isolation (no history, cooling
names backfilled behind fresh ones, a material score move waiving the
cooldown, the stale-isolated-entry edge case, no price date);
`build_candidates()` against the pre-refactor `_today_candidates()`
behaviour (a regression pin so the two callers cannot silently drift
apart); `scheduled_scan._record_todays_five()` end to end across two
simulated sessions with unchanged scores, asserting the second
session's five differ from the first's; and `app.py`'s `today_page()`
wired end to end — seeded with cooldown history claiming yesterday's
unfiltered top 5 were already shown with unchanged scores, `/today`
is asserted to show a genuinely different five today, not the
identical set repeated.

### Round 14 — 2026-08-15 — pages that actually point at each other, and real motion

Operator: "the pages do not communicate with each other now that we
have free actions we must make the site much more dynamic it's so old
and ugly." Three visual/content rounds already preceded this one
(Round 7's anti-slop audit, Round 8's charts, Round 12's radar +
adaptive text) and the complaint persisted — itself evidence the gap
wasn't purely visual. Asked whether to fold in a full visual redesign
alongside a structural fix; the answer was structural-only, so a
palette/typography pass stayed explicitly out of scope. Two parallel
Explore passes and one Plan pass turned the complaint into three
concrete, file:line-verified findings before writing anything.

**Finding 1 — tickers were plain text in the two places that matter
most.** `templates/today.html`'s pick header and `templates/index.html`'s
`/full` table (both the top-picks cards and the near-miss table) never
linked to `/credit/<ticker>`, even though `templates/credit.html` and
`templates/investors.html` already linked tickers there, and
`today.html` already had a working JS precedent for exactly this kind
of link. The cross-reference existed; the hyperlink didn't. And
`/credit/<ticker>` itself had zero awareness of 13F holdings or
Today's Five membership for that ticker, even though `/today` already
had both in memory — the gap ran in both directions.

**Finding 2 — a real cross-page data-disagreement bug, not just a
missing link.** `app.py`'s `_book_refresher()` kept five published
caches (prices, vol, credit, liquidity, 13F) warm on a 5-minute cycle,
but four others — earnings, patterns, market regime, and last round's
cooldown history — were only ever fetched once, at process boot, and
frozen for the life of the deploy. Confirmed `_earnings_book(fetch=True)`
is a cheap published-file read, not the expensive live Nasdaq rebuild
(that's a separate object on its own cycle), so extending the same
9-book refresh was safe and free. The consequence was real: `/today`
computed earnings exclusively from the frozen book while `/check`'s
pre-trade path read a structurally different, separately-refreshing
calendar — a US ticker whose date Nasdaq corrected after the last
deploy could show two different dates on two different pages, live.

**Finding 3 — real day-over-day motion already existed and was
invisible.** Last round's `_record_todays_five()` publishes
`recent_picks.json` purely to feed the cooldown; nothing on the site
showed it to a reader. This is the direct, already-computed answer to
"more dynamic now that we have free actions" — no new GitHub Actions
compute needed, just surfacing data that already existed.

**What shipped, all three reusing existing patterns:**

1. Every ticker on Today's Five and `/full` is now a real
   `<a href="/credit/...">` link; "Held by N superinvestors" links to
   `/investors`. `/credit/<ticker>` gained an "Elsewhere on this site"
   card — real 13F holder count with a link back to `/investors`, and
   either "one of Today's Five right now" or a streak/return line, only
   ever rendered when there is something real to say (confirmed by a
   test: a ticker with no holders and no history renders no card at
   all, not an empty one).
2. `_book_refresher()` now keeps all nine published books on the same
   5-minute cadence. Scoped honestly, not oversold: this does not
   unify `/today` and `/check` onto one earnings source — `/check`
   still reads a structurally different, separately-refreshing
   calendar object — it only stops the four newly-added caches from
   being frozen at boot, so they now lag the scheduled scan's hourly
   publish by minutes instead of by up to a full deploy cycle.
   `/published` gained a `book_ts` field reporting when each of the
   four actually last refreshed, so this is verifiable in minutes, not
   provable only by waiting out a multi-hour staleness window.
3. `ranking.py` gained `session_streak(ticker, history, today)` — a
   deliberate sibling to `select_daily_five()`, sharing only the
   entry-sorting primitive (factored out as `_history_entries()`, with
   `select_daily_five()`'s own behavior and tests unchanged). Reads the
   same published cooldown history a second, purely descriptive way —
   never used to decide ranking, only to describe it — returning "new
   today," "on the list N of the last M sessions," or "back after N
   sessions off," and `None` whenever there's nothing sayable (fresh
   deploy, thin history). Wired into both `/today` (per pick) and
   `/credit/<ticker>` (the cross-reference card).

**A mistake caught while testing, not by re-reading the plan more
carefully.** This round's implementation plan was produced by a Plan
subagent, which read `tests/test_cooldown.py`'s ticker-extraction
regex (`r'class="tk">([A-Z0-9]+)'`) and reasoned that turning the pick
header's `<span class="tk">` into `<a class="tk" href="...">` needed no
test changes, since the class stayed `class="tk"`. Running the test
immediately after the markup change said otherwise: the regex required
`class="tk"` to be followed *immediately* by `>`, and the new `href=`
attribute sat in between, so the match failed outright ("no picks
rendered"). Widening the regex to `class="tk"[^>]*>` then overmatched
in a different way — `today.html` also has `<b class="tk"
style="font-size:1rem">` in the collapsed credit-index section further
down the page, and the widened pattern matched that too, inflating "5
picks shown" into "all 12 candidate tickers on the page." Fixed by
anchoring on the tag itself (`<a class="tk"[^>]*>...</a>`), which
matches only the pick headers. Logged in `MISTAKES.md`: a delegated
plan's "no test edit needed" is a claim to verify by running the test,
not a fact to carry forward, especially for anything built on
exact-string HTML matching.

**Verification:** `SKIP_WARM=1 python3 tests/test_*.py` — full suite
green. New file `tests/test_streak.py` pins: `session_streak()`'s
behavior in isolation (all four return shapes, the stale-isolated-entry
guard, no-ticker/no-today); `/today` renders the exact streak sentence
`session_streak()` computes directly on the same fixture, and renders
nothing when history is empty; every pick header on `/today` is a real
link; `/credit/<ticker>` renders the correct combination of held-by
count, `in_todays_five`, or a streak line for tickers in each state,
and renders no card at all for a ticker with nothing to say; and
`_book_refresher()` completes one full pass over all nine books with
every fetch failing, reaching `time.sleep()` rather than dying on the
first exception (exercised by stubbing `_published_get` to always
raise and `time.sleep` to break the loop after one pass).

### Round 15 — 2026-08-15 — "is buying the dip better than a coin flip" — answered honestly, per ticker

Operator: "identify the tickers where to buy the dip... validate with
past if it made good decisions... for example JNJ buy the dip today...
allow to run simulation... is it better of a coin flip or not?"

This one needed a straight answer before any code: this project
already ran exactly that test, at whole-market scale, and the pullback
signal lost to a coin flip — twice, on both shipped rule sets
(`STRATEGY.md`, `KNOWN_ISSUES.md`, 12 August 2026). **balanced**: 376
stocks, 25 seeds, z = −0.07, p = 0.50. **wide-net** (the rule set
behind the published board): 657 stocks, 40 seeds, z = +0.31, p =
0.41. Neither clears significance; random entry beat the signal
outright on the balanced run. Building a feature that presented "buy
the dip on JNJ" as a validated strategy would have directly
contradicted this project's own hardest-won result — refused, and said
so plainly, rather than building it anyway. Asked the operator how the
validator should work given that; the answer was the honest version:
real per-ticker data, correctly contextualized against the whole-market
finding, never a fresh verdict manufactured from a handful of trades.

**Why a fresh per-ticker statistical test isn't the right shape either
— checked, not assumed.** Investigated whether a rigorous per-ticker
version of the same permutation test was even feasible. It isn't, for
a structural reason: the whole-market test itself needed 30+ real
trades pooled across hundreds of stocks just to have any power
(`scripts/null_test.py`'s own `if n_real < 30: return 1` guard). A
single ticker's pullback signal fires only occasionally — the
wide-net run's own trade count (3,658 trades / 657 stocks ≈ 5.6
trades/ticker/5y) implies most single names would land at or below
double digits, far under that floor. A per-ticker p-value built on that
few events would be noise dressed as a verdict — the exact failure
mode this project's own `db.py` already names in its `edge_for()`
docstring: "a win rate off 2 trades is noise dressed as insight."

**What shipped instead — real, honest, and free to compute:**

1. **`db.trades_for(params, ticker)`** — a new read-only query against
   `db.py`'s existing `signals`/`signal_outcomes` tables, which the
   scheduled scan's regular backtest step already populates every run
   via `backtest.run_backtest()` → `db.record_backtest()`. Zero new
   simulation, zero new Yahoo calls — this data was already being
   computed and discarded (only ever read back as a whole-universe or
   already-aggregated `edge_for()` view; the individual trade rows for
   one ticker had no reader before this).
2. **`.github/workflows/null-test.yml`** now runs the whole-market
   permutation test — `scripts/null_test.py`, completely unchanged —
   on a weekly cron against the wide-net preset (the rule set actually
   shown live), and publishes `null_test.json` to the `screener-data`
   branch using the exact same carry-forward pattern
   `scheduled-scan.yml`/`pattern-sweep.yml`/`thirteenf.yml` already
   use, rather than the separate `null-test-result` branch it used to
   push to (which nothing ever read). Weekly, not daily, deliberately:
   the methodology and the answer do not move day to day, and
   `pattern-sweep.yml`'s own comment already states the reason a
   shorter cadence would be wrong — re-testing significance more often
   than the thing being tested actually changes is its own
   multiple-comparisons problem.
3. **`app.py`** gained `_null_test_book()` (registered in all three of
   `_warm_books()`/`_boot_books()`/`_book_refresher()` — the exact
   three registration points Round 14 had to retrofit onto four other
   caches, done right the first time here) and `_wide_net_params()`
   (memoized; matches `scripts/scheduled_scan.py::PRESETS["wide-net"]`
   exactly, so a `db.py` query against it hashes to the same config
   the scheduled scan's own backtest recorded trades under).
   `/credit/<ticker>` now computes `dip_edge` (`db.edge_for()`,
   already excludes anything under `MIN_SAMPLE=5`) and `dip_trades`
   (the real trade list) alongside the whole-market verdict.
4. **`templates/credit.html`** gained "Buying the dip in {ticker},
   checked": the whole-market verdict first, always, governing how
   everything below it should be read; then the ticker's own real
   trades, if any — with the aggregate win rate/avg R shown only when
   there are enough trades to say anything, and an explicit refusal to
   compute a rate below that floor. A ticker that has never triggered
   the signal says so plainly. Nothing here is a target or a
   recommendation — same rule as the rest of the site.

**Deliberately not this round:** the other bundled asks in the same
message (fundamentals/ratios, free sentiment, ticker logos, general
"research the web and GitHub for everything") — sequenced as separate
follow-up rounds by the operator's own choice, each needing its own
feasibility research (sentiment specifically flagged as the riskiest:
reliable free sentiment sources are scarce and prone to producing
noisy, invented-feeling numbers, which is the one thing this project
has never shipped).

**Verification:** `SKIP_WARM=1 python3 tests/test_*.py` — full suite
green. New file `tests/test_dip_check.py` pins: `db.trades_for()`
returns the right ticker's trades newest-first and an empty list for
an unmeasured one; `edge_for()`'s existing `MIN_SAMPLE` exclusion and
`trades_for()`'s own trade list agree on which tickers clear the bar;
`/credit/<ticker>` renders three distinct, verified states — enough
trades (exact win rate/avg R computed independently in the test and
matched against the rendered page, not eyeballed), too few trades (the
real trade still shown, the rate explicitly withheld), and never
triggered (says so, invents nothing); and the whole-market verdict
card renders its dated hardcoded citation when `null_test.json` isn't
published yet, and switches to the live published numbers — both the
`signal_alive=True` and `signal_alive=False` sentences, each rendering
its own distinct text — the moment it is.
