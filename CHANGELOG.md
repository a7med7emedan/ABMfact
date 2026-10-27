# Changelog

All notable changes are recorded here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project uses
[semantic versioning](https://semver.org/).

## [Unreleased]

### Added

- `abmfact compare`: runs one ABM-IR model in several engines over the same seeds and compares each metric with
  a baseline engine against a declared disagreement budget (AGREE, DISAGREE, INCONCLUSIVE with the seeds
  needed, NOT_RUN), with each engine's fidelity report and, for threshold claims, whether every engine draws
  the same conclusion. Runs can be kept in a run store and reused.
- Translation between ABM-IR and Morpheus (tier T1) with a fidelity report, in stated units of micron per
  node and minutes per step: one MorpheusML file with cell states, Hill-type rules as Morpheus expressions,
  division, death, motility, fields with secretion and uptake, attack and timed boundary values. MorpheusML
  models import to ABM-IR. Chemotaxis follows the gradient's direction only, as in ABM-IR, through
  `DirectedMotion`. In both cellular Potts translations, a field whose diffusion is capped for speed has its
  secretion, uptake and decay slowed by the same factor, which keeps its steady profile.
- The Morpheus engine: Morpheus 2.4.1 models run as they are, with two added loggers for cells and fields,
  natively with `MORPHEUS_BIN` or in the pinned image in `containers/morpheus/Dockerfile`.
- Translation between ABM-IR and CompuCell3D (tier T1) with a fidelity report, in stated units of micron per
  site and minutes per Monte Carlo step: a CC3DML model plus a generic steppable that runs placements, the
  cycle, death, rules, motility, secretion, attack and timed events. CompuCell3D projects import to ABM-IR.
- The CompuCell3D engine: CompuCell3D 4.10.0 projects run as they are, with a driver that adds one output
  steppable, natively with `CC3D_PYTHON` or in the pinned image in `containers/compucell3d/Dockerfile`.
- The Gate 3 study in `studies/gate3/`: the six reference problems compared across the reference engine,
  PhysiCell, CompuCell3D and Morpheus with 20 seeds each, against budgets taken from the models, and each new
  engine audited on its own published cell-sorting example. The protocol, comparisons and budgets were
  committed before any run.
- Six reference problems in `examples/suite/`: monolayer, spheroid, differential adhesion, chemotaxis, drug
  pulse and immune attack, each an ABM-IR model with two qualitative claims committed before any audit.
- Attack and damage in the reference engine, following PhysiCell 1.14.2: effectors attack live neighbours,
  damage accumulates and is repaired, and rules read it as the `damage` signal. ABM-IR gains
  `attack_duration`, `immunogenicities` and a cell type's `integrity` (damage and repair rates), and the
  PhysiCell translation carries them both ways.
- `abmfact recommend`: ranks every engine for one model by whether it can run it, how faithfully (a dry-run
  translation's fidelity counts), which audit checks become testable and whether it is installed. Engines
  gain an optional `translation()` method for this.
- ABM-IR 0.1: a versioned, engine-neutral model description (space, substrates, cell types, Hill-type
  rules, intracellular model references, initial conditions, timed events, observables, experiment
  settings, provenance and native extensions), with a JSON Schema, validation errors that name the place
  in the document, parameter changes by path, and a model hash. Three example models.
- The engine contract (`capabilities`, `prepare`, `run`, `collect`), a registry on the `abmfact.engines`
  entry point group, and `abmfact engines`. The v0.1 adapters run through it unchanged.
- Run records, a common output schema (cells, fields, observables) and a content-addressed run store,
  with Parquet, Zarr and DuckDB under the `[store]` extra.
- Runtime backends: local, container (Docker, Podman, Apptainer, with the image digest recorded) and
  Slurm job arrays.
- The reference engine: a small 2D NumPy simulator with PhysiCell's mechanics and rule semantics, an
  unconditionally stable diffusion solver, and every audit knob.
- The PhysiCell and PhysiBoSS engine: native runs of 1.14.2 projects, builds with a local GNU g++ or the
  pinned image in `containers/physicell/Dockerfile`, output reading with pcdl or a SciPy reader, and
  translation to and from ABM-IR with fidelity reports. A warning when `dt_diffusion` exceeds
  `dt_mechanics`, which makes PhysiCell move cells too slowly.
- Convergence under time-step and voxel refinement with the observed order and Roache's GCI, and
  `abmfact converge`.
- Audit files can name an engine and a model (`adapter: engine`), and gain an optional `convergence:`
  section.
- Commands: `run-model`, `import`, `export`, `converge` and `engines`.
- The planted-fault zoo gains a field model and the `unstable_diffusion` fault, caught only by
  convergence; the acceptance test asserts the extended matrix with its one documented overlap.
- The `dx_scale` knob (voxel size).
- A warning on a claim and its verdicts when the default 10% tolerance meets a near-zero baseline mean
  (below the standard error of the mean, or a mean below 0.01), suggesting an absolute tolerance.
- The manual: Sphinx HTML and a PDF typeset with LaTeX, from the Markdown pages in `docs/`, with getting
  started, recipes, a developer guide for engine plug-ins, a citation page, a command-line reference
  generated from the parser, and an API reference generated from the docstrings. Its examples run as
  doctests. CI builds the HTML with warnings as errors, the PDF and the doctests, and a test requires a
  docstring on every public module, class and function. A `[docs]` extra holds the build tools.

### Changed

- A new logo for the platform, drawn by `scripts/make_logo.py`: agents on a lattice, off-lattice cells and
  cellular Potts cells under one lens. The README, manual front page, package description and citation
  now describe ABMfact as one interface to several simulators, with a table of supported tools.
- Version 0.2.0.dev0. pydantic is now a core dependency (ABM-IR only; `import abmfact` does not load it).
- `abmfact demo` adds the convergence column (`--no-convergence` for the seven audit checks alone).

### Fixed

- PhysiCell exports lower `dt_diffusion` until `dt_diffusion·D/dx²` is at most 0.25 for the fastest field, and
  report it as approximated. BioFVM's split diffusion solver is far from converged above that: at 25, in the
  Gate 3 spheroid, PhysiCell's oxygen fell far below the converged field and six times as many cells died.
- CompuCell3D exports take the exact fraction of a step for secretion and uptake, 1 − exp(−rate), and their
  rules read the field before the step's uptake. Both errors had lowered the oxygen a spheroid cell senses, and
  about two and a half times as many cells died as in the reference.
- CompuCell3D rules read a field averaged over the step's own secretion and uptake. Read before uptake, a
  spheroid cell saw the top of a 1.25-fold swing each step, and only half as many cells died by 12 hours as in
  the reference; the necrosis rule's Hill power of 8 amplifies the swing.

- Seed 0 no longer reaches CompuCell3D, which reads it as a request for a clock seed and so never repeats a
  run; it is mapped to 2147483647, the largest seed CompuCell3D accepts.
- The CompuCell3D and Morpheus translations dilute secretion and uptake over the voxel as ABM-IR does (a cell
  acts at its rate times its volume over the voxel's, with a 20 micron voxel depth in 2D). They had applied
  the rate on every lattice site a cell covers, which made the spheroid's oxygen uptake 1.8 times too strong.
- Morpheus fidelity reports list the pressure rule as approximated, as CompuCell3D's do.
- Translated Morpheus runs are keyed by the model file Morpheus reads, not by the ABM-IR model, so a changed
  translation is never served from the run store. CompuCell3D's run key leaves out the fidelity notes
  written beside a project, so an unchanged project keeps its stored runs.

- Determinism reruns always execute again: they bypass the run store and any cache that honours
  `abmfact.records.cache`, so a cached first run can no longer make a nondeterministic model look
  deterministic.
- Model hashes, PhysiCell build keys and import provenance read text files with LF line endings, so the
  same model has the same identity on a Windows (CRLF) checkout.

## [0.1.0] - 2026-10-18

### Added

- Audit files in YAML: model, parameters, claims and budget.
- Claims with threshold, range and monotone tests, and a safe metric grammar.
- Seven checks: determinism, update order, agent order, random streams, grid placement, time step and
  population scale.
- Verdicts ROBUST, SHIFTED, FRAGILE, INCONCLUSIVE, BASELINE_FAILS, NOT_TESTABLE and SKIPPED, with
  reasons, seeds needed and reproducer commands.
- Diagnosis of the likely cause from the pattern of flagged checks.
- Python, Mesa, NetLogo and command adapters.
- Outputs: results.json, a self-contained report.html and an RO-Crate 1.1 record.
- The planted-fault zoo and an acceptance test requiring a diagonal detection matrix.
- Command line: run, check, demo, init and replay.
