<p align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="https://raw.githubusercontent.com/a7med7emedan/ABMfact/main/assets/logo-dark.svg">
    <img src="https://raw.githubusercontent.com/a7med7emedan/ABMfact/main/assets/logo.svg" alt="ABMfact" width="420">
  </picture>
</p>

<p align="center">
  <a href="https://github.com/a7med7emedan/ABMfact/actions/workflows/tests.yml"><img src="https://github.com/a7med7emedan/ABMfact/actions/workflows/tests.yml/badge.svg" alt="tests"></a>
  <img src="https://img.shields.io/badge/python-3.11%20%7C%203.12%20%7C%203.13-blue" alt="Python 3.11 to 3.13">
  <a href="https://github.com/a7med7emedan/ABMfact/blob/main/LICENSE"><img src="https://img.shields.io/badge/licence-Apache--2.0-green" alt="Apache-2.0"></a>
</p>

**ABMfact runs agent-based models in several simulators through one interface, and tells you whether
their conclusions are findings or implementation artefacts.**

A model stays in its own format, or is written once in ABM-IR, a shared description that ABMfact
translates for each simulator. Every translation reports what it kept exactly, approximated or dropped.
Every run is recorded with its engine version, settings and seed, so it can be repeated. On top sit the
checks: ABMfact reruns the model under changes that a correct implementation must not care about, and
refines its time step and voxel size. It then reports, claim by claim, which conclusions survive. Each
verdict comes with an interval, a reason and a command that reproduces it.

<p align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="https://raw.githubusercontent.com/a7med7emedan/ABMfact/main/assets/platform-dark.svg">
    <img src="https://raw.githubusercontent.com/a7med7emedan/ABMfact/main/assets/platform.svg" alt="How ABMfact fits together: a model in its own format or in ABM-IR is translated with a fidelity report, run by an engine on this machine, in a container or on Slurm, recorded in the run store, and checked by the seven audit checks and convergence, which write report.html, results.json and an RO-Crate record." width="100%">
  </picture>
</p>

## Supported tools

