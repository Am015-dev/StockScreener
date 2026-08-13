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
