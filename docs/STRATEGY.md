# Today's Five — product specification and build instructions

**Status:** specification, not yet built.
**Audience:** the developer implementing it.
**Prerequisite reading:** `patterns.py` module docstring, `analysis.py` module
docstring, `KNOWN_ISSUES.md`.

---

## 1. Why this exists

The tool currently measures a great deal and decides nothing. It can tell you a
company's distance to default, and separately how violently a share moves, and
separately when it reports — three fragments on three pages, and no answer. A
user opens it, reads numbers, and still does not know what to do. That is the
defect this specification closes.

The output is **at most five names each trading morning**, each with a complete
trade plan a person can execute in two minutes without interpreting anything.

## 2. The honest basis — read this before designing anything

Two results constrain what may be claimed, and both were measured in this
repository, not assumed:

1. **The original pullback signal is dead.** Coin-flip entry through
   byte-identical stop/target/exit code did as well or better, on both shipped
   rule sets (p = 0.50 and 0.41). It must never be a ranking input.
2. **Simple price shapes are mostly volatility in disguise.** Eleven shapes
   looked "tradeable" over a year until the comparison group was drawn from the
   same volatility bucket on the same day; the survivors were detecting that a
   3% move selects violent stocks, not anything about the shape.

So **this product does not forecast direction, and must not imply that it
does.** What it ranks on is survivability, cost efficiency, portfolio fit, and —
only where a shape has actually earned it by holding up on data it was not
chosen on — a measured edge.

This is not a weaker product. Position sizing and loss control are what
separate accounts that compound from accounts that don't, and neither requires
knowing where a stock is going.

## 3. Hard filters — a name failing any of these is excluded, not scored

| # | Filter | Threshold | Why |
|---|---|---|---|
| F1 | Median dollar volume, last 20 sessions | ≥ $20M | You must be able to exit at size without moving the price. |
| F2 | Scheduled earnings | none within `horizon + 2` sessions | A report gaps the price straight through a stop. The stop does not protect you. |
| F3 | Distance to default (non-financials with filings) | ≥ 2.0 | Below this the balance sheet is the dominant risk and no chart matters. |
| F4 | Share price | ≥ $5 | Below this the spread is a material share of the move. |
| F5 | Annualised volatility | ≤ 90% | Above this a sane stop is so wide the position becomes too small to matter. |
| F6 | Credit measurable | must be measured, or F3 is waived **and the name is flagged** | "Not measured" is never silently treated as "safe". |

Every exclusion is recorded with its reason and published. A screen that shows
only what passed is not evidence of anything.

## 4. The score — 100 points, every component measurable

Applied only to names that clear §3. No component is a forecast.

### 4a. Survivability — 40 points
- **Credit headroom, 20 pts.** Percentile rank of distance to default across
  today's measured universe. Reuse `credit.py`; the values are already
  published in `credit.json`.
- **Volatility, 20 pts.** Percentile rank of *inverse* annualised volatility.
  Lower volatility scores higher, because the same risk budget buys a larger and
  therefore more meaningful position for the same money at risk.

### 4b. Cost efficiency — 20 points
`(1σ 10-day move) ÷ (round-trip cost)`, percentile-ranked.
A share whose typical ten-day move is 12× the cost of trading it is a better
vehicle than one where it is 3×. Use `patterns.ROUND_TRIP_COST_PCT`.

### 4c. Portfolio fit — 20 points
`1 − max(|correlation|)` against the user's existing holdings over 120
sessions, percentile-ranked. Reuse `concentration.correlation()` and
`concentration.returns_frame()` — do not write a new correlation routine.
With no holdings loaded, award the full 20 to everyone and say so on the page.

### 4d. Confirmed pattern edge — 20 points
**Zero for every name unless `published/patterns.json` contains a shape marked
`confirmed: true`.** If one exists and fires on this name today, award points in
proportion to its `holdout.after_costs_pct` against the best confirmed shape.

If nothing is confirmed — the likely case, and an acceptable one — the page must
say in plain words: *"No price shape has yet held up on data it was not chosen
on, so no name is being ranked on one."* Never silently redistribute these 20
points; a 100-point scale that quietly becomes an 80-point scale hides the fact
that the forward-looking component is empty.

## 5. The trade plan attached to each name

Reuse `analysis.risk_frame()` — it already derives the stop and the share count.

