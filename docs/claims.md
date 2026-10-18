# Writing claims

A claim is a conclusion stated so that a computer can check it. Write claims from the paper, its
abstract or your own hypotheses, before you run the audit, and keep them in version control. Changing a
claim after seeing a verdict defeats the purpose.

```yaml
claims:
  - id: C1                              # unique, short
    statement: "most agents are infected"
    metric: attack_rate                 # see the grammar below
    test: greater_than                  # greater_than | less_than | between | monotone_increasing | monotone_decreasing
    value: 0.6                          # for between: [low, high]
    where: {beta: [0.4]}                # optional parameter grid, merged over params
    tolerance: 0.05                     # optional equivalence margin, in the metric's units
    skip_checks:                        # optional, every entry needs a reason
      population_scale: "the claim is about one population size"
```

## Tests

| Test | Holds when | Fails when |
|---|---|---|
| `greater_than` | the interval for the mean lies above `value` at every grid point | it lies below `value` at any point |
| `less_than` | the interval lies below `value` everywhere | it lies above `value` anywhere |
| `between` | the interval lies inside `[low, high]` everywhere | it lies wholly outside anywhere |
| `monotone_increasing` | the OLS slope over `over` is confidently positive | the slope is confidently negative, or confidently smaller than 10% of the baseline slope |
| `monotone_decreasing` | the same, mirrored | the same, mirrored |

Anything between holds and fails is *uncertain*, and leads to an `INCONCLUSIVE` verdict with the number
of seeds that would settle it.

Monotone tests need `over: <parameter>` and at least two values of it under `where`.

## Metric grammar

| Form | Example | Meaning |
|---|---|---|
| `name` | `attack_rate` | a scalar output |
| `name[i]` | `prevalence[-1]` | one element of a series, negative indices allowed |
| `f(name)` | `max(prevalence)` | `f` is one of `mean`, `sum`, `min`, `max`, `argmax`, `argmin`, `first`, `last`, `std` |

Nothing else is accepted. Metric expressions are parsed, never evaluated as code.

## Tolerance

Equivalence needs a margin: how far a result may move and still count as the same. The default is 10%
of the absolute baseline mean at each grid point. Set `tolerance` when you know what difference matters
in your field, for example two days for a peak time.
