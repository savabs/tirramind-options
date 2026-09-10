# Why the published error moved, and why slices kept falling back

2026-09-10 · reproduce with `PYTHONPATH=src python research/fit_stability.py`
· raw output in `fit_stability_results.json` and `across_time.json`

## The question

The surface's published error moved between 0.65 and 1.04 volatility points across
runs half an hour apart, and one to four of eleven BTC expiries fell back from the
refined SVI slice to the eSSVI backbone on any given fit. Both numbers are on a
public page. Either the optimiser is unstable or the acceptance rule is, and it
matters which.

## The optimiser is not the cause

Three refits of one identical chain:

| Refit | Error, vol pts | Slices falling back |
|---|---|---|
| 1 | 0.6455846189623061 | 21.9d, 77.9d, 196.9d |
| 2 | 0.6455846189623061 | 21.9d, 77.9d, 196.9d |
| 3 | 0.6455846189623061 | 21.9d, 77.9d, 196.9d |

Bit-identical to sixteen figures. There is no randomness in the fit: the warm
starts are deterministic functions of the quotes and the backbone, and Adam and
L-BFGS are deterministic. So every bit of the movement comes from the data or from
what the acceptance rule does with it.

## The acceptance rule is deciding on extrapolation

A refined slice is rejected if its total variance dips below the previous expiry's
anywhere on the overlap of the two slices' *validated* ranges. The validated range
is three times the quoted span, capped at a log-moneyness of 2. For a chain whose
quotes stop at 0.4, that check runs out to 2.0, which is a strike at 13% of the
forward. Nobody quotes there. Nobody trades there.

Every rejection on the first chain, with the location of the worst dip:

| Pair | Worst dip in total variance | At log-moneyness | Both slices quoted there? | Worst dip where both are quoted |
|---|---|---|---|---|
| 14.9d → 21.9d | −4.9e−3 | +0.55 | no | +2.1e−3 |
| 49.9d → 77.9d | −3.2e−2 | −1.26 | no | +1.1e−2 |
| 105.9d → 196.9d | −4.5e−2 | −2.00 | no | +3.8e−2 |

In each case the dip is outside the quoted region, and inside it the slices are
comfortably ordered the right way, by ten to a hundred times the bid-ask spread
expressed in the same units. The rule is rejecting good fits because two
extrapolations cross each other in a wing that has no quotes in it.

Two things this is **not**. It is not a tolerance problem: the dips are 5e−3 to
5e−2, far larger than any spread-scaled tolerance would forgive. And it is not
market data containing a real calendar arbitrage: where there is data, there is no
violation.

## It happens on every chain

Eight chains, one every ninety seconds:

| | Shipped rule | Checked only where both slices have quotes |
|---|---|---|
| Error, vol pts, mean | 0.749 | 0.700 |
| Error, run-to-run spread | 0.260 | 0.132 |
| Inside the spread, mean | 88.7% | 91.5% |
| Slices demoted, per chain | 2 to 4 | 0 |

**Twenty-three demotions across the eight chains. Not one of them fails the
calendar check where both slices have quotes.** Correlation between the number of
demotions and the published error is 0.61 under the shipped rule and −0.13 once
the rule is narrowed, so the demotions are most of the wobble rather than a
symptom of it.

The backbone alone would give 1.84 volatility points and 61% inside the spread, so
demoting a slice is expensive: it is the refinement that buys the published
accuracy, and it is being discarded on wings.

## What this costs on the live page

The at-the-money term structure kinks wherever a slice was demoted, because the
backbone's at-the-money can sit several volatility points from its refined
neighbours. That kink is an artefact of the rule, not a feature of the market. The
page already draws demoted points hollow and says so, which was the right call
before knowing why, and is a better call now.

## The fix, and its honest cost

Run the calendar check on the intersection of the two slices' **quoted** ranges,
not their extrapolated ones, and narrow the published guarantee to match: calendar
consistency is claimed where there are quotes on both sides, and beyond the quoted
range the surface is extrapolation and is labelled as such.

The cost is real and worth stating. The current rule is conservative in a
defensible way: it refuses to publish a surface whose extrapolated wings cross,
even though nothing there is tradeable. Narrowing the check trades that
conservatism for a fit that is better where the market actually is. Since the
whole product claim is "here is the error, per expiry, measured against real
quotes", enforcing a property where there are no quotes at the cost of accuracy
where there are is the wrong way round.

## What is still open

- The check compares adjacent expiries. Consistency across non-adjacent pairs is
  implied only when the pairwise ranges coincide, which they do not always. Not
  measured here.
- Eleven minutes of chains is a short window. The direction of the result is
  unambiguous, since zero of twenty-three demotions survive the narrower check,
  but the magnitudes will move with the market.
- ETH was not measured. It demotes less often, which is consistent with a
  wing-extrapolation cause, since its quoted range is wider relative to its smile,
  but that is an inference and not a measurement.
