# Morpheus

`engine: morpheus` runs [Morpheus](https://morpheus.gitlab.io) 2.4.1 models, MorpheusML files combining cellular
Potts cells, ODEs and PDEs, as they are (tier T0). ABM-IR models are translated to MorpheusML first (tier T1),
with a fidelity report, and MorpheusML models import to ABM-IR.

## Running Morpheus

The engine tries, in order:

1. **native**: `MORPHEUS_BIN`, a `morpheus` executable.
2. **docker**: the image built from `containers/morpheus/Dockerfile`, which pins a Debian base image by digest
   and verifies Morpheus's static Linux binary by its SHA-256:

   ```bash
   docker build --platform linux/amd64 -t abmfact/morpheus:2.4.1 containers/morpheus
   ```

Morpheus publishes its headless simulator as a static x86-64 binary and no arm64 build, so the image is amd64.
On Apple silicon Docker runs it under emulation; on Intel and most cloud machines it runs natively. The
official runner image on Morpheus's registry dates from 2022 and predates 2.4.1, so it is not used.

## Native runs (tier T0)

For each run the engine copies the model into a run folder, sets any element a parameter names, and adds two
loggers to its `Analysis` section, which are Morpheus's own output components and change no dynamics:

| Logger | Writes |
|---|---|
| cells | each cell's id, type, centre and volume, at time 0 and every `save_every` time units (a tenth of the run by default) |
| fields | every global field, one file per save |

Morpheus then runs with the seed, one thread, gnuplot off and the run's output folder on its command line:

```bash
abmfact run-model path/to/model.xml --engine morpheus --out results/run --params '{"Time/StopTime": 1000}'
```

A parameter `Time/StopTime` sets the stop time; any other parameter is an element path whose `value`
attribute is set, such as `Global/Constant[@symbol='d']`.

## Units

Morpheus models carry their own units: lattice nodes, and time in whatever unit the model chose. The common
output schema uses micron and minutes, so the engine takes `node_length` (micron per node) and `time_minutes`
(minutes per model time unit). Both are 1 by default for native models, and every run record then says the
outputs are in the model's own units. Translated models use 2 micron per node and 1 minute per step.

## Translation (tier T1)

ABM-IR describes centre-based cells in micron and minutes; Morpheus moves lattice nodes between cells, one
Monte Carlo step per time unit. Every quantity is converted with two stated scales, `node_length` (2 micron per
node by default for exports) and `time_minutes` (1 minute per step). Morpheus can say almost all of ABM-IR in
its own language, so an export is one MorpheusML file with no extra code. Each cell keeps its state (live,
apoptotic, necrotic), its damage and its behaviours as cell properties, and the cell logger records the state.
Models are translated once per model and translator version, and cached.

| ABM-IR | Exported as | Fidelity |
|---|---|---|
| space | a square lattice of `size / node_length` nodes, second-order neighbourhood; periodic edges kept | exact |
| volume | `VolumeConstraint` with a target area (2D: the cross-section of the cell's sphere) in nodes, strength 1 | approximated |
| adhesion and affinities | contact energies `J(a,b) = 20 - 10 e / 0.4` (at least 2), medium 10, with `e` the geometric mean of strength times affinity, as for CompuCell3D | approximated |
| substrates | a global `Field` and a `DiffEqn` with diffusion in nodes² per step, decay, and secretion and uptake by the cells covering each node | exact |
| live cycle | `CellDivision` with a per-step probability of rate times step length | exact |
| apoptosis, necrosis | an `Event` that sets the state, then `CellDeath` after the clearance time; dead cells keep their size until then | approximated |
| rules | Hill-type rules combined as in PhysiCell 1.14.2, as one Morpheus expression per behaviour; signals from `Mapper` (the field under the cell) and `NeighborhoodReporter` (contacts) | exact |
| pressure signal | the fraction of the cell's boundary touching other cells | approximated |
| motility | `PersistentMotion` of strength 50 x speed, as CompuCell3D's potential; the speed reached depends on temperature and the volume constraint | approximated |
| chemotaxis | Morpheus `Chemotaxis` answers concentration differences, ABM-IR the gradient's direction only. Its strength is 50 x speed x bias x the domain width in nodes / the field's largest set value, so a gradient of that size pushes as hard as the persistent motion; shallower gradients push less | approximated |
| fast diffusion | above 200 nodes² per step the coefficient is capped, as Morpheus substeps diffusion to stay stable, with the ratio in the note; the reaction step stays below one over the fastest uptake, secretion or decay rate | approximated |
| attack and damage | each touching live effector adds damage at its damage rate times the steady share of time an effector spends attacking, rate x duration / (1 + rate x duration) | approximated |
| initial placements | `InitCircle` and `InitRectangle` with the cell count, placed at random and grown to size | approximated |
| timed boundary values (a drug pulse) | two global events that set and reset the edge value; outside the pulse the edges hold 0, so a no-flux edge also drains the field | approximated |
| phagocytosis, fusion, transformations, intracellular models, CSV positions | not exported | dropped |

Importing a MorpheusML model reads the lattice, cell types, target areas, contact energies (inverting the
mapping above), fields with their diffusion, decay and boundary values, and circle and rectangle
initialisers, evaluating arithmetic on `size.x`, `size.y` and global constants. Rates and speeds are read where
the model gives them as plain numbers, as an export does; any other expression, and every element without an
ABM-IR reading, is listed as dropped.

```bash
abmfact run-model examples/ir/immune.yaml --engine morpheus --out results/immune --option node_length=2
abmfact recommend examples/ir/immune.yaml --details
```

## Knobs

Refining the time step or the lattice of a cellular Potts model needs other parameters rescaled with it, so the
engine refuses `dt_scale` and `dx_scale` with the reason. Morpheus has one random stream set by the seed, so the
`rng_streams` check remaps the seed. With one thread Morpheus is deterministic for a seed, which a test checks.

## Tests

The unit tests need neither Morpheus nor Docker. The tests marked `morpheus` run two example models copied
unchanged from Morpheus 2.4.1 (`tests/fixtures/morpheus`, BSD-3-Clause): cell sorting, checked for determinism,
and autocrine chemotaxis, checked for its field. They also run the translated immune attack problem and check
that tumour cells die. CI runs them in the image.
