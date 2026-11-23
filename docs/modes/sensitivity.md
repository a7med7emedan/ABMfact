# Sensitivity analysis

`abmfact sensitivity` estimates how much each parameter drives a metric, with bootstrap intervals, through SALib
(Herman and Usher, 2017). It needs the `[sensitivity]` extra.

A sensitivity file is a sweep file with a `sensitivity` section instead of `design`:

```yaml
# examples/modes/sensitivity.yaml
model: ../ir/monolayer.yaml
params: {experiment.max_time: 720}
parameters:
  - {name: "cell_types[epithelial].cycle.rates[0]", low: 0.0007, high: 0.0028, scale: log}
  - {name: "rules[0].half_max", low: 0.25, high: 1.0}
sensitivity: {method: sobol, n: 32, seed: 1}
seeds_per_point: 2
metrics: ["last(cells)"]
```

```bash
abmfact sensitivity examples/modes/sensitivity.yaml --store runs/ --out results/sobol
abmfact sensitivity examples/modes/sensitivity.yaml --method morris --n 10 --out results/morris
```

| Method | Indices | Points |
|---|---|---|
| `sobol` (Saltelli et al., 2010) | `S1`, the share of the variance from one parameter alone, and `ST`, its total share including interactions; `S2` for pairs with `second_order: true` | N × (D + 2), or N × (2D + 2) with second order |
| `morris` (Morris, 1991) | `mu_star`, the mean absolute elementary effect, with `mu` and `sigma` | N × (D + 1) for N trajectories |

The indices describe the parameters' distributions as stated: uniform on each range, or log-uniform on a log
scale. Morris effects are per unit of that scaled range.

## Stochastic models

Each point's output is the mean over its seeds, so it carries seed noise, and the indices split that noise up with
everything else. The report gives the **noise share**: the mean variance between a point's seeds over the total
variance of all runs. A large share means more seeds per point would sharpen the indices.

## Warnings

The command warns, before or after the runs:

- when N is not a power of two, or is below 512 for Sobol, or when second-order indices are asked of a small N;
- when any interval's half-width is above 0.1;
- when Morris has fewer than 10 trajectories or an odd number of levels;
- when seed noise is above 20% of the output variance.

The example uses N = 32 on purpose, and its output says the sample is too small. Outputs: `indices.json`,
`indices.csv`, `report.html`, and the sweep's own tables under `sweep/`.