| Field | How it is computed | Shown as |
|---|---|---|
| Entry | last close | "Buy at market, or a limit at {price}" |
| Stop | 2σ of a 10-day move below entry (`analysis.STOP_SIGMAS`) | "{price} — {pct}% away. Closer than this and normal noise takes you out." |
| Size | `risk_budget ÷ (entry − stop)` | "{n} shares, about {value} committed, {budget} at risk" |
| Typical move | 1σ 10-day move, both directions | "This share usually travels ±{pct}% over ten sessions" |
| Time stop | `horizon` sessions | "Out after {n} sessions whether or not it has moved" |
| Earnings | next scheduled date | "Reports {date} — {n} sessions away" |
| Falsifier | the specific condition | "This is wrong if it closes below {stop} or the sector rolls over" |

**Do not print a price target.** A target implies a forecast this tool has not
earned. "Typical ten-day move" is the same arithmetic without the false claim,
and it is what sizes the trade.

## 6. The one-page strategy shown to the user

Fixed text, not generated. It belongs on the page because a shortlist without
rules is a way to lose money slowly:

- **Risk 1% of the account on any one trade.** The share count in each plan is
  computed from this. It is the single most important number here.
- **At most 8 open positions**, and **at most 25% of the book in one sector.**
- **Never add to a loser.** The stop is the exit; moving it is how a 1% loss
  becomes a 10% loss.
- **Exit on whichever comes first:** the stop, the time stop, or a scheduled
  earnings report.
- **Expect to be wrong roughly half the time.** This is normal and the plan
  assumes it. The account grows because the losses are capped at 1% and the
  position sizes are right, not because the picks are clever.

## 7. Implementation

### 7.1 `ranking.py` (new)
```
score(candidates: list[dict], holdings: list[dict], credit: dict,
      patterns_report: dict | None, risk_budget: float) -> list[dict]
```
Applies §3 then §4. Returns every candidate sorted by score, each carrying
`score`, `components` (the four sub-scores, so the page can show the arithmetic),
and `excluded_because` where applicable. Pure function, no I/O, no network — so
it is testable without fixtures.

### 7.2 `plan.py` (new)
```
trade_plan(row: dict, risk_budget: float, horizon: int) -> dict
```
Builds §5 from an already-scored row. Reuses `analysis.risk_frame()`.

### 7.3 `scripts/scheduled_scan.py` (extend)
After the existing scan, call `ranking.score(...)` then `plan.trade_plan(...)`
for the top five, and publish `published/today.json`:
```json
{"as_of": "...", "horizon": 10, "risk_budget": 100,
 "picks": [...], "excluded": [{"ticker": "...", "why": "..."}],
 "pattern_component_active": false,
 "universe": 400, "passed_filters": 137}
```

### 7.4 `/today` route and `templates/today.html` (new)
Follow `templates/patterns.html` for the token set and the ≤560px stacking —
do not invent a second design system. Structure:
1. The five names, each a card: ticker, one-sentence thesis, then the plan table.
2. "Why these five" — the score components, visible.
3. "What was excluded and why" — collapsed, but present.
4. The §6 strategy, always visible.

`/today` becomes the site root. The current brief moves to `/brief`.

### 7.5 Tests (new — `tests/test_ranking.py`)
1. A name failing each of F1–F6 is excluded, with the right reason.
2. A name with no credit measurement is flagged, never silently passed.
3. With `patterns.json` holding nothing confirmed, `pattern_component_active`
   is `false` and every name scores 0 on §4d.
4. Sizing round-trips: `shares × (entry − stop)` ≈ `risk_budget`, within one
   share.
5. Ranking is stable — same inputs, same order — so a user is not shown a
   reshuffled list on a page refresh.

### 7.6 Release gate (extend `scripts/release_gate.py`)
- `/today` renders five or fewer names, never more.
- Every name shows a stop and a share count.
- No page anywhere prints the word "target" as a price.
- The strategy block is present.
- Nothing scrolls sideways at 390px.

## 8. Sequence

| Order | Task | Depends on |
|---|---|---|
| 1 | `ranking.py` + tests | nothing |
| 2 | `plan.py` + tests | 1 |
| 3 | `today.json` in the scheduled scan | 1, 2 |
| 4 | `/today` route and template | 3 |
| 5 | Release-gate assertions | 4 |
| 6 | Root swap, `/brief` redirect | 5 green |

## 9. What this specification refuses to do, and why

- **No price targets.** Implies a forecast that has been measured and found
  absent.
- **No "buy" / "strong buy" labels.** They compress away the risk information
  that is the actual product.
- **No back-tested equity curve on the front page.** The one signal this project
  did back-test failed its own null twice; a curve invites the reader to trust
  exactly what has not survived testing.
- **No pattern-based ranking until a pattern has held up out of sample.** The
  component exists and stays at zero until earned.
