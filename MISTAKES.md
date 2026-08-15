# Mistakes

A running log of mistakes an agent working on this repo actually made —
caught either by running the code, reading a live response, or a human
saying "that's wrong." Not a list of bugs found in the *product*
(`KNOWN_ISSUES.md` is that); this is the record of the agent getting
something wrong while building or checking it, so the same mistake costs
five minutes to avoid next time instead of an hour to repeat.

Append an entry the moment a mistake is caught — during the same task,
not at the end of it. Each entry: what was assumed, what was actually
true, how it was caught, and the one-line rule that would have prevented
it. Skip entries that are just "a bug existed and I fixed it" — this file
is for the agent's own reasoning going wrong, not for ordinary product
bugs found during ordinary review (those belong in `KNOWN_ISSUES.md` or a
commit message).

## Format

```
## YYYY-MM-DD — short title
**Assumed:** what I believed going in.
**Actually:** what was really true.
**Caught by:** the specific check that surfaced it (a test run, a live
curl, a human correction) — never "on reflection."
**Rule:** the one sentence that prevents the repeat.
```

---

## 2026-08-15 — read a stale local `main` ref without fetching first, diagnosed a bug that had already been fixed

**Assumed:** `git show main:.github/workflows/scheduled-scan.yml` and
`git show main:vix.py` reflected the CURRENT state of the repository's
`main` branch — that GitHub Actions' `schedule:` triggers were running
unpinned, stale code from `main`, and that this was the reason a live
feature round (VIX regime signal, Altman Z-score) was invisible on the
site.
**Actually:** my local `main` ref was stale — last synced days earlier
in the session, 3 commits and 6 days behind `origin/main`. Those 3
missing commits had ALREADY fixed exactly this problem (pinned
`ref: claude/pullback-uptrend-screener-vvlzeb` in both
`scheduled-scan.yml`'s and `thirteenf.yml`'s checkout steps) in an
earlier round of this same session. A full plan to "fix" an
already-fixed problem was written and approved before the real cause
(a 20-hour incremental-refresh throttle plus a weekend gap in the
cron schedule — nothing broken at all) was found.
**Caught by:** running `git fetch origin main` and re-checking —
`git rev-parse main` vs `git rev-parse origin/main` disagreed, and
`git log main..origin/main` showed the 3 commits my diagnosis had
missed entirely.
**Rule:** before reading ANY branch with `git show branch:path` or
`git log branch`, especially one this session has not been actively
committing to, run `git fetch origin <branch>` first — a local ref is
a cache, not a live view, and it can be silently days stale.

## 2026-08-13 — reused a sentinel timestamp across two fixture loads, hid a memo cache

**Assumed:** setting `app._creds.update(data=..., ts=9e9)` twice in the
same test — once for the original 40-ticker fixture, once again after
merging in two EU tickers — would make the second load visible
everywhere that read `_creds`.
**Actually:** `_credit_view()` memoizes on the tuple
`(_creds["ts"], _book["ts"])`; reusing the identical `9e9` sentinel both
times produced an identical cache key, so the memo silently kept serving
the FIRST load's restated book — missing the two new EU tickers
entirely — and every downstream assertion about them read `None` for
fields that were clearly set in the fixture.
**Caught by:** an isolated single-ticker repro of the same fixture
passed while the full multi-ticker test file failed on the identical
assertion — the difference was the file's second `ts=9e9` collided with
its first, and the isolated repro never had a first load to collide
with.
**Rule:** when a test reloads a store this codebase memoizes on `ts`,
bump the sentinel between loads (`9e9`, `9e9 + 1`, ...) — an identical
timestamp is an identical cache key, not "fresh data."

## 2026-08-13 — splitting a 2-tuple into a 3-tuple dropped a staleness gate

**Assumed:** rewriting `app.py::_published_earnings()` to return
`(cal, complete, single_source)` instead of `(cal, ok)` could keep the
`complete`/`ok` value as a straight `bool(book.get("complete"))` — the
staleness check (`shift <= 3`) already governed whether `cal` got
populated, so it looked like `complete` didn't need to depend on it too.
**Actually:** the pre-existing behavior tied "the calendar can be trusted
as complete" to freshness as well as the raw published flag — a calendar
five days stale must read as incomplete even though the source publisher
still says `"complete": true` in the underlying data, because "complete"
here means "you may trust an absence from this map as the all-clear," and
a five-day-old absence is not trustworthy. Dropping that link let a stale
calendar report `ok=True`, silently re-opening the exact fail-open shape
item 6 was built to close, just moved to the freshness axis instead of
the US/EU-region axis.
**Caught by:** `tests/test_server_robustness.py`'s existing staleness pin
(`cal == {} and ok is False` after backdating `as_of` by 5 days), which
predated this session's changes and had nothing to do with EU earnings —
it caught a regression in an unrelated refactor of the same function.
**Rule:** when a function's return value grows a new field, re-derive
every existing field from the same inputs that produced it before —
don't assume "unchanged expression" means "unchanged behavior" once a
nearby computation (like a staleness flag) that used to be folded into it
is now sitting in a separate local variable.

