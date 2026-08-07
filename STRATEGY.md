# Does this strategy work?

No. Not at a size anyone can trade. This file records what was measured, so
the question does not have to be re-litigated from intuition.

## The bar

A configuration is worth trading only if it clears all three:

- profit factor ≥ 1.5
- maximum drawdown no worse than the S&P 500 over the same window
- Sortino ratio ≥ 1.0

Measured on a **5-position portfolio at 1% risk per trade**, not on "every
signal taken". That distinction turned out to matter more than any filter
(see *Capacity*, below).

## Results

Five years, ~1,000 US stocks, each row adding one change to the row above.
Produced by `scripts/sweep.py` via the `Strategy sweep` workflow.

| config | PF | win | max DD | Sortino | 4-yr return |
|---|---|---|---|---|---|
| raw rules | 0.97 | 23% | −38.9% | −0.14 | −11.3% |
| + regime & relative-strength gates | 1.00 | 24% | −39.8% | 0.02 | +0.9% |
| + 0.20% round-trip costs | 0.94 | 24% | −47.2% | −0.24 | −14.3% |
| + 1.5×ATR stops | **1.20** | 32% | −25.6% | 0.72 | **+25.0%** |
| + 15-bar time exit | 1.07 | **39%** | **−18.4%** | 0.30 | +11.2% |
| + $5 / 500k-share floors | 1.07 | 39% | −18.4% | 0.30 | +11.2% |

**SPY over the same window: +94% to +100%, −18.8% drawdown.**

Nothing clears the bar. The best configuration returns a quarter of what
holding the index returned, with a worse drawdown.

## What was actually learned

**Stop placement was the whole problem.** Switching from a fixed percentage
below the pivot to 1.5×ATR moved the return from −14.3% to +25.0% and
halved the drawdown. Every other filter was noise by comparison.

**Capacity is the hidden killer.** Taking every signal implies ~80 open
positions — 80% of the account at risk at once. Constrained to 5 positions,
the same rules collapse (profit factor 1.14 → 0.68 in the original test),
because the signals *cluster*: a market-wide dip fires dozens on one day and
their outcomes are correlated, so five slots hold one bet, not five. A
synthetic control with the same holding-period asymmetry but *uncorrelated*
outcomes did not reproduce the collapse; adding clustering did. The
diversification that makes the headline number survivable is unavailable to
a small account.

**Costs are fatal to a thin edge.** 0.20% round-trip cost 6 points of profit
factor and 8 points of drawdown. Any strategy whose edge does not survive
friction never had one.

**The liquidity floors did nothing here.** The $5 and 500k-share filters
removed zero trades from the 1,000 largest US stocks. They matter only on a
wider universe.

## What the defaults are, and why

`stop_mode="atr"`, `stop_atr_mult=1.5`, `max_hold_bars=15`, `cost_pct=0.20`.

The 15-bar cap trades profit factor (1.20 → 1.07) for a much higher win rate
(32% → 39%) and the only drawdown that has matched the benchmark. For a
small account that is the better trade: −26% is the drawdown you abandon a
system in, and a 39% win rate is far easier to keep following than 32%.

These are the best *measured* rules, not good rules.

## Before believing any future result

Six configurations were tested. Testing enough of them will eventually
produce one that clears the bar by luck. Any configuration that passes needs
validating on a window that was not used to select it, and the sample needs
to be large enough that the profit factor is not an artifact of a handful of
trades.