| Tool | How ABMfact runs it | ABM-IR translation | Status |
|---|---|---|---|
| [PhysiCell](https://github.com/MathCancer/PhysiCell) 1.14.2 and PhysiBoSS | projects run as they are, built with GNU g++ or in a pinned container | both ways, with a fidelity report | available |
| Reference engine | a small built-in 2D simulator with PhysiCell's mechanics and rule semantics | runs ABM-IR directly | available |
| [Mesa](https://github.com/projectmesa/mesa) 3 | model classes, through the `mesa` adapter | not yet | available for audits |
| [NetLogo](https://ccl.northwestern.edu/netlogo/) | `.nlogo` models, through pynetlogo | not yet | available for audits |
| Python | any `run(params, seed, knobs)` function | not needed | available |
| Any other language | a program that reads JSON on standard input and prints JSON | not needed | available |
| [CompuCell3D](https://compucell3d.org) 4.10.0 | projects run as they are, natively or in a pinned container | both ways, with a fidelity report | available |
| [Morpheus](https://morpheus.gitlab.io) 2.4.1 | models run as they are, natively or in a pinned container | both ways, with a fidelity report | available |
| Chaste, Agents.jl, BioDynaMo, Tissue Forge, FLAME GPU 2, Repast4Py | | | planned |

`abmfact engines` lists the engines installed on your machine and what each can do. Another package can
add an engine through an entry point; see
[writing an engine plug-in](https://github.com/a7med7emedan/ABMfact/blob/main/docs/engine-plugins.md).

Two commands work across engines. `abmfact recommend model.yaml` ranks every engine for one model: whether
it can run it, how faithfully its translation keeps the model, which checks become testable, and whether it
is installed here. `abmfact compare` runs one model in several engines over the same seeds and reports, for
each metric, whether each engine agrees with a baseline within a budget you declare beforehand. The
[Gate 3 study](https://github.com/a7med7emedan/ABMfact/tree/main/studies/gate3) compares the six reference
problems across four engines, and its
[follow-up](https://github.com/a7med7emedan/ABMfact/tree/main/studies/gate3_followup) reruns the INCONCLUSIVE
comparisons on fresh seeds.

## Install

ABMfact needs Python 3.11 to 3.13. Version 0.2 is not on PyPI yet, so install it from GitHub:

```bash
pip install "abmfact @ git+https://github.com/a7med7emedan/ABMfact"
```

Once it is released, `pip install abmfact` will do. Extras add optional parts: `[mesa]`, `[netlogo]`,
`[store]` (Parquet, Zarr and DuckDB), `[physicell]` (the pcdl output reader) and `[docs]`.

## Quick start

See it work on the planted-fault zoo, about a minute on four cores:

```bash
abmfact demo --jobs 4
```

**Audit your own model.** Write one YAML file with the model, its parameters, the claims to test and a
budget of seeds:

```bash
abmfact init                          # a commented audit.yaml to fill in
abmfact run audit.yaml --out results/
abmfact check audit.yaml              # exits with status 1 if any claim is FRAGILE or SHIFTED, for CI
```

```yaml
model:
  adapter: python
  target: mypackage.model:run          # run(params, seed, knobs) -> dict of outputs
  supports:                            # the implementation choices your model can vary
    update_order: {values: [random, id, synchronous], default: random}
    agent_order: true
    grid: true
    dt_scale: true
    pop_scale: true

params: {beta: 0.4, gamma: 0.1}

claims:
  - id: C1
    statement: "most of the population is infected"
    metric: attack_rate
    test: greater_than
    value: 0.6
  - id: C2
    statement: "the attack rate rises with transmission"
    metric: attack_rate
    test: monotone_increasing
    over: beta
    where: {beta: [0.25, 0.4]}

budget: {seeds: 100, jobs: 4}
```

Claims test conclusions, not raw numbers, because a number can move while the conclusion holds. Write
them from the paper or the abstract before running anything.

**Run a biological model.** Run an ABM-IR model, move a PhysiCell project into ABM-IR and back, and check
convergence:

```bash
abmfact run-model examples/ir/monolayer.yaml --engine reference --out results/monolayer
abmfact import physicell path/to/project -o model.yaml
abmfact export model.yaml --to physicell -o exported/
abmfact converge examples/ir/audit_monolayer.yaml
```

Any audit file can name an engine and a model (`adapter: engine`), so every check applies to models run
in any engine.

## Why

Galán et al. (2009) separated two ways a model can mislead. An *error* is code that does not do what
the modeller meant. An *artefact* is a result produced by an implementation choice that is not part of
the theory, such as the order agents act in. Both are hard to see by reading code, and both survive
peer review. ABMfact turns that distinction into automatic, repeatable tests, so the question "is this
an artefact?" gets an answer with a seed attached.

## The checks

<p align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="https://raw.githubusercontent.com/a7med7emedan/ABMfact/main/assets/how-it-works-dark.svg">
    <img src="https://raw.githubusercontent.com/a7med7emedan/ABMfact/main/assets/how-it-works.svg" alt="The audit pipeline: a model and its claims run under a baseline and six perturbed conditions, over many seeds; intervals and equivalence tests give a verdict per claim and check (ROBUST, SHIFTED or FRAGILE), written to report.html, results.json and an RO-Crate record." width="100%">
  </picture>
</p>

| Check | Question | Typical cause when it fails |
|---|---|---|
| `determinism` | Does the same seed always give the same result? | an unseeded generator, set or dict iteration order, wall-clock time |
| `update_order` | Does the conclusion survive a different activation order? | stale or partial state reads, buffers reused between steps |
| `agent_order` | Does it survive relabelling the agents? | `agents[:k]`, ties broken by id |
| `rng_streams` | Does it survive fresh, independent random streams? | a hard-coded seed, streams shared between processes |
| `grid` | Does it survive moving or rotating the world on the torus? | a boundary that does not wrap, absolute array positions |
| `time_step` | Does it survive halving the time step? | probabilities not converted with the current step |
| `population_scale` | Does it survive doubling the population at fixed density? | a size or count fixed at the calibration value |
| `convergence` | Does the result settle as the time step and voxel size shrink? | an explicit solver past its stability limit, a rate tied to one step size |

A check needs the model or its engine to honour its knob. When neither does, the check is reported as
`NOT_TESTABLE`, never skipped silently. The `rng_streams` check always runs: when a model does not manage
its own streams, ABMfact remaps the seed.

| Verdict | Meaning |
|---|---|
| `ROBUST` | the claim holds under every variant, and every result is statistically equivalent to baseline |
| `SHIFTED` | the claim still holds, but a result moved beyond its tolerance, or its spread doubled or halved |
| `FRAGILE` | the claim fails under a variant, or the same seed gave a different result |
| `INCONCLUSIVE` | the intervals are too wide to decide; the report says how many seeds would settle it |
| `BASELINE_FAILS` | the claim does not hold even before anything is changed |
| `NOT_TESTABLE`, `SKIPPED` | the model does not expose the knob, or the claim declares the check out of scope, with a reason |

Convergence has its own verdicts: `CONVERGED`, `NOT_CONVERGED`, `INCONCLUSIVE` and `NOT_TESTABLE`, with
the observed order and Roache's grid convergence index. Each audit writes `report.html` (verdicts,
interval plots, likely causes and reproducer commands), `results.json` for scripts and CI, and an
RO-Crate 1.1 record so the audit itself can be archived and cited.

For example, the zoo model with a stale-buffer bug (`abmfact.zoo:fault_stale_state`) gives:

| Claim | determinism | update order | agent order | rng streams | grid | time step | population |
|---|---|---|---|---|---|---|---|
| C1 most are infected | ROBUST | **SHIFTED** | ROBUST | ROBUST | ROBUST | ROBUST | ROBUST |
| C2 rises with beta | ROBUST | **SHIFTED** | ROBUST | ROBUST | ROBUST | ROBUST | ROBUST |
| C3 peak below 50% | ROBUST | **SHIFTED** | ROBUST | ROBUST | ROBUST | ROBUST | ROBUST |
| C4 peaks by t = 25 | ROBUST | **FRAGILE** | ROBUST | ROBUST | ROBUST | ROBUST | SKIPPED |

with the likely cause: *stale or partial state reads: agents see a state that depends on the scheduling
path*.

## Validation: the planted-fault zoo

A checker is only as good as its evidence that it catches what it claims to catch, and nothing else.
`abmfact.zoo` holds one clean SIR model on a torus and seven copies of it, each with one realistic bug,
plus a second clean model with a diffusing nutrient and one copy whose diffusion step breaks its
stability limit. `abmfact demo` audits all ten with every check. The figure below is drawn from a real
run with 100 seeds per condition, and the test suite requires exactly this matrix.

<p align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="https://raw.githubusercontent.com/a7med7emedan/ABMfact/main/assets/matrix-dark.svg">
    <img src="https://raw.githubusercontent.com/a7med7emedan/ABMfact/main/assets/matrix.svg" alt="Planted-fault detection matrix: each of the eight faulty models is caught by its own check, dt_unscaled also by convergence, and the two clean models by none." width="100%">
  </picture>
</p>

Each fault is caught by its own check and by no other, with one documented overlap: `dt_unscaled` is
also caught by convergence, because both checks refine the time step. The clean models are never
flagged. The bugs are described in
[the zoo page](https://github.com/a7med7emedan/ABMfact/blob/main/docs/zoo.md) and marked `# FAULT` in
the source.

## Documentation

The manual is in [`docs/`](https://github.com/a7med7emedan/ABMfact/tree/main/docs) and builds with
Sphinx as web pages and as a PDF. It covers getting started, the user guide, ABM-IR and each engine,
recipes, the command-line and Python API references, and a guide to writing an engine plug-in.

## Roadmap

These are plans, not features yet:

- **Next:** more engines (Chaste, Agents.jl, BioDynaMo, Tissue Forge, FLAME GPU 2, Repast4Py), and the
  monotone claims of the reference problems compared across engines, which needs parameter sweeps in each.
- **Later:** calibration, sensitivity analysis and surrogate models through existing tools as plug-ins;
  more engines; and a twin mode that re-audits a model each time new data recalibrates it.

## Limits

ABMfact checks implementation choices that should not matter. It does not check whether the model is a
good description of the world, and it cannot find a bug that changes no stated claim. Its power depends
on the seeds you give it: an `INCONCLUSIVE` verdict is an honest answer, not a pass. Many comparisons are
made in one audit, so treat a single marginal `SHIFTED` as a lead to investigate, not a conclusion. A
translated model is a second implementation of the original, so read its fidelity report before
comparing results.

## Citing

If ABMfact helps your work, please cite it using
[CITATION.cff](https://github.com/a7med7emedan/ABMfact/blob/main/CITATION.cff) (GitHub shows a "Cite this
repository" button). The idea it implements comes from:

> Galán, J. M., Izquierdo, L. R., Izquierdo, S. S., Santos, J. I., del Olmo, R., López-Paredes, A., &
> Edmonds, B. (2009). Errors and artefacts in agent-based modelling. *Journal of Artificial Societies and
> Social Simulation*, 12(1), 1. https://www.jasss.org/12/1/1.html

When you run models in PhysiCell or PhysiBoSS through ABMfact, please cite those projects too.

## Licence

Apache-2.0. See [LICENSE](https://github.com/a7med7emedan/ABMfact/blob/main/LICENSE). The PhysiCell
template files bundled with the PhysiCell engine keep their BSD-3-Clause licence.