## 2026-08-13 — fixed the fail-open bug in three call sites, missed that /check never reaches the fourth

**Assumed:** auditing every call site that read the old global `cal_complete`
flag (`ranking.py::filters`, `plan.py::trade_plan`, and the `/check` route's
calls to `analysis.build`/`pretrade.check`) and switching them to the new
per-candidate `cal_covered`/`earnings_single_source` fields closed the
fail-open bug everywhere it could occur — the `/check` route's earnings
dict already came from `_published_earnings()`, which carries the `eu`
book, so passing the right `complete_for_ticker` there was the whole fix.
**Actually:** `/check` never actually reaches `_published_earnings()` in
production. It first tries `screener._earnings_calendar(build=False)` — a
separate, US-only, disk-cached live calendar that predates the published
book — and only falls back to `_published_earnings()` when that live map
is completely empty. On a warm instance (essentially always, since the
disk cache persists across requests) it is never empty, so the EU book's
dates never merged in at all: every EU ticker's pre-trade check answered
"earnings date could not be verified" regardless of what the scheduled
runner had published for it. Confirmed live against production —
`POST /check {"ticker": "TYT.L"}` blocked with that exact message despite
`earnings.json`'s `eu.map` on the data branch holding `"TYT.L": 83`.
**Caught by:** a manual live curl against the deployed `/check` endpoint
after the scan had published fresh EU data — not by any of the tests
written for this feature, all of which exercised `_today_candidates()`
and `ranking.filters()` directly and never actually called the `/check`
route, so this path's separate earn-resolution logic was untested.
**Rule:** when a fix touches "all call sites of X", grep for every
function that could independently answer the same question through a
*different* data path (a second cache, a second fetch) — auditing call
sites of the shared helper is not the same as auditing every route that
answers the same user-facing question, and a live curl against the actual
endpoint the feature is meant to change is worth more than tracing the
code path by inspection.

## 2026-08-13 — "everything seems old" meant staleness, not styling

**Assumed:** a report that "everything seems old" on `/full` was about
data freshness, and spent the first pass investigating the scan cron
schedule and the `/status` freshness fields to rule that out.
**Actually:** the freshness system was working correctly and honestly;
the complaint was about the page's visual chrome (colored bars,
box-shadowed cards, accordion styling) looking dated next to the
redesigned `/today` and `/credit`.
**Caught by:** asking the user directly with `AskUserQuestion` after the
data-freshness path came up clean, rather than continuing to guess.
**Rule:** when a vague complaint has more than one plausible reading and
the cheap one (asking) is available, ask before spending a full
investigation on the expensive one.

## 2026-08-13 — a manually-triggered scan looked like it changed nothing

**Assumed:** after pushing a fix to `screener.py` and manually dispatching
`scheduled-scan.yml`, the very next `/status` fetch would reflect the new
data, since the workflow run and the git push to `screener-data` both
showed as completed/fresh.
**Actually:** `app.py` caches the published index for `PUBLISHED_TTL`
(900s) after the last fetch; three separate `/status` calls in the
following two minutes all returned the same pre-scan snapshot,
byte-for-byte, because none of them fell outside the cache window.
**Caught by:** comparing the `results_ts` epoch in the response against
the real wall clock (`date -u`) instead of assuming a 200 meant fresh
data, then finding `POST /published/refresh` in `app.py` to force it.
**Rule:** after fixing a bug that only shows up in published/cached data,
confirm the fix against a **forced** refresh (or wait out the TTL) before
concluding it didn't work — a cache hit and "the fix didn't apply" look
identical from outside.

## 2026-08-13 — assumed the scheduled scan runs on the branch that dispatched it

