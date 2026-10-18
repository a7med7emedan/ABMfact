# The checks

| Check | Variants | Needs |
|---|---|---|
| `determinism` | reruns the first `determinism_reruns` seeds (default 8) and compares outputs exactly | nothing |
| `update_order` | every declared order other than the default | `update_order` with at least two values |
| `agent_order` | `agent_order=shuffled` with a permutation seed derived from each run's seed | `agent_order` |
| `rng_streams` | `rng_streams=independent` | nothing (seeds are remapped when the model does not manage streams) |
| `grid` | offsets `(0.5, 0.5)` and `(0.25, 0)`, rotation `90` | `grid` |
| `time_step` | `dt_scale=0.5` | `dt_scale` |
| `population_scale` | `pop_scale=2` | `pop_scale` |

Run a subset with `budget.checks: [determinism, update_order]`.

## Reading a failure

ABMfact maps the pattern of flagged checks to the most likely cause:

- **determinism**: hidden nondeterminism. Look for the global `random` or `numpy.random` module, iteration
  over sets or dicts keyed by objects, wall-clock time, threads or parallel reductions.
- **update_order** without **agent_order**: stale or partial state reads. A synchronous update must read
  only last step's state and write every agent's new state. Buffers reused between steps are a classic
  source.
- **update_order** with **agent_order**: ties broken by list position, or resources handed out in
  iteration order.
- **agent_order**: `agents[:k]`, id-based tie-breaking, sampling by walking a list in creation order.
- **rng_streams**: a generator built from a constant, or one stream shared by processes that should be
  independent. Derive one generator per process from the replicate seed, for example with
  `numpy.random.SeedSequence(seed).spawn(k)`. A model that is deterministic by design should skip this
  check.
- **grid**: a boundary that does not wrap, wrong neighbour offsets, or array positions used where world
  positions are meant.
- **time_step**: a per-step probability not converted as `1 - exp(-rate * dt)` with the current `dt`,
  or durations counted in steps.
- **population_scale**: a size, count or normaliser fixed at the calibration value, or density
  dependence where frequency dependence was meant. Spread is not compared in this check, because it
  legitimately shrinks with system size.
