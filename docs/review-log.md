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