**Assumed:** because `list_workflow_runs` showed `head_branch: main` for
every past run, the scheduled scan executes `main`'s copy of
`screener.py` — meaning a fix pushed only to the deploy branch wouldn't
take effect until merged into `main`.
**Actually:** GitHub's `schedule:`/`workflow_dispatch` triggers always
report the *default* branch as `head_branch` regardless of which ref the
job actually checks out; the workflow file itself pins
`actions/checkout@v4` to `ref: claude/pullback-uptrend-screener-vvlzeb`
(a previous, already-documented fix for this exact confusion, in the
workflow's own comments). The fix was live the moment it reached the
deploy branch.
**Caught by:** reading the checked-in workflow YAML instead of trusting
the run-list metadata.
**Rule:** `head_branch` on a scheduled/dispatched Action run is not the
branch that ran — read the checkout step's `ref:` to know what actually
executed.

## 2026-08-13 — two lists disagreeing about the same ticker, mistaken for one bug at first glance

**Assumed:** "duplicated stocks" meant a literal repeated row in one
table, so the first check was `results.csv` for duplicate ticker rows
(none found), which briefly looked like the complaint might not be
reproducible.
**Actually:** the duplication was across *two different tables* — a
ticker whose only failure was `unverified` landed in both `pending`
(rendered as an unscored BLOCKED row) and `near_board` (rendered as a
scored close-miss with its own reason), because `near_rows` was built
from the same `near` list `pending` filters, without excluding what
`pending` had already claimed.
**Caught by:** fetching `/status` directly and set-intersecting the
ticker lists from `pending` and `near_board`, instead of stopping at the
single-table CSV check that came back clean.
**Rule:** "duplicated" from a user can mean duplicated *across* surfaces,
not just within one — check every list that can independently contain
the same key before concluding a report doesn't reproduce.

## 2026-08-12 — clobbered a module-level variable name in a test

**Assumed:** `r` was a safe, obvious name for a risk-free-rate argument in
a new `horizon_view` test block appended near the end of `test_credit.py`.
**Actually:** `r` was already bound to a report dict earlier in the same
file (`r = credit.report(...)`), so passing `r` as the rate argument threw
`TypeError: unsupported operand type(s) for +: 'dict' and 'float'` deep
inside the Merton solver, several frames from the actual mistake.
**Caught by:** running the test and reading the traceback, not by review.
**Rule:** in a long, single-namespace test file, grep for a variable name
before reusing it — don't trust that a short common name is free.

## 2026-08-12 — cross-sector monotonicity assumption in a peer-percentile test

**Assumed:** leverage percentiles would decrease monotonically across all
12 tickers in a two-sector test fixture, in one combined pass.
**Actually:** each sector's percentile scale resets independently at
100%, so the least-levered member of the *second* sector can outrank the
most-levered member of the *first* in absolute terms while still being
"100th percentile of its own six" — the combined-list assertion was
comparing numbers that were never meant to be compared against each other.
**Caught by:** running the test, seeing the failure, and re-deriving what
"percentile" actually promises (rank within a group, not across groups).
**Rule:** when a measurement is deliberately group-relative, write the
test's assertions within each group separately — a global assertion
smuggles in a global-comparability claim the code never made.

## 2026-08-12 — assumed `filter_sector` should exclude a companyless-peer ticker

**Assumed:** `credit.filter_sector(book, "9999", level="sic_major")` for a
ticker whose major group nobody else in the book shares should return an
empty dict.
**Actually:** the function has no notion of "self" — it returns every
member of the matching group, and the calling ticker itself is a member
of its own group of one. An empty result requires a `sic` that matches
*nobody*, not just nobody *else*.
**Caught by:** running the test and reading what the function actually
returned before deciding the test's expectation was wrong, not the code.
**Rule:** when a pure function's contract doesn't mention excluding the
caller, don't assume it does — read the signature's actual inputs before
writing the assertion.

## 2026-08-12 — n_measured computed from the sector-narrowed book, not the whole one

**Assumed:** it was safe to compute `n_book` (the "measured against N
companies" count shown on the credit report) at the same point the
sector-filtered `book` variable was already in scope.
**Actually:** by that point `book` had already been narrowed by
`credit.filter_sector()`, so the reported count silently became the
*sector's* size instead of the *whole measured universe's* size — wrong
for the "against the whole market" sentence specifically.
**Caught by:** re-reading the render call against what each sentence on
the page actually claims to be counting, before shipping — not by a
failing test (none existed for this at the time).
**Rule:** when a value gets narrowed in-place partway through a function,
compute anything that must reflect the *pre-narrowed* state before the
narrowing line runs, not after — and name the two so a diff makes the
distinction obvious (`whole_book` vs `book`).
