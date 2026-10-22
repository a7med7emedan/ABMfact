# Convergence

The seven audit checks ask whether a conclusion survives changes that should not matter. Convergence asks
a different question: does a result stop changing as the numerics get finer? A result that still moves
when the time step or the voxel size shrinks is a property of the discretisation, not of the model.

```bash
abmfact converge audit.yaml --out results/
```

## What it does

For each claim's metric at each parameter point, the model runs at three levels. Level `k` scales the
time step by `2^-k`, and also the voxel size when the model declares `dx_scale`. Each level runs the
audit's seeds, so every value is an ensemble mean with an interval. With `f3` the coarsest and `f1` the
finest mean, and `e21 = f2 - f1`, `e32 = f3 - f2`:

| Quantity | Formula |
|---|---|
| convergence ratio | `R = e21 / e32`: monotone if `0 < R < 1`, oscillatory if `-1 < R < 0`, divergent if `abs(R) >= 1` |
| observed order | `p = ln(e32 / e21) / ln 2` |
| extrapolated value | `f1 + (f1 - f2) / (2^p - 1)` |
| grid convergence index | `GCI = 1.25 abs(e21 / f1) / (2^p - 1)` (Roache, three levels) |

The order and GCI are reported only when both differences are established, that is when their intervals
exclude zero; differences within noise give no order rather than a misleading one.

## Verdicts

| Verdict | Meaning |
|---|---|
| `CONVERGED` | the change between the two finest levels lies within the tolerance |
| `NOT_CONVERGED` | the change grows with refinement, or lies outside the tolerance, or a level blew up (non-finite values) |
| `INCONCLUSIVE` | the intervals are too wide; the result says how many seeds would settle it. Also when every seed gave the same value at some level, as in the audit |
| `NOT_TESTABLE` | the model does not declare `dt_scale` |
| `SKIPPED` | the claim lists `convergence` under `skip_checks`, with a reason |

The tolerance is the claim's own, or 10% of the fine mean, as in the audit, and the same three-outcome
equivalence test decides. `converge` exits with status 1 when any claim is `NOT_CONVERGED`.

Settings go in an optional top-level `convergence:` section of the audit file:

```yaml
convergence:
  refine: [dt, dx]        # dt alone, or dt and the voxel size together
  ratio: 2
  safety_factor: 1.25
  deterministic: false    # true for a deterministic model: zero between-seed variance is then expected
```

## Validation

The planted-fault zoo includes a fault that only convergence catches: `unstable_diffusion`, an explicit
diffusion step taken without the substeps its stability limit needs. At the audit's resolution, and
when only the time step is halved, it is identical to the clean model; refining the time step and the
voxel size together pushes it past the limit and it blows up. `fault_dt_unscaled` is caught by
convergence as well as by the time-step check, because both refine `dt` and a rate tied to the
calibration step genuinely does not converge. See [the zoo](zoo.md).

Roache, P. J. (1994). Perspective: a method for uniform reporting of grid refinement studies. *Journal of
Fluids Engineering*, 116(3), 405-413.
