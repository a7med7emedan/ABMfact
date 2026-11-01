# Calibration

`abmfact calibrate` fits parameters so a model's observables match a target. It needs the `[calibrate]` extra.

```yaml
# examples/modes/calibrate.yaml: recover a planted division rate
model: ../ir/monolayer.yaml
engine: reference
params: {experiment.max_time: 720, experiment.save_interval: 120}
parameters:
  - {name: "cell_types[epithelial].cycle.rates[0]", low: 0.0007, high: 0.0028, scale: log}
target:
  engine: reference
  seeds: 20
  seed_start: 1000
  params: {"cell_types[epithelial].cycle.rates[0]": 0.0021}
observables: [cells, spread]
seeds_per_evaluation: 5
method: bayesopt
bayesopt: {n_init: 6, n_iter: 10, seed: 1}
```

```bash
abmfact calibrate examples/modes/calibrate.yaml --store runs/ --out results/calibrate
abmfact calibrate --methods
```

## Targets

| Target | Form |
|---|---|
| `data` | a CSV with columns `time`, `observable`, `value` and optionally `sd` |
| `ensemble` | an `ensemble.json` written by `abmfact ensemble` |
| `engine` | an engine run as an ensemble now, with `seeds`, `seed_start`, `params` and `options` |

## The distance

Each evaluation runs the setting over its seeds, takes each observable's mean at the target's times (linear
interpolation between saves), and scores the fit as the mean over all fitted points of
((simulated mean − target) / scale)². The scale is the target's standard deviation. Where the target has no
spread (data without `sd`, or a time where every seed agrees, such as the starting count), the observable's range
stands in, and the result says which was used.

## Methods

| Method | Result | Interval |
|---|---|---|
| `bayesopt` | the best fit, by Bayesian optimisation (bayesian-optimization, MIT) over `n_init` Latin hypercube points and `n_iter` further ones | for each parameter, with the others at the best fit, the values whose distance a bootstrap of the best fit's seeds cannot tell from the best; each edge is bisected `refine` times (default 4) |
| `abc-smc` | the posterior median, by approximate Bayesian computation with sequential Monte Carlo (pyABC, BSD-3; Toni et al., 2009) | the 90% credible interval from the weighted posterior |
| `uq-physicell` | as `abc-smc`, with UQ-PhysiCell's safeguards on weights and kernels and its standardised kernel | as `abc-smc` |

ABC-SMC needs many evaluations (`population` × generations), so it suits cheap models. The `fitmulticell` slot
detects FitMultiCell, whose calibration drives a native Morpheus executable and is not bridged yet; use `abc-smc`.
Other tools register a calibrator through the `abmfact.calibrators` entry point.

Each method warns when its result should be read with care: a best value at the edge of its range, a best fit
that came late in the search, an interval that reaches the end of the range, a small effective sample in the last
ABC generation, or a credible interval that barely narrows the prior.

## Outputs

`calibration.json` (settings, target with its SHA-256, the result, diagnostics and every evaluation),
`evaluations.csv` and `report.html`. Fitted ABM-IR values are written as `calibrated.yaml`, the model with only
those values changed and a provenance note naming the method, target and distance. Fitted engine options are
written as `calibrated_options.json`.
