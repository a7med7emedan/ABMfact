# Gate 3 protocol (fixed before any run)

Gate 3 asks whether the four engines that can run ABM-IR models give the same results for the six reference
problems, and whether the two new engines are deterministic and use their seeds on their own published examples.
This protocol, the six comparison files and the two audit files were committed before any Gate 3 run. Nothing in
them is changed after seeing results; any deviation is reported in `RESULTS.md` with its reason.

**What was seen before this was written.** Single runs with seed 0 of each problem in each engine were reported
at Stop B (`ABMfact-brief/STOP_B.md`). The budgets below come from quantities in the models, not from those runs,
and the claims were frozen earlier (commit 032fb83). Seeds 1000 and above were used for timing only.

## Engines

| Engine | Route | Tier |
|---|---|---|
| reference | reads ABM-IR | T0, the baseline |
| physicell | PhysiCell 1.14.2, translated | T1 |
| compucell3d | CompuCell3D 4.10.0, translated, 2 micron per site and 1 minute per MCS | T1 |
| morpheus | Morpheus 2.4.1, translated, 2 micron per node and 1 minute per step | T1 |

Every translation's fidelity report is kept beside its verdicts.

## Comparisons

`abmfact compare <problem>.compare.yaml` runs each problem in each engine with seeds 0 to 19 and compares each
metric with the reference engine: a Welch interval for the difference of means (alpha 0.05 each side) against the
metric's budget. AGREE means the whole interval lies within the budget, DISAGREE wholly outside, INCONCLUSIVE
neither (reported with the seeds that would settle it). The CompuCell3D spheroid runs 10 seeds (0 to 9), because
each run takes about 2 hours under amd64 emulation; that is the only change to the seed count.

| Problem | Metric | Budget | From |
|---|---|---|---|
| Monolayer (1 day) | `last(cells)` | 25% of the reference mean | counts are compared relative to their size |
| Spheroid | `last(oxygen_min)` | 0.94 mmHg | a quarter of the necrosis rule's half-max, 3.75 |
| | `max(dead)` | 25% of the reference mean | |
| Differential adhesion | `last(dark_spread)`, `last(light_spread)` | 33.6 micron | two cell diameters (a 2494 cubic micron sphere) |
| Chemotaxis | `last(mean_x)` | 100 micron | a tenth of the 1000 micron domain |
| Drug pulse | `last(live)` | 25% of the reference mean | |
| | `last(drug_mean)` | 0.05 | the suite's clearance threshold, 5% of the pulse |
| Immune attack | `last(tumour_live)`, `max(tumour_dead)` | 25% of the reference mean | |

The suite's threshold claims (M1, P1, P2, S2, X1, D2, I2) are also evaluated in each engine at the model's own
parameters, to see whether every engine draws the same conclusion. The monotone claims need parameter sweeps
and are not part of Gate 3.

## Native examples

`abmfact run native/<engine>_cellsorting.audit.yaml` audits each new engine on its own cell-sorting example, run
unchanged: CompuCell3D's book-chapter demo (10,000 MCS) and Morpheus's CellSorting_2D (Graner and Glazier, 1992).
Each has one claim from the model's geometry: the more cohesive type ends more compact than the starting disc,
whose radius of gyration is R/√2. The determinism and rng_streams checks run; the other checks are NOT_TESTABLE for
these engines, with the reason.

## What counts as done

Each engine either agrees with the reference within the budget, or the disagreement is traced to a named cause:
a translation approximation listed in the fidelity report, a difference between modelling paradigms, or a bug,
fixed in the right place and reported. Disagreements are reported, never tuned away.

## Budget and running

`run_gate3.sh` runs everything with 10 runs at a time on this machine (12 CPUs; CompuCell3D and Morpheus in amd64
containers under emulation). Expected time: about 4 hours, within the 8 hours allowed. Runs are kept in a run
store outside the repository, so an interrupted study resumes without rerunning finished runs. The study stops and
reports if free disk space falls below 20 GB.
