# Recipes

Short answers to common tasks. Commands run from the repository root unless a path says otherwise.

## Fail a CI job when a conclusion is fragile

`abmfact check` runs an audit and exits with status 1 when any claim is `FRAGILE` or `SHIFTED`. A
GitHub Actions step needs only:

```yaml
- run: pip install abmfact
- run: abmfact check audit.yaml --seeds 40 --jobs 4 -q
```

`--seeds` and `--jobs` override the audit's budget, so CI can use a smaller budget than a full study.
The command prints the flagged claims on the error stream. With `--out DIR` it also writes the report.

## Reproduce one run from a report

Every verdict in `report.html` comes with the commands that reproduce its runs. Each command is an
`abmfact replay`, which runs the model once with a given seed, parameters and knobs and prints the
outputs:

```bash
abmfact replay examples/command/audit.yaml --seed 3 --knobs '{"update_order": "synchronous"}'
```

## Choose an engine for a model

`abmfact recommend` ranks every engine for one model: whether it can run the model, how faithfully, which
audit checks become testable with it, and whether it is installed here.

```bash
abmfact recommend examples/ir/monolayer.yaml
abmfact recommend examples/ir/monolayer.yaml --details   # list every approximated part
```

For the monolayer example, the reference engine reads ABM-IR directly and can test every check, while
PhysiCell runs a translation with five approximated parts and can test determinism, random streams, the
time step and convergence. A native PhysiCell project runs only in PhysiCell, as given. Engines that cannot
run the model are listed with the reason.

## Compare a model across engines

`abmfact compare` runs one ABM-IR model in several engines over the same seeds and compares each metric with a
baseline engine against a budget you declare:

```bash
abmfact compare examples/ir/immune.yaml --engines reference,morpheus \
    --metric 'last(tumour_live)' --budget 0.25 --seeds 10 --jobs 4
```

A comparison file sets a budget per metric and can evaluate an audit's threshold claims in every engine;
`studies/gate3/` has six. [Comparing engines](compare.md) explains the verdicts and how to choose a budget.

## Audit a model written in another language

The `command` adapter runs any program that reads a JSON request on standard input and prints JSON
outputs. `examples/command` holds a complete example, a voter model, and [adapters](adapters.md)
describes the protocol.

## Audit an ABM-IR model

An audit file can name an engine and a model instead of a Python function:

```yaml
model:
  adapter: engine
  engine: reference
  model: monolayer.yaml          # relative to this file
```

Parameters and the `over` field of a monotone claim then use ABM-IR paths, such as
`experiment.max_time` or `rules[0].half_max`. `examples/ir/audit_monolayer.yaml` is a complete audit.
The claims use the model's observables, for example `last(cells)`.

## Check convergence

Add a `convergence:` section to the audit, or rely on its defaults, and run:

```bash
abmfact converge examples/ir/audit_monolayer.yaml --out results/convergence
```

The command refines the time step, and the voxel size too when the model supports it, then reports the
observed order and the grid convergence index for each claim metric. It exits with status 1 when a
metric is `NOT_CONVERGED`. [Convergence](convergence.md) explains the settings and the verdicts.

## Reuse runs with a store

A run store keeps each run's record and outputs under a key computed from everything that decides the
result. An identical request is then served from disk:

```bash
abmfact run-model examples/ir/chemotaxis.yaml --store runs/ --out chem1 --params '{"experiment.max_time": 120}'
abmfact run-model examples/ir/chemotaxis.yaml --store runs/ --out chem2 --params '{"experiment.max_time": 120}'
```

The first command prints `reference: ok`, the second `reference: cached`, with the same run id. In an
audit, `store: runs/` under `model:` does the same for every run, except determinism reruns, which
always execute. Install the `store` extra for Parquet, Zarr and DuckDB storage. Without it the store uses
NumPy files, CSV and a JSON lines index.

## Bring a PhysiCell project into ABM-IR, and back

```bash
abmfact import physicell path/to/project -o model.yaml
abmfact export model.yaml --to physicell -o exported-project/
```

Both commands print a fidelity report and write it beside the output, as Markdown and JSON. Every part
of the model is marked `exact`, `approximated`, `dropped` or `native`, with a note. Custom C++ code is
never translated. It is listed as a native extension so that nothing is lost silently. The
[PhysiCell page](engines/physicell.md) has the full mapping table.

## Run a PhysiCell project as it is

```bash
docker build -t abmfact/physicell:1.14.2 containers/physicell     # once, if no GNU g++ is installed
abmfact run-model path/to/project --engine physicell --out results/run --option threads=1
```

The engine builds the project once per hash of its sources, edits only the settings a run controls,
and reads the outputs into the common schema. Use `threads=1` for audits, because PhysiCell is not
deterministic for a fixed seed with more than one OpenMP thread.

## Run on a cluster

{class}`abmfact.runtime.slurm.SlurmBackend` writes one Slurm job array for many prepared native runs:
an `sbatch` script and a manifest of work folders and commands. It submits the array only when asked,
and it can wrap each command in a container backend. It runs native commands only, such as PhysiCell
runs, not in-process engines like the reference engine. {class}`abmfact.runtime.container.ContainerBackend`
runs the same commands in Docker, Podman or Apptainer, and records the image digest in each run record.
