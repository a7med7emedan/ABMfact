# The reference engine

`engine: reference` runs ABM-IR models directly in a small 2D NumPy and SciPy simulator. It is a
baseline: small enough to read, deterministic per seed, and honouring every audit knob, so every check
applies to it. It is not meant to compete with PhysiCell.

## What it simulates

| Process | Implementation |
|---|---|
| Mechanics | overdamped centre-based motion with PhysiCell's repulsion and adhesion potentials and simple pressure; positions by second-order Adams-Bashforth (`core/PhysiCell_cell.cpp`, PhysiCell 1.14.2) |
| Cell cycle | the `live` model: division at the cycle-entry rate, daughters half a radius either side of the mother |
| Death | apoptosis and necrosis rates, removal after the apoptotic or necrotic duration |
| Motility | persistent random walk with bias; chemotaxis up or down a substrate gradient; directions normalised as BioFVM normalises them |
| Substrates | diffusion, decay, secretion and uptake on a regular grid; no-flux, Dirichlet or periodic edges |
| Rules | Hill-type signal-to-behaviour rules, combined exactly as PhysiCell 1.14.2 combines them |
| Events | set a substrate, set a substrate's boundary value for a while (a drug pulse), add cells |
| Attack and damage | effectors start attacks on live neighbours at `attack_rate * immunogenicity * dt`, add `damage_rate * dt` of damage per mechanics step, and stop with probability `dt / attack_duration` or when the target dies; damage grows and is repaired per phenotype step, and kills through rules that read `damage` (PhysiCell 1.14.2 `standard_cell_cell_interactions`, `Cell::attack_cell`, `Cell_Integrity::advance_damage`) |

The diffusion solver is a symmetrised backward-Euler LOD scheme with decay and uptake inside the
implicit sweeps. It is unconditionally stable, keeps concentrations non-negative, and is first order in
time and second order in space. Each step averages the x-then-y and y-then-x sweep orders, so a 90
degree rotation of the world gives the same result. The module docstring of
`abmfact.reference.diffusion` states the scheme and its stability in full; tests check the spatial order
against the heat kernel, the first-order steady-state error in `dt`, mass conservation and positivity.

Rules follow `parse_csv_rule_v3`, `set_behavior_min_value`, `set_behavior_max_value` and
`Hypothesis_Rule::evaluate` in `core/PhysiCell_rules.cpp`: up-regulating signals combine as
`U = S/(1+S)` with `S = sum (s/h)^n`, the behaviour is `b0 + (bM - b0) U` moved towards the minimum by
the down-regulating response, and a test checks a hand-computed case.

## Knobs

| Knob | Honoured as |
|---|---|
| `update_order` | `random`, `id`, `reverse`, `synchronous`: which cell receives which random number |
| `agent_order` | creation order of the initial cells |
| `rng_streams` | the random streams are salted |
| `grid_offset`, `grid_rotation` | the world moves by whole voxels, or rotates in 90 degree steps; needs a periodic domain, and rotation a square one |
| `dt_scale` | all three time steps |
| `dx_scale` | the voxel size |
| `pop_scale` | domain area and initial cell counts, at fixed density |

With offsets snapped to whole voxels and rotations in 90 degree steps, a moved world gives the same
fields and positions as the original to rounding, and a test checks this to 1e-9.

## What it refuses

The engine refuses, with the reason, models it cannot run faithfully: 3D models, intracellular models,
cycle models other than `live`, transformations, rules with signals or behaviours it does not implement,
timed parameter changes, native extensions, and cells that phagocytose or fuse.

Attacks differ from PhysiCell in two declared ways. PhysiCell links an effector to its target by a spring
for the length of an attack; the reference engine does not. PhysiCell tries neighbours in the order its
cell container holds them; the reference engine tries them in a random order drawn from the run's seed,
so the result does not depend on how cells are labelled.

## Examples

| File | Shows |
|---|---|
| `examples/ir/monolayer.yaml` | contact inhibition on a periodic domain |
| `examples/ir/spheroid.yaml` | an oxygen field with rules for division and necrosis; an illustrative 2D teaching example, not a calibrated model (oxygen uptake is raised so a 2D cross-section forms the hypoxic core a 3D spheroid would) |
| `examples/ir/chemotaxis.yaml` | chemotaxis up a gradient held at one edge |
| `examples/ir/audit_monolayer.yaml` | an audit of the monolayer through the engine adapter |

```bash
abmfact run-model examples/ir/monolayer.yaml --engine reference --out results/monolayer
abmfact run examples/ir/audit_monolayer.yaml --out results/monolayer-audit
```
