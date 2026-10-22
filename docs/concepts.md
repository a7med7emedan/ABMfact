# Concepts

**Model.** Anything that can be called as `run(params, seed, knobs)` and returns a mapping of outputs.
An output is a number or a sequence of numbers. Adapters turn Mesa classes, NetLogo files and external
programs into this shape.

**Knobs.** Implementation choices that the audit varies. A correct implementation of the model's theory
should give the same conclusions whatever their value. A model declares which knobs it honours; the
others make their checks `NOT_TESTABLE`.

| Knob | Values | Meaning |
|---|---|---|
| `update_order` | declared by the model | activation scheme within a step, for example `random`, `id`, `reverse`, `synchronous` |
| `agent_order` | `default`, `shuffled` | create or store agents in a permuted order; `agent_perm_seed` gives the permutation |
| `rng_streams` | `default`, `independent` | derive every random stream independently from the seed |
| `grid_offset` | `(fx, fy)` | translate the whole world on the torus by fractions of its size |
| `grid_rotation` | `0`, `90`, `180`, `270` | rotate the whole world |
| `dt_scale` | for example `0.5` | multiply the time step |
| `pop_scale` | for example `2.0` | multiply population and space together, at fixed density |
| `dx_scale` | for example `0.5` | multiply the voxel size of a model's fields; used by the convergence check |

Only declare an `update_order` value when your theory treats it as equivalent to the default. If the
theory says agents act in random order, a synchronous update is a different model, not an artefact.

**Condition.** A setting of the knobs. The *baseline* has every knob at its default; each check adds
one or more *variants*. Every condition runs the same seeds at the same parameter points.

**Claim.** A conclusion written as a checkable statement about a metric: above or below a threshold,
inside a range, or rising or falling with a parameter. See [claims.md](claims.md).

**Verdict.** One per claim and check: `ROBUST`, `SHIFTED`, `FRAGILE`, `INCONCLUSIVE`, `BASELINE_FAILS`,
`NOT_TESTABLE` or `SKIPPED`. See [statistics.md](statistics.md) for the rules.

**Reproducer.** For every flagged verdict, ABMfact records one seed, the parameters and the knobs, and
prints a command such as:

```bash
abmfact replay audit.yaml --seed 17 --knobs '{"update_order": "synchronous"}'
```

which runs that single model run and prints its outputs.
