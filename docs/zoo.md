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
[`src/abmfact/zoo.py`](../src/abmfact/zoo.py).

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

## The acceptance test

`tests/test_matrix.py` audits every zoo model with 100 seeds and asserts a diagonal matrix: each fault
flagged (`FRAGILE` or `SHIFTED`) by its own check and by no other, and the clean model `ROBUST` (or
`SKIPPED` where the claim declares it) everywhere. `abmfact demo` prints the same matrix.

The claims are in `abmfact.zoo.CLAIMS` and in [examples/zoo/audit.yaml](../examples/zoo/audit.yaml).
Point that file at any `fault_*` model to see a single audit.
