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
