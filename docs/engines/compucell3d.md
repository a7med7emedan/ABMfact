# CompuCell3D

`engine: compucell3d` runs [CompuCell3D](https://compucell3d.org) 4.10.0 projects, cellular Potts models on a
lattice, as they are (tier T0), and ABM-IR models translated into CompuCell3D projects (tier T1), each with a
fidelity report. `abmfact recommend` shows that report before anything runs.

## Running CompuCell3D

The engine tries, in order:

1. **native**: `CC3D_PYTHON`, a Python with the `cc3d` package, for example from
   `conda install -c compucell3d -c conda-forge cc3d=4.10.0`.
2. **docker**: the image built from `containers/compucell3d/Dockerfile`, which pins the base image by digest
   and CompuCell3D by version and build:

   ```bash
   docker build --platform linux/amd64 -t abmfact/compucell3d:4.10.0 containers/compucell3d
   ```

CompuCell3D publishes conda builds for linux-64 but not linux-aarch64, so the image is amd64. On Apple silicon
Docker runs it under emulation, which works and is slower; on Intel and most cloud machines it runs natively.
`abmfact engines` says which route is available here.

## Native runs (tier T0)

A project is a folder with one `.cc3d` file, which names the CC3DML model, the Python main script and the
steppable modules. For each run the engine copies the project into a run folder and edits only what a run
controls in the CC3DML model:

| Setting | Element |
|---|---|
| seed | `Potts/RandomSeed`, added if absent |
| parameters | any element path, such as `Potts/Steps` or `Potts/Temperature` |
| centroids | the `CenterOfMass` plugin, added if absent; it tracks centroids and changes no dynamics |

A small driver then starts CompuCell3D's own runner on the copy with one extra steppable registered. At MCS 0,
every `save_every` steps (a tenth of the run by default) and the last step, it writes each cell's id, type,
centre of mass and volume, and every chemical field. The project's own Python code runs as written; it is
custom code that ABMfact runs but never translates.

```bash
abmfact run-model path/to/project --engine compucell3d --out results/run --params '{"Potts/Steps": 1000}'
```

## Units

CompuCell3D works in lattice sites and Monte Carlo steps (MCS). The common output schema uses micron and
minutes, so the engine takes `site_length` (micron per site) and `mcs_minutes` (minutes per MCS), both 1 by
default. With the defaults the outputs stay in sites and MCS, and every run record says so in a warning.

## Translation (tier T1)

ABM-IR describes centre-based cells in micron and minutes; CompuCell3D moves lattice sites between cells,
step by Monte Carlo step. Every quantity is converted with two stated scales: `site_length` (2 micron per
site by default for exports, so a cell of PhysiCell's default volume covers about 55 sites in 2D) and
`mcs_minutes` (1 minute per MCS). An exported project has a CC3DML model for what the cellular Potts model
does natively, `abmfact_model.json` with the rest of the model in lattice units, and a generic steppable
module that runs it. Projects are exported once per model and translator version, and cached.

| ABM-IR | Exported as | Fidelity |
|---|---|---|
| space | lattice of `size / site_length` sites; periodic edges kept | exact |
| volume | target area (2D: the cross-section of the cell's sphere) in sites, volume stiffness 2 | approximated |
| adhesion and affinities | contact energies `J(a,b) = 20 - 10 e / 0.4` (at least 2), medium 10, with `e` the geometric mean of strength times affinity | approximated |
| repulsion | the volume constraint | approximated |
| substrates | DiffusionSolverFE: coefficients in sites² per MCS, decay, initial and boundary values | exact, unless capped |
| fast diffusion | above 200 sites² per MCS the coefficient is capped, and the field's secretion, uptake and decay are slowed by the same factor, which keeps its steady profile (the decay length √(D/k)) and slows only how fast it settles; the note gives the factor, and a larger `site_length` keeps more of the speed | approximated |
| secretion and uptake | CompuCell3D's field secretor, at the cell's centre value | approximated |
| live cycle, apoptosis, necrosis | the generated steppable; dead cells keep their size until their clearance time, then shrink away | exact cycle, approximated death |
| rules | Hill-type rules combined as in PhysiCell 1.14.2 | exact |
| pressure signal | the fraction of the cell's boundary touching other cells, as cellular Potts has no pressure | approximated |
| motility and chemotaxis | a persistent walk pushed by an external potential of strength 50 x speed; the speed reached depends on temperature and the volume constraint | approximated |
| attack and damage | as in PhysiCell 1.14.2, over lattice neighbours | exact |
| initial placements | each cell on the free sites of a square of its target area, or the nearest free site, then grown to size | approximated |
| timed boundary values (a drug pulse) | held on every edge site while the event lasts | approximated |
| phagocytosis, fusion, transformations, intracellular models, CSV positions | not exported | dropped |

Importing a project reads what its CC3DML declares: the lattice, cell types, target volumes, contact
energies (inverting the mapping above), diffusion fields with their boundary values, and blob or uniform
initialisers. The project's Python code becomes a declared native extension. Plugins without an ABM-IR
counterpart are listed as dropped.

```bash
abmfact run-model examples/ir/immune.yaml --engine compucell3d --out results/immune --option site_length=2
abmfact recommend examples/ir/immune.yaml --details
```

## Knobs

A Monte Carlo step is not a time step, and refining the lattice needs volumes, surfaces and energies rescaled
together, so the engine refuses `dt_scale` and `dx_scale` with the reason, and their checks are
`NOT_TESTABLE`. The seed is remapped for `rng_streams`. CompuCell3D is deterministic for a fixed seed: the same
seed gives byte-identical output, which a test checks.

## Differences from the common schema

CompuCell3D cells have no apoptotic or necrotic state; every cell is reported live, and a cell that
disappears simply leaves the table. Volumes are lattice sites times `site_length` to the power of the
dimension.

## Tests

The unit tests need neither CompuCell3D nor Docker. The tests marked `compucell3d` run two demo projects copied
unchanged from CompuCell3D 4.10.0 (`tests/fixtures/compucell3d`, MIT licence): cell sorting, checked for
determinism, and a diffusing pulse, checked for its field and its conserved total. CI runs them in the image.
