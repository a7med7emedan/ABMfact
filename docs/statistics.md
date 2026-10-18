# Statistics

Everything here is implemented in `src/abmfact/stats.py` and `src/abmfact/audit.py`.

## Intervals

`alpha` (default 0.05) is used one-sided, so every interval is a two-sided `1 - 2*alpha` interval
(90% by default) and each side is a one-sided test at `alpha`.

- Means: Student t-interval over seeds.
- Differences between conditions: Welch interval for `mean(variant) - mean(baseline)`.
- Monotone claims: ordinary least squares slope of the metric on the parameter, over every run at every
  grid point, with a t-interval.

## Claim status in one condition

`holds`, `fails` or `uncertain`, as described in [claims.md](claims.md).

## Comparing a variant with the baseline

At every parameter point:

1. **Mean, three outcomes.** With tolerance `tol`: `equivalent` when the difference interval lies inside
   `(-tol, tol)`, `shifted` when it lies wholly outside, otherwise `undetermined`. This is two
   one-sided tests for equivalence, extended so that a confident difference is reported as such.
2. **Spread.** Brown-Forsythe test, with the ratios of standard deviations and of interquartile ranges.
   Spread counts as shifted only when `p < alpha / 10` and both ratios are beyond 2 in the same
   direction. Spread is secondary evidence, so the bar is high. It is not compared in the
   `population_scale` check.

## Verdict rules, in order

1. `SKIPPED` if the claim lists the check in `skip_checks`.
2. `NOT_TESTABLE` if the model does not declare the knob.
3. `BASELINE_FAILS` if the claim fails at baseline.
4. `determinism`: `FRAGILE` if any rerun differs at all, otherwise `ROBUST`.
5. Zero between-seed variance at baseline: `FRAGILE` for `rng_streams` (the seed has no effect),
   `INCONCLUSIVE` for the other checks.
6. `INCONCLUSIVE` if the claim is uncertain at baseline.
7. `FRAGILE` if the claim fails under any variant.
8. `SHIFTED` if any mean or spread comparison is shifted.
9. `INCONCLUSIVE` if anything is uncertain or undetermined, with `seeds_needed`.
10. `ROBUST` otherwise.

## Seeds needed

For an interval of half-width `h` from `n` seeds that misses a decision boundary by `g`, ABMfact reports
`ceil(n * (h / g)^2)`, capped at 10 000. It is a planning figure, not a guarantee.

## Multiple comparisons

An audit makes many comparisons: checks times variants times parameter points times claims. No
correction is applied to the mean comparisons, because the equivalence decision needs the whole interval
outside the tolerance, which is conservative for practical differences. Spread uses a tenfold stricter
level. Even so, read a single marginal `SHIFTED` as a lead to investigate with more seeds, not as a
conclusion.

## Determinism of the audit

Seeds are `seed_start, seed_start + 1, ...`. Every condition uses the same seeds. Permutation seeds for
`agent_order` and remapped seeds for `rng_streams` are derived from each seed with
`numpy.random.SeedSequence`. With the same audit file and versions, `results.json` is identical apart
from its `created` timestamp, whether runs are serial or parallel.
