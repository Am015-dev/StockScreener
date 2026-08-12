---
name: site-review
description: Strictly evaluate the LIVE site against the project's aim, score it out of 100, and drive improvement until it clears 80. Use when asked to review, score, or improve the product end to end.
---

# Site review: score the tool against its aim, then close the gap

## The aim, stated once

A tool a private investor with a small book would genuinely profit from
using — where "profit" means better decisions and avoided losses, never a
promised edge: skip the trade that gaps through your stop at earnings,
notice you are buying a position you already own, see how far a company
sits from trouble on its debts before you lend it your savings, and know
what the round trip costs. It must be **decisive** (answers first, working
second), **clear** (a first-time visitor understands every sentence),
**alive** (a reason to come back tomorrow), and **honest without being
hedge-ridden** (methodology lives on one page; answers do not each carry a
disclaimer). It must never recommend trades from the falsified pattern,
invent probabilities, or present an unmeasured thing as safe.

## How to run a review

1. Audit the LIVE deployment, never the code alone: render every page in
   headless Chromium at 390px (the release gate in
   `scripts/release_gate.py` shows the mechanics), press the actual
   buttons, POST to the actual endpoints. Cross-check every number that
   appears twice.
2. Read every user-visible sentence and ask: would a first-time visitor
   understand it, and does it help them decide anything?
3. Score the rubric below. Be harsh: this rubric exists because "looks
   fine to the author" shipped a page that hid itself, a report that
   contradicted its own table, and a check that answered its one question
   wrongly.
4. If the total is **below 80**: fix the highest-value items, run the
   full test suite, deploy, wait for the release gate to pass against the
   new commit, and re-score from scratch. Repeat until ≥ 80. Do not ask
   the user anything; decide and act.

## The rubric (100 points)

**1. Decisive answers — 25.** Every interactive surface opens with a
decision-grade answer, not evidence: the check leads with "Do not buy
this today: …" / "Nothing here argues against it"; a credit report leads
with the number, the band, and what drives it. Deduct 5 per surface that
makes the reader derive the answer; deduct 10 if any answer is a wall of
findings with no verdict.

**2. Clarity for a first-timer — 20.** No unexplained jargon (R, RSI,
Sortino, DD) outside the collapsed research section. Sentences under ~25
words in primary surfaces. Numbers that appear twice agree everywhere.
Deduct 4 per confusing primary surface, 5 per internal contradiction.

**3. Value density and life — 20.** The front page must offer something
worth returning for daily, above the fold, without typing anything:
what's reporting soon, who moved near trouble, what changed since
yesterday. Deduct 10 if a visitor who types nothing sees nothing that
changed today; deduct 5 if the page's first screen is questions instead
of information.

**4. Honesty without hedging — 15.** One methods/limits page carries the
caveats. Answers state facts plainly; at most ONE short trust line per
page. Deduct 3 per repeated disclaimer paragraph on an answer surface;
deduct 15 if any surface recommends a trade from the falsified pattern,
quotes an invented probability, or renders an unmeasured thing as safe
(these are floors — the page fails the section outright).

**5. Correctness under a browser — 10.** The release gate passes against
the live commit; no JS errors; no leaked template syntax, NaN, undefined;
dark mode holds everywhere. Deduct 5 per live defect.

**6. Coverage and freshness — 10.** The credit box answers for the
stocks people actually ask about (large-cap US coverage substantially
complete; refusals carry reasons); the earnings answer works minutes
after a deploy; data age is visible. Deduct 3-5 per material gap.

## Scoring discipline

- Score each section with named evidence (quote the sentence, name the
  URL, show the number) — a score without evidence is invalid.
- An independent scorer (a fresh agent that did not write the fixes)
  produces the final number. The author never grades their own round.
- Record each round: date, commit, score by section, top defects, what
  was changed. Append to `docs/review-log.md`.

## What "improve" means here, in priority order

1. Anything that makes an answer wrong, self-contradictory, or invisible.
2. Anything that makes the reader wait or retry (warm-up blocks, cold
   starts without explanation).
3. Answers-first restructuring; disclaimer consolidation.
4. New decision-relevant data the free runner can collect (published
   calendars/books) — prefer feeding the tool more data over adding more
   caveats.
5. Delight: faster, cleaner, one memorable daily surface.

Never "improve" by weakening honesty floors: no invented probabilities,
no resurrecting the falsified pick, no rendering unmeasured as safe.
