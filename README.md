<p align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="https://raw.githubusercontent.com/a7med7emedan/ABMfact/main/assets/logo-dark.svg">
    <img src="https://raw.githubusercontent.com/a7med7emedan/ABMfact/main/assets/logo.svg" alt="ABMfact" width="420">
  </picture>
</p>

<p align="center">
  <a href="https://github.com/a7med7emedan/ABMfact/actions/workflows/tests.yml"><img src="https://github.com/a7med7emedan/ABMfact/actions/workflows/tests.yml/badge.svg" alt="tests"></a>
  <img src="https://img.shields.io/badge/python-3.11%20%7C%203.12%20%7C%203.13-blue" alt="Python 3.11 to 3.13">
  <a href="LICENSE"><img src="https://img.shields.io/badge/licence-Apache--2.0-green" alt="Apache-2.0"></a>
</p>

**ABMfact tells you whether the conclusions of an agent-based model are findings or implementation
artefacts.**

You state your model's conclusions as checkable claims. ABMfact reruns the model under changes that a
correct implementation must not care about: activation order, agent labels, random streams, where the
world sits on the grid, the time step and the population size. It then reports, claim by claim, which
conclusions survive. Every verdict comes with an interval, a reason and a one-line command that
reproduces it.

## Why

Galán et al. (2009) separated two ways a model can mislead. An *error* is code that does not do what
the modeller meant. An *artefact* is a result produced by an implementation choice that is not part of
the theory, such as the order agents act in. Both are hard to see by reading code, and both survive
peer review. ABMfact turns that distinction into automatic, repeatable tests, so the question "is this
an artefact?" gets an answer with a seed attached.

## Sixty-second start

```bash
pip install abmfact            # add [mesa] or [netlogo] for those frameworks
abmfact demo                   # audit the planted-fault zoo (about two minutes)
abmfact init                   # write a commented audit.yaml for your model
abmfact run audit.yaml --out results/
```

`results/report.html` opens in any browser. `abmfact check audit.yaml` exits with status 1 when any claim
is fragile, so it fits in continuous integration.

## What you write

One YAML file: the model, its parameters, the claims and a budget.

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
  - id: C3
    statement: "the epidemic peaks within 25 time units"
    metric: peak_time
    test: less_than
    value: 25
    skip_checks:
      population_scale: "peak time legitimately grows with system size"

