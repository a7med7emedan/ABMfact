# Gate 2 protocol (fixed before any run)

Gate 2 asks whether ABMfact's checks find real issues in published PhysiCell models. This protocol, the
two audit files and the metric code in `gate2_models.py` were committed before any simulation in the
study was run. Nothing in them is changed after seeing results; any deviation is reported in
`RESULTS.md` with its reason.

## Models

Two sample projects shipped with PhysiCell 1.14.2, both 2D versions of examples in the PhysiCell paper:
Ghaffarizadeh A, Heiland R, Friedman SH, Mumenthaler SM, Macklin P (2018). PhysiCell: An open source
physics-based cell simulator for 3-D multicellular systems. *PLoS Comput Biol* 14(2): e1005991.
https://doi.org/10.1371/journal.pcbi.1005991 (PMID 29474446). The abstract names both examples:
"cancer heterogeneity" and a "cellular cargo delivery" system. They are demonstrations from a methods
paper, not research models; candidate research models for the later audit paper are listed in
`RESULTS.md`.

| Project | What it shows | Shipped settings |
|---|---|---|
| `heterogeneity` | a 2D tumour whose cells carry an oncoprotein level that scales their proliferation; oxygen-driven necrosis | 45 days, 6 threads |
| `biorobots` | worker cells attach to cargo cells and drag them towards director cells, dropping them where the director signal is high | 2 days, 6 threads |

Both run as shipped (tier T0), built in `containers/physicell/Dockerfile`. Only these change: the seed,
the knob under test, the output folder and save interval (outputs at the start and the horizon only),
and SVG snapshots (off). None of these affects the dynamics. Threads stay as shipped (6).

## Claims

Written from the paper and the sample descriptions, before any run:

| Claim | Statement | Metric |
|---|---|---|
| H1 | the tumour grows | `growth`: live cells at the horizon over live cells at the start, greater than 1 |
| H2 | selection raises the mean oncoprotein of live cells | `onco_shift`: mean oncoprotein of live cells at the horizon minus at the start, greater than 0 |
| H3 | a necrotic core forms | `necrotic_fraction`: necrotic cells over all cells at the horizon, greater than 0 |
| B1 | workers bring cargo towards the directors | `delivery`: mean distance from cargo cells to the nearest director at the start minus at the horizon (micron), greater than 0 |
| B2 | more cargo sits near a director at the end | `near_gain`: fraction of cargo cells within 50 micron of a director at the horizon minus at the start, greater than 0 |

## Checks

- **determinism**: 8 seeds rerun with the same seed.
- **rng_streams**: independent streams through seed remapping.
- **time_step**: all three PhysiCell time steps halved.
- **convergence**: three levels, the time steps and the voxel size halved together at each level.

PhysiCell has no torus, no activation-order switch and no population scaling of these projects, so the
other checks are `NOT_TESTABLE`. The audit uses 20 seeds per condition (seeds 0 to 19), alpha 0.05 and
the default 10% tolerances. Verdicts are reported as found, `INCONCLUSIVE` ones with the seeds needed.

**Expected and not an ABMfact defect.** With more than one OpenMP thread PhysiCell is not deterministic
for a fixed seed. If the determinism check fails, the same 8 seeds are rerun twice at 1 thread as a
diagnostic, and both results are reported side by side.

## Horizon rule

The claims are evaluated at the shipped end time, unless one project's projected compute for the whole
protocol exceeds half of the 8-hour budget on this machine (12 CPUs, two 6-thread runs at a time). Then
its horizon is the largest whole number of days that fits, computed from a timing run of one seed
(seed 1000, outside the audit's seeds) at the coarsest and finest convergence levels. The timing run's
outputs are not used for any claim. The chosen horizons and the timing are recorded in `RESULTS.md`.

## After the study

Docker images and work directories are removed. The study stops and reports if free disk space falls
below 20 GB.
