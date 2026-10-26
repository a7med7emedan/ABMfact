# The planted-fault zoo

`abmfact.zoo` is the evidence that each check catches what it claims to catch, and nothing else.

## The clean reference model

An SIR epidemic on an `L x L` torus (default 20 x 20), one agent per cell. A random 20% of agents start
immune. Patient zero is a plus of five agents. A susceptible agent's hazard is `beta` per infected von
Neumann neighbour plus `beta_g` times the infected fraction (weak global mixing). Infected agents recover
at rate `gamma`. Probabilities are `1 - exp(-rate * dt)`, every update reads a snapshot of the previous
step, and the random streams come from `SeedSequence(seed).spawn(4)`.

Activation order only decides which random number each agent receives, so all orders give the same
distribution of results. The grid knobs move the whole world, ids included, so translation and rotation
leave every run unchanged.

## The faults

Each fault model differs from the clean model in one place, marked `# FAULT` in
[`src/abmfact/zoo.py`](https://github.com/a7med7emedan/ABMfact/blob/main/src/abmfact/zoo.py).

| Model | Bug | Caught by |
|---|---|---|
| `fault_nondeterminism` | the activation shuffle uses Python's unseeded global `random` module | `determinism` |
| `fault_stale_state` | the synchronous path writes only changed agents into a buffer swapped every step, so unchanged agents read their state from two steps ago | `update_order` |
| `fault_id_dependence` | the immune agents are `agents[:n]` by creation order, not a random sample | `agent_order` |
| `fault_ignored_seed` | the generator is built from a hard-coded seed | `rng_streams` |
| `fault_boundary` | neighbour lookup pads with zeros instead of wrapping, so the torus is a box | `grid` |
| `fault_dt_unscaled` | the infection probability uses the calibration step, not the current one | `time_step` |
| `fault_density` | the immune count is computed from the calibration population size | `population_scale` |

Several faults are invisible at baseline: the model gives exactly the clean model's results until the
relevant knob moves. That is what makes such bugs survive review.

## The field model and a fault for convergence

A second clean model, `field_clean`, has cells dividing at a rate set by a nutrient that diffuses in
from the edges of a square and decays inside. The nutrient is solved with the explicit FTCS scheme, split
into the substeps its stability limit needs, with the edge value imposed on the domain faces (second
order in space). Its claims are that the population at least triples in ten hours (`F1`) and that cells
sense less nutrient than the edges supply (`F2`).

| Model | Bug | Caught by |
|---|---|---|
| `fault_unstable_diffusion` | one explicit diffusion step per time step, whatever the stability limit says | `convergence` |

At the audit's resolution, and when only the time step is halved, one step is within the limit, so the
fault model is identical to the clean one and every audit check passes it. Refining the time step and
the voxel size together doubles `D dt / dx^2` at each level, past the limit of 1/4, and the field blows
up. Only the convergence check refines both, so only it catches the bug.

## The acceptance test

`tests/test_matrix.py` audits every zoo model with 100 seeds, checks convergence, and asserts the exact
expected matrix:

```
                    determinism  update order  agent order  rng streams  grid    time step  population scale  convergence
clean               .            .             .            .            .       .          .                 .
nondeterminism      CAUGHT       .             .            .            .       .          .                 .
stale_state         .            CAUGHT        .            .            .       .          .                 .
id_dependence       .            .             CAUGHT       .            .       .          .                 .
ignored_seed        .            .             .            CAUGHT       .       .          .                 .
boundary            .            .             .            .            CAUGHT  .          .                 .
dt_unscaled         .            .             .            .            .       CAUGHT     .                 CAUGHT
density             .            .             .            .            .       .          CAUGHT            .
field_clean         .            .             .            .            .       .          .                 .
unstable_diffusion  .            .             .            .            .       .          .                 CAUGHT
```

Each fault is flagged (`FRAGILE` or `SHIFTED` by an audit check, `NOT_CONVERGED` by convergence) by its
own check and by no other, with one documented overlap: `dt_unscaled` is also caught by convergence. Both
checks refine the time step, and a rate tied to the calibration step genuinely does not converge, so
this is a true detection by two checks, not a false alarm; `abmfact.zoo.EXPECTED_ALSO` records it. The
clean models are never flagged. `abmfact demo` prints the same matrix (add `--no-convergence` for the
seven audit checks alone).

The claims are in `abmfact.zoo.CLAIMS` and in [examples/zoo/audit.yaml](https://github.com/a7med7emedan/ABMfact/blob/main/examples/zoo/audit.yaml).
Point that file at any `fault_*` model to see a single audit.
