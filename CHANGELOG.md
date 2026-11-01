# Changelog

All notable changes are recorded here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project uses
[semantic versioning](https://semver.org/).

## [Unreleased]

Version 0.3.0.dev0.

### Added

- `abmfact ensemble`: many seeds of one setting in any engine. Every observable is summarised at each save time
  (mean with its 90% interval, standard deviation, 5%, 50% and 95% quantiles), as are metrics such as
  `last(cells)`, with each seed's value kept. Writes `ensemble.json`, `ensemble.csv` and `report.html`, and reuses
  runs through the run store.
- `abmfact sweep`: one model over a design of parameter settings, each with the same seeds. Designs are a full grid,
  a Latin hypercube or a scrambled Sobol sequence (`scipy.stats.qmc`), on linear or log scales, over ABM-IR paths,
  native paths or engine options (`option:NAME`). Writes `sweep.json`, `design.csv`, `runs.csv`, `summary.csv` and
  `report.html`. A sweep run again with the same store reuses every finished run, so an interrupted sweep resumes.
  `--backend slurm` writes a Slurm job array for the runs not yet stored, and `--collect` reads them back.
- `abmfact sensitivity`: Sobol (Saltelli) first-order, total and optional second-order indices, or Morris
  `mu_star`, `mu` and `sigma`, with bootstrap intervals, through SALib (MIT) in the new `[sensitivity]` extra. The
  points run as a sweep, each the mean over its seeds, and the report gives the share of output variance that is
  seed noise. It warns when N is not a power of two or below 512, when an interval is wider than ±0.1, when Morris
  has fewer than 10 trajectories or an odd number of levels, and when seed noise is above 20% of the variance.
- `abmfact calibrate`: fits parameters so a model's observables match a target. The target can be data (a CSV of
  observables over time, with optional standard deviations), an `ensemble.json`, or another engine's ensemble run
  on the fly. The distance is the mean squared standardised gap between the simulated means and the target.
  Bayesian optimisation (bayesian-optimization, MIT, in the new `[calibrate]` extra) finds the best fit. Each
  parameter's interval is bounded by bisection: the values, with the others at the best fit, whose distance the
  seeds cannot tell from the best. Writes `calibration.json`, `evaluations.csv` and `report.html`. Fitted ABM-IR
  values go into `calibrated.yaml`, with a provenance note; fitted engine options go into
  `calibrated_options.json`. Calibration methods can be added through the `abmfact.calibrators` entry point.
- ABC-SMC calibration through pyABC (BSD-3, in `[calibrate]`). It reports a weighted posterior, the posterior
  median with a 90% credible interval, and per-generation epsilon, evaluations and effective sample size. It warns
  on a small last-generation sample, a stop forced by the simulation cap, and intervals that barely narrow the prior.
- Calibration plug-in slots:
  - `uq-physicell` runs ABC-SMC with UQ-PhysiCell's weight and kernel safeguards and its standardised kernel,
    through any engine.
  - `fitmulticell` detects FitMultiCell. Its native-Morpheus calibration is not bridged yet; the slot says so and
    points to `abc-smc`.
  - `abmfact calibrate --methods` lists the methods and plug-ins.
- `abmfact.records.store.read_run` reads a run written by `write_run`.
- CompuCell3D and Morpheus take two mapping gains as engine options, `pressure_gain` (a scale on the contact
  fraction that stands in for pressure) and `motility_gain` (a scale on motility strength). Both are 1 in a
  structural translation (T1); T2 translation calibrates them. The fidelity report names any gain that is not 1.

### Changed

- `abmfact[all]` includes `[sensitivity]` and `[calibrate]`.
- The CompuCell3D steppable reads the pressure gain, so every CompuCell3D run key changes once and stored
  CompuCell3D runs from 0.2.0 are not reused. Morpheus exports at gain 1 are byte-identical to 0.2.0's.
- The run loop that `abmfact compare` used is now shared by every mode (`abmfact.modes.batch`). Comparison results
  are unchanged.

## [0.2.0] - 2026-10-29

### Added

- `abmfact compare`: runs one ABM-IR model in several engines over the same seeds and compares each metric with
  a baseline engine against a declared disagreement budget (AGREE, DISAGREE, INCONCLUSIVE with the seeds
  needed, NOT_RUN), with each engine's fidelity report and, for threshold claims, whether every engine draws
  the same conclusion. Runs can be kept in a run store and reused.
- Comparison files take `seed_start`, the first seed, so a follow-up comparison can draw seeds an earlier one
  has not used.
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
- pydantic is now a core dependency (ABM-IR only; `import abmfact` does not load it).
- `abmfact demo` adds the convergence column (`--no-convergence` for the seven audit checks alone).
- When an engine image is missing, the message gives a `docker build` command that fetches its Dockerfile
  from GitHub at the release tag, so a `pip install` needs no checkout. The engine pages show the same.
- The manual is published on GitHub Pages from main, and the package's Documentation link points there.

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
