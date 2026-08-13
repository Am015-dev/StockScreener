# Working on this repo

## Mistake log — read it, then keep it honest

`MISTAKES.md` is a log of mistakes an *agent* has actually made working on
this repo — wrong assumptions, misread caches, test bugs, misdiagnoses —
each with what was assumed, what was true, how it was caught, and the
one-line rule that prevents the repeat. It is not a bug tracker for the
product (that's `KNOWN_ISSUES.md`); it's a record of the agent's own
reasoning going wrong.

- **Before starting a non-trivial task** — anything touching the
  scheduled scan, the credit model, published-data caching, GitHub
  Actions, or a test file with shared module-level state — skim
  `MISTAKES.md` for entries in the same area. If one applies, say so
  inline ("this prevented X, per MISTAKES.md") rather than silently
  avoiding the mistake with no trace of why.
- **The moment a mistake is caught** — a test fails because of a bad
  assumption, a live check contradicts something believed true, a human
  corrects a wrong turn — append an entry before moving on, not at the
  end of the session. Follow the format at the top of the file. Five
  minutes to write beats an hour lost to the same mistake later.
- **What counts:** the agent assumed something false and acted on it,
  and the falseness cost real time to discover. What doesn't: ordinary
  bugs found in the product during ordinary review (those go in
  `KNOWN_ISSUES.md` or just get fixed and committed), and routine
  back-and-forth that isn't actually a wrong assumption.
- **Keep entries short and specific** — a ticker, a timestamp, an actual
  error message. A vague entry ("was confused about caching") is nearly
  as useless as no entry.

## Everything else

- Standing product rules — the falsified entry signal, no invented
  probabilities, no rendering unmeasured things as safe — are documented
  in `STRATEGY.md` and `KNOWN_ISSUES.md`. Read them before touching
  `screener.py`, `credit.py`, `patterns.py`, or any user-facing template.
- Run the relevant test file(s) after any change (`SKIP_WARM=1 python3
  tests/test_*.py`), and the full suite before deploying.
- This repo's docs write in a specific voice: precise, evidence-named,
  no filler ("BABAF #1 on an 8× unit error", not "there was a data
  issue"). Match it when writing to any `.md` file here.