budget: {seeds: 100, jobs: 4}
```

Claims test conclusions, not raw numbers, because a number can move while the conclusion holds. Write
them from the paper or the abstract before running anything.

## What you get

For example, auditing the zoo model with a stale-buffer bug (`abmfact.zoo:fault_stale_state`, claims as
in [examples/zoo/audit.yaml](examples/zoo/audit.yaml)) gives:

| Claim | determinism | update order | agent order | rng streams | grid | time step | population |
|---|---|---|---|---|---|---|---|
| C1 most are infected | ROBUST | **SHIFTED** | ROBUST | ROBUST | ROBUST | ROBUST | ROBUST |
| C2 rises with beta | ROBUST | **SHIFTED** | ROBUST | ROBUST | ROBUST | ROBUST | ROBUST |
| C3 peak below 50% | ROBUST | **SHIFTED** | ROBUST | ROBUST | ROBUST | ROBUST | ROBUST |
| C4 peaks by t = 25 | ROBUST | **FRAGILE** | ROBUST | ROBUST | ROBUST | ROBUST | SKIPPED |

with the likely cause: *stale or partial state reads: agents see a state that depends on the scheduling
path*.

Each run writes three files:

- `report.html`: the verdict table, one interval plot per claim and condition, likely causes, and
  reproducer commands. Self-contained, light and dark.
- `results.json`: every interval, comparison and verdict, for scripts and CI. Identical across reruns
  apart from the timestamp.
- `ro-crate-metadata.json`: an RO-Crate 1.1 record of the audit file, results and report, with SHA-256
  hashes, so the audit itself can be archived and cited.

## The checks

| Check | Question | Typical cause when it fails |
|---|---|---|
| `determinism` | Does the same seed always give the same result? | an unseeded generator, set or dict iteration order, wall-clock time |
| `update_order` | Does the conclusion survive a different activation order? | stale or partial state reads, buffers reused between steps |
| `agent_order` | Does it survive relabelling the agents? | `agents[:k]`, ties broken by id |
| `rng_streams` | Does it survive fresh, independent random streams? | a hard-coded seed, streams shared between processes |
| `grid` | Does it survive moving or rotating the world on the torus? | a boundary that does not wrap, absolute array positions |
| `time_step` | Does it survive halving the time step? | probabilities not converted with the current step |
| `population_scale` | Does it survive doubling the population at fixed density? | a size or count fixed at the calibration value |

A check needs the model to honour its knob. When a model does not, the check is reported as
`NOT_TESTABLE`, never skipped silently. The `rng_streams` check always runs: when a model does not manage
its own streams, ABMfact remaps the seed.

## Verdicts

| Verdict | Meaning |
|---|---|
| `ROBUST` | the claim holds under every variant, and every result is statistically equivalent to baseline |
| `SHIFTED` | the claim still holds, but a result moved beyond its tolerance, or its spread doubled or halved |
| `FRAGILE` | the claim fails under a variant, or the same seed gave a different result |
| `INCONCLUSIVE` | the intervals are too wide to decide; the report says how many seeds would settle it |
| `BASELINE_FAILS` | the claim does not hold even before anything is changed |
| `NOT_TESTABLE`, `SKIPPED` | the model does not expose the knob, or the claim declares the check out of scope, with a reason |

Decisions use one-sided tests at `alpha` (0.05 by default, so 90% intervals) and a three-outcome
equivalence test against a tolerance, 10% of the baseline mean unless the claim sets one. See
[docs/statistics.md](docs/statistics.md).

## Validation: the planted-fault zoo

A checker is only as good as its evidence that it catches what it claims to catch, and nothing else.
`abmfact.zoo` holds one clean SIR model on a torus and seven copies of it, each with one realistic bug.
`abmfact demo` audits all eight with every check. The test suite requires this exact matrix:

```
                determinism  update order  agent order  rng streams  grid    time step  population scale
clean           .            .             .            .            .       .          .
nondeterminism  CAUGHT       .             .            .            .       .          .
stale_state     .            CAUGHT        .            .            .       .          .
id_dependence   .            .             CAUGHT       .            .       .          .
ignored_seed    .            .             .            CAUGHT       .       .          .
boundary        .            .             .            .            CAUGHT  .          .
dt_unscaled     .            .             .            .            .       CAUGHT     .
density         .            .             .            .            .       .          CAUGHT
```

Each fault is caught by its own check and by no other, and the clean model is robust everywhere. The
bugs are described in [docs/zoo.md](docs/zoo.md) and marked `# FAULT` in the source.

## Adapters

| Adapter | For | Notes |
|---|---|---|
| `python` | any `run(params, seed, knobs)` callable | the fastest path |
| `mesa` | Mesa 3 model classes | knobs map to constructor keywords; `pip install "abmfact[mesa]"` |
| `netlogo` | `.nlogo` models through pynetlogo | knobs map to globals; needs a local NetLogo |
| `command` | anything else: R, Julia, Java, C++ | reads JSON on stdin, prints JSON on stdout |

Working examples for each live in [examples/](examples). See [docs/adapters.md](docs/adapters.md).

## Limits

ABMfact checks implementation choices that should not matter. It does not check whether the model is a
good description of the world, and it cannot find a bug that changes no stated claim. Its power depends
on the seeds you give it: an `INCONCLUSIVE` verdict is an honest answer, not a pass. Many comparisons are
made in one audit, so treat a single marginal `SHIFTED` as a lead to investigate, not a conclusion.

## Citing

If ABMfact helps your work, please cite it using [CITATION.cff](CITATION.cff) (GitHub shows a "Cite this
repository" button). The idea it implements comes from:

> Galán, J. M., Izquierdo, L. R., Izquierdo, S. S., Santos, J. I., del Olmo, R., López-Paredes, A., &
> Edmonds, B. (2009). Errors and artefacts in agent-based modelling. *Journal of Artificial Societies and
> Social Simulation*, 12(1), 1. https://www.jasss.org/12/1/1.html

## Licence

Apache-2.0. See [LICENSE](LICENSE).
