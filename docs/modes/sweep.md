# Sweeps

`abmfact sweep` runs one model over a design of parameter settings, each with the same seeds, and records metrics
for every run. Use it to see how a result moves with its parameters before asking which ones matter most.

```yaml
# examples/modes/sweep.yaml
title: monolayer division and contact inhibition
model: ../ir/monolayer.yaml
engine: reference
params:
  experiment.max_time: 1440
parameters:
  - {name: "cell_types[epithelial].cycle.rates[0]", low: 0.0007, high: 0.0028, scale: log}
  - {name: "rules[0].half_max", low: 0.25, high: 1.0}
design: {method: lhs, n: 8, seed: 1}
seeds_per_point: 3
metrics: ["last(cells)", "last(spread)"]
jobs: 4
```

```bash
abmfact sweep examples/modes/sweep.yaml --store runs/ --out results/sweep
```

## Parameters and designs

A parameter is named the way runs take it: an ABM-IR dotted path, a native path with `/` for engines that read
their own files (such as `overall/dt_mechanics` in PhysiCell), or `option:NAME` for an engine option. Each has a
`low` and `high`, a `scale` (`linear`, or `log` for a positive range spanning orders of magnitude) and may be an
`integer`.

| Design | Points | Use |
|---|---|---|
| `grid` with `levels` | every combination of evenly spaced levels | one or two parameters, readable plots |
| `lhs` with `n` | a Latin hypercube: one point in each of `n` strata of every parameter | several parameters, a fixed budget |
| `sobol` with `n` | a scrambled Sobol sequence, balanced in powers of two | space filling; other sizes give a warning |

Points are drawn by `scipy.stats.qmc`, so the core needs no extra package. ABM-IR paths are checked against the
model before any run.

## Outputs

`sweep.json`, `design.csv` (the points), `runs.csv` (each run's point, seed, run id and metrics), `summary.csv`
(each point's mean with its interval per metric) and `report.html`. Every point runs the same seeds, so
differences between points are not blurred by different random streams.

## Resuming and clusters

With `--store`, a sweep that is run again reuses every finished run, so an interrupted sweep resumes where it
stopped and a larger design adds only its new points.

On a cluster, `--backend slurm` writes a Slurm job array for the runs not yet in the store, with a `slurm` section
for its settings (`partition`, `time`, `mem`, `cpus_per_task`, `account`, `max_parallel`, and `command`, the way to
call ABMfact there). Each array task runs `abmfact run-model` into its own folder. `--submit` also calls `sbatch`.
Once the array has finished, `abmfact sweep FILE --collect --out DIR` reads the runs back and summarises them.
