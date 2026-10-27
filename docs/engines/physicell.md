# PhysiCell and PhysiBoSS

`engine: physicell` runs PhysiCell 1.14.2 projects: natively as they are (tier T0), or ABM-IR models
exported onto PhysiCell's template project (tier T1). PhysiBoSS projects (MaBoSS intracellular models)
build and run the same way. ABMfact writes PhysiCell's settings XML itself and does not depend on any
settings library.

## Building PhysiCell

PhysiCell needs a GNU `g++` with OpenMP. The engine tries, in order:

1. **native**: `PHYSICELL_CPP`, then `g++-15` down to `g++-11`, then `g++`, using the first that is GNU
   (not Apple clang) and compiles `-fopenmp`. The PhysiCell sources come from the project itself (a full
   PhysiCell tree) or from `PHYSICELL_SOURCE`.
2. **docker**: the image built from [`containers/physicell/Dockerfile`](https://github.com/a7med7emedan/ABMfact/blob/main/containers/physicell/Dockerfile),
   pinned to PhysiCell 1.14.2 and to a Debian base image digest:

   ```bash
   docker build -t abmfact/physicell:1.14.2 containers/physicell
   ```

`abmfact engines` says which route is available and, if none is, what is missing. A project is built
once per hash of its sources and the toolchain, under `~/.cache/abmfact/physicell` (change it with the
`cache_dir` option). The template's `-march=native -mfpmath=both -m64` flags do not compile on 64-bit
ARM, so builds use portable flags; with that change PhysiCell builds and runs natively under arm64
Docker on Apple silicon, without emulation.

## Native runs (tier T0)

A project is the folder `make <sample>` populates: `main.cpp`, `Makefile`, `custom_modules/` and
`config/`. For each run the engine copies `config/` into a work directory and edits only what a run
controls:

| Setting | Element (PhysiCell 1.14.2) |
|---|---|
| seed | `options/random_seed`, and `user_parameters/random_seed` where a project's custom code reads it |
| time steps | `overall/dt_diffusion`, `dt_mechanics`, `dt_phenotype`, times `dt_scale` |
| voxel size | `domain/dx`, `dy` (and `dz` in 3D), times `dx_scale` |
| threads | `parallel/omp_num_threads`, when the `threads` option is set |
| output | `save/folder`, `save/full_data/interval`; SVG snapshots off (they do not affect dynamics) |
| parameters | any element path, or `user_parameters.<name>` |

```bash
abmfact run-model path/to/project --engine physicell --out results/run \
  --params '{"overall/max_time": 1440}' --option threads=1
```

Outputs are read into the common schema with [pcdl](https://github.com/elmbeech/physicelldataloader)
when `pip install "abmfact[physicell]"` is installed, and with a small SciPy reader of the MultiCellDS
XML and MATLAB files otherwise; the two agree exactly on real PhysiCell output kept as a test fixture.
Native observables default to `cells`, `live`, `dead`, `live_<type>` and `<substrate>_mean`, or pass
ABM-IR observable specs with the `observables` option.

PhysiCell honours `dt_scale`, `dx_scale` and, through seed remapping, `rng_streams`. It is not a torus
and has no activation-order switch, so the grid, update-order and agent-order checks are `NOT_TESTABLE`.
With more than one OpenMP thread, PhysiCell is not deterministic for a fixed seed; run audits with
`threads=1` to separate that from the model.

**A PhysiCell behaviour to know.** PhysiCell advances in diffusion steps and runs mechanics at most once
per diffusion step, and `Cell::update_position` fixes its time step from its first call. When
`dt_diffusion` exceeds `dt_mechanics`, every cell therefore moves too slowly: in the chemotaxis example
with `dt_diffusion = 1` and `dt_mechanics = 0.1`, ten times too slowly. Native runs with that ordering
carry a warning in their run record, and export lowers `dt_diffusion` to `dt_mechanics` and says so.

BioFVM's diffusion solver splits each step by axis and is far from converged when `dt_diffusion·D/dx²` is
large. In the Gate 3 spheroid (D 10⁵ micron²/min, dx 20 micron, `dt_diffusion` 0.1 minute, so 25), a field
with no cells settled at 33.0 at its centre, where the converged value is 36.4, and six times as many
spheroid cells died as in the reference. Export therefore lowers `dt_diffusion` to a whole fraction of
`dt_mechanics` that keeps `dt_diffusion·D/dx²` at or below 0.25 for the fastest field, and reports it.
This includes PhysiCell's usual oxygen setting (0.01 minute at 20 micron, 2.5), which still left a
consuming cell's voxel about 4 below the converged value. Native runs keep their own step.

## Translation (tier T1)

```bash
abmfact import physicell path/to/project -o model.yaml       # also writes model.fidelity.md and .json
abmfact export model.yaml --to physicell -o exported/         # onto PhysiCell's template project
abmfact run-model model.yaml --engine physicell --out results/t1
```

Import reads the settings XML, the rules CSV (CBHG version 3) and the cells CSV. Export writes them onto
PhysiCell's own template project, bundled with ABMfact under PhysiCell's BSD 3-Clause licence, or onto a
project given with `--base`. Running an ABM-IR file in the PhysiCell engine exports it first, once per
model hash.

| ABM-IR | PhysiCell | Import | Export |
|---|---|---|---|
| space | `domain` | exact (walled) | exact; periodic becomes walled (approximated) |
| substrates | `microenvironment_setup/variable` | exact, including per-side Dirichlet values | exact |
| cycle | `cycle` code with rates or durations | exact; fixed-duration phases approximated | exact; custom becomes live (approximated) |
| death | apoptosis and necrosis models | rates and durations; volume changes dropped (approximated) | exact; volume changes use PhysiCell's defaults |
| volume | `volume/total` | total kept; growth dynamics dropped (approximated) | total set (approximated) |
| mechanics | adhesion, repulsion, adhesion distance, affinities | exact | exact |
| motility | speed, persistence, bias, chemotaxis | exact; advanced chemotaxis dropped | exact |
| secretion | per-substrate rates and targets | exact | exact |
| interactions | attack (rate, damage rate, duration), phagocytosis, fusion, transformations | exact; distinct dead-phagocytosis rates approximated | exact; immunogenicity other than 1 dropped, since the 1.14.2 settings file has none |
| integrity | `cell_integrity` damage and repair rates | exact | exact |
| rules | `cell_rules.csv` (CBHG v3) | exact; rules naming unknown things dropped | exact |
| initial cells | `cells.csv` | positions and types; extra columns dropped (approximated) | random placements drawn once with seed 0 (approximated) |
| intracellular | `intracellular[@type="maboss"]` | files and settings; mappings carried as settings (approximated) | MaBoSS files and settings (approximated); SBML dropped |
| events | none | | dropped: PhysiCell has no timed events without custom code |
| time steps | `overall` | exact | exact; `dt_diffusion` lowered to `dt_mechanics` when larger, and until `dt_diffusion·D/dx²` ≤ 0.25 (approximated) |
| custom C++ | `custom_modules` | native extension, never translated | the base project's code |

The translation is structural, not a proof of equivalence: the reference engine and PhysiCell move
cells up the chemotaxis gradient at a similar speed, but docking the two engines within stated
tolerances is milestone 3.

## Tests

Unit tests use configuration files and output copied from PhysiCell 1.14.2 and need no compiler. Tests
marked `physicell` build and run the template project and the exported chemotaxis model; they are
skipped, with the reason, where neither a compiler nor the Docker image is available.
