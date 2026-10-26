# Command-line reference

Generated from the argument parser by `scripts/make_cli_reference.py`. Every command also takes
`-h` or `--help`, and `abmfact --version` prints the version. Every command exits with 2 on an
invalid audit file, claim, model or engine, and prints the reason.

## `abmfact run`

Run an audit and write results.

```text
abmfact run audit [--seeds SEEDS] [--jobs JOBS] [--quiet] [--out OUT]
```

| Argument | Meaning | Default |
|---|---|---|
| `audit` | audit YAML file | required |
| `--seeds` | override budget.seeds |  |
| `--jobs` | override budget.jobs |  |
| `-q`, `--quiet` | print only errors |  |
| `--out` | output directory | `abmfact-results` |

Exit status: 0 when the audit finished.

## `abmfact check`

Run an audit; exit 1 if any claim is FRAGILE or SHIFTED.

```text
abmfact check audit [--seeds SEEDS] [--jobs JOBS] [--quiet] [--out OUT]
```

| Argument | Meaning | Default |
|---|---|---|
| `audit` | audit YAML file | required |
| `--seeds` | override budget.seeds |  |
| `--jobs` | override budget.jobs |  |
| `-q`, `--quiet` | print only errors |  |
| `--out` | also write outputs to this directory |  |

Exit status: 0 unless any claim is FRAGILE or SHIFTED, then 1.

## `abmfact demo`

Audit the planted-fault zoo and print the detection matrix.

```text
abmfact demo [--seeds SEEDS] [--jobs JOBS] [--quiet] [--no-convergence]
```

| Argument | Meaning | Default |
|---|---|---|
| `--seeds` | seeds per condition | `100` |
| `--jobs` | worker processes | `1` |
| `-q`, `--quiet` | no progress messages |  |
| `--no-convergence` | only the seven audit checks (faster) |  |

Exit status: 0 when the detection matrix is as expected, 1 otherwise.

## `abmfact init`

Write a commented audit file template.

```text
abmfact init [--adapter {python,mesa,netlogo,command,engine}] [--output OUTPUT] [--force]
```

| Argument | Meaning | Default |
|---|---|---|
| `--adapter` | the kind of model the template is for | `python` |
| `-o`, `--output` | file to write, or - for stdout | `audit.yaml` |
| `--force` | overwrite an existing file |  |

Exit status: 0 when the template was written, 2 when the file exists and `--force` was not given.

## `abmfact replay`

Run one model run and print its outputs.

```text
abmfact replay audit --seed SEED [--params PARAMS] [--knobs KNOBS]
```

| Argument | Meaning | Default |
|---|---|---|
| `audit` | audit YAML file | required |
| `--seed` | the seed to run | required |
| `--params` | JSON object merged into the audit's params |  |
| `--knobs` | JSON object of knobs, e.g. {"update_order": "id"} |  |

Exit status: 0 when the run finished.

## `abmfact run-model`

Run one ABM-IR model (or native project) in an engine.

```text
abmfact run-model model [--engine ENGINE] [--out OUT] [--seed SEED] [--params PARAMS] [--knobs KNOBS] [--store STORE] [--option KEY=VALUE] [--quiet]
```

| Argument | Meaning | Default |
|---|---|---|
| `model` | ABM-IR file, or a native project folder for engines that take one | required |
| `--engine` | engine name (see: abmfact engines) | `reference` |
| `--out` | output directory | `abmfact-run` |
| `--seed` | the seed to run | `0` |
| `--params` | JSON object of parameter overrides, e.g. {"experiment.max_time": 600} |  |
| `--knobs` | JSON object of knobs, e.g. {"dt_scale": 0.5} |  |
| `--store` | run store directory; an identical earlier run is reused |  |
| `--option` | engine option, e.g. threads=1 |  |
| `-q`, `--quiet` | print only errors |  |

Exit status: 0 when the run finished or was reused from the store, 1 when it failed.

## `abmfact converge`

Check that claim metrics converge as dt (and voxel size) are refined.

```text
abmfact converge audit [--seeds SEEDS] [--jobs JOBS] [--quiet] [--out OUT]
```

| Argument | Meaning | Default |
|---|---|---|
| `audit` | audit YAML file | required |
| `--seeds` | override budget.seeds |  |
| `--jobs` | override budget.jobs |  |
| `-q`, `--quiet` | print only errors |  |
| `--out` | write convergence.json to this directory |  |

Exit status: 0 unless a claim metric is NOT_CONVERGED, then 1.

## `abmfact import`

Translate a simulator's project into an ABM-IR model.

```text
abmfact import {physicell} project [--output OUTPUT] [--quiet]
```

| Argument | Meaning | Default |
|---|---|---|
| `{physicell}` | the simulator the project is for | required |
| `project` | project folder | required |
| `-o`, `--output` | ABM-IR file to write | `model.yaml` |
| `-q`, `--quiet` | print nothing; the fidelity report is still written |  |

Exit status: 0 when the model was written.

## `abmfact export`

Translate an ABM-IR model into a simulator's project.

```text
abmfact export model --to {physicell} --output OUTPUT [--base BASE] [--quiet]
```

| Argument | Meaning | Default |
|---|---|---|
| `model` | ABM-IR file | required |
| `--to` | the simulator to write for | required |
| `-o`, `--output` | project folder to write | required |
| `--base` | project whose C++ to use |  |
| `-q`, `--quiet` | print nothing; the fidelity report is still written |  |

Exit status: 0 when the project was written.

## `abmfact compare`

Run one model in several engines and compare against a disagreement budget.

```text
abmfact compare target [--engines ENGINES] [--metric METRIC] [--budget BUDGET] [--seeds SEEDS] [--jobs JOBS] [--out OUT] [--store STORE] [--workdir WORKDIR] [--quiet]
```

| Argument | Meaning | Default |
|---|---|---|
| `target` | comparison YAML file, or an ABM-IR model with --engines and --metric | required |
| `--engines` | comma-separated engines for a model; the first is the baseline |  |
| `--metric` | metric to compare, e.g. 'last(cells)'; repeatable |  |
| `--budget` | relative budget for --metric (default 0.10) | `0.1` |
| `--seeds` | seeds per engine |  |
| `--jobs` | runs at once |  |
| `--out` | folder for compare.json |  |
| `--store` | run store: finished runs are kept and reused |  |
| `--workdir` | folder for run folders |  |
| `-q`, `--quiet` | print only errors |  |

Exit status: 0 unless an engine DISAGREEs with the baseline on a metric, then 1; 2 for an invalid comparison.

## `abmfact engines`

List engines, their capabilities and whether they run here.

```text
abmfact engines [--json]
```

| Argument | Meaning | Default |
|---|---|---|
| `--json` | print JSON instead of a table |  |

Exit status: 0.

## `abmfact recommend`

Rank the engines for one model: can they run it, how faithfully, what checks.

```text
abmfact recommend model [--details] [--json]
```

| Argument | Meaning | Default |
|---|---|---|
| `model` | ABM-IR file, or a native project folder | required |
| `--details` | list every approximated or dropped part |  |
| `--json` | print JSON instead of a table |  |

Exit status: 0, also when no engine can run the model; the output says why.
