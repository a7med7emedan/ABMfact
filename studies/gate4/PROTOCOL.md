# Gate 4 protocol (fixed before any run)

Gate 3 left 7 of its 30 comparisons DISAGREE on current code. Six trace to two mappings between centre-based and
cellular Potts models that T1 translation fixes by hand: pressure read as the contact fraction, and motility
strength. Gate 4 asks how many of the 7 become AGREE after T2 translation, which calibrates those two mappings and
nothing else. It uses fresh seeds and the budgets frozen in cc69375.

This protocol, the two calibration models, the four calibration files, the twelve comparison files and the run
script were committed before any Gate 4 run. Nothing in them is changed after seeing results. Any deviation is
reported with its reason.

## The 7 DISAGREEs on current code

| Problem | Metric | Engine | Difference (90% interval) | Budget | Source | Recorded cause |
|---|---|---|---|---|---|---|
| monolayer | `last(cells)` | compucell3d | −56.7 (−60.4 to −53.0) | ±28.3 | study | pressure read as contact fraction |
| monolayer | `last(cells)` | morpheus | −50.9 (−54.7 to −47.0) | ±28.3 | study | pressure read as contact fraction |
| chemotaxis | `last(mean_x)` | compucell3d | −446 (−447 to −445) | ±100 | study | lattice speed below the stated one (motility) |
| chemotaxis | `last(mean_x)` | morpheus | −415 (−418 to −413) | ±100 | study | lattice speed below the stated one (motility) |
| drug pulse | `last(live)` | physicell | +121 (+114 to +128) | ±27.5 | study | the timed event is dropped in translation |
| drug pulse | `last(live)` | compucell3d | −34.7 (−40.7 to −28.7) | ±27.5 | study | slower growth, fits the pressure mapping |
| immune | `last(tumour_live)` | morpheus | −13.2 (−15.4 to −11.0) | ±8.87 | follow-up | pressure halves division (diagnosed) |

Source: the Gate 3 `RESULTS.md` (Verdicts and follow-up sections) on the local branch `gate3-results` (8f4d250).
The other 23 comparisons stand at 22 AGREE and 1 INCONCLUSIVE (CompuCell3D's immune dead count).

## What was seen before this was written

- The Gate 3 study, its spheroid reruns and its follow-up, with every verdict and diagnosis above.
- **Stop A (1 November).**
  - A Morpheus sweep of the monolayer over the contact-inhibition half-max, at gain 1: 4 grid points from 0.25 to
    1.0, 5 seeds each, one day. Mean final cells 43.2, 60.6, 102 and 146.
  - Monolayer ensembles on seeds 0 to 19, reproducing Gate 3's reference and Morpheus means.
- **The pressure-gain tests.** One seed of the one-day monolayer at `pressure_gain` 0.25 and at 4, in CompuCell3D
  and in Morpheus. The lower gain gave the larger colony in both.
- **A T2 demonstration on the monolayer (1 November)**, while building `abmfact translate`:
  - Morpheus, half a day, calibrated on `cells` and with `spread` held out, against 10 reference seeds from 500.
  - It gave `pressure_gain` 0.627 (0.514 to 0.757). The held-out spread difference was −1.34 (−5.52 to +2.83).
  - This is a Gate 3 problem. Gate 4 does not use that gain: its gains come only from the calibration models
    below. It is listed because it was seen.
- **Timing runs of the two calibration models**, seed 9000, gains at 1:

  | Model | Engine | Wall time | End values |
  |---|---|---|---|
  | contact inhibition | reference | 1.2 s | cells 109, spread 68.9 |
  | contact inhibition | morpheus | 1.5 s | cells 74, spread 51.5 |
  | contact inhibition | compucell3d | 11.9 s | cells 57, spread 51.4 |
  | migration | reference | 0.6 s | fast spread 102 to 182, slow spread 96 to 108 |
  | migration | morpheus | 1.1 s | fast spread 120 to 217, slow spread 127 to 131 |
  | migration | compucell3d | 18.1 s | fast spread 107 to 121, slow spread 102 to 100 |

  Morpheus places the migration model's discs wider at t = 0 (120 against 102). A gain cannot change that, and
  it is reported as it falls.

Approved by Ahmed Hemedan on 1 November 2026: each gain gets its own calibration model, so every judged comparison
is held out; T2 varies only the mapping gains, never biological parameters; all 30 comparisons rerun from seed
4000; PhysiCell's drug pulse is structural; PhysiCell against the calibrated lattice engines is reported as its own
line.

## Calibration (T2 translation)

- **What may vary.** Only the mapping gains `pressure_gain` and `motility_gain`, each on 0.1 to 10 on a log scale.
  Rates, half-max values, adhesion and every other biological parameter stay as the models state them.
  `abmfact translate` refuses anything else.
- **Where.** One gain per mechanism per lattice engine, fitted once on its own calibration model and then used in
  every problem. Neither model is one of the six Gate 3 problems, so every judged comparison is held out.

| File | Model | Engine | Fitted on | Held out in the fit |
|---|---|---|---|---|
| `calibration/pressure_compucell3d.translate.yaml` | `calibration/contact_inhibition.yaml` | compucell3d | `cells` | `spread` |
| `calibration/pressure_morpheus.translate.yaml` | same | morpheus | `cells` | `spread` |
| `calibration/motility_compucell3d.translate.yaml` | `calibration/migration.yaml` | compucell3d | `fast_spread` | `slow_spread` |
| `calibration/motility_morpheus.translate.yaml` | same | morpheus | `fast_spread` | `slow_spread` |

- **The models.**
  - The contact-inhibition model is a colony growing along a closed strip six cells wide. Its division rate
    (0.00174 per minute) and inhibition rule (half-max 0.6, Hill power 3) differ from the monolayer's. It has no
    motility.
  - The migration model has sparse cells that neither divide nor die, walking persistently without bias at 2 and
    0.5 micron per minute. It has no pressure rule. Each gain is therefore fitted where the other plays no part.
- **Target.** The reference engine, 20 seeds from 3000, with every observable's mean and standard deviation at
  each save.
- **Method.** Bayesian optimisation: 8 Latin hypercube points, 16 further ones, seed 1, 10 seeds per evaluation
  from 3000. The distance is the mean squared gap between the simulated and target means, each standardised by
  the target's standard deviation. The interval for each gain is bounded by 4 bisection steps per edge, as
  `abmfact calibrate` documents.
- **Recorded, not judged.** Each held-out observable's end value in the reference and the lattice engine at the
  calibrated gain.

## Judging

- **The comparisons.** The six Gate 3 comparison files, copied unchanged except for:
  - `seed_start: 4000`. Seeds 0 to 19 were Gate 3's, 2000 and up the follow-up's, 3000 and up calibration's.
  - `options_from`, which gives CompuCell3D and Morpheus their two calibrated gains.
- **What stays frozen.** Models, metrics, budgets, alpha (0.05 on each side, so 90% intervals), seeds (20;
  CompuCell3D's spheroid 10) and claims are as frozen in cc69375. Relative budgets are a quarter of the fresh
  reference runs' mean, as in Gate 3.
- **Engines.** All four. PhysiCell and the reference engine run as they are.
- **PhysiCell's drug pulse.** It drops its timed event in translation, so T2 does not apply to it. It reruns
  unchanged and counts as not converted. Timed-event support is milestone 5 work.
- **PhysiCell as a second baseline.** The same six comparisons, from the same runs (the run store serves them),
  with PhysiCell as baseline and only the PhysiCell and lattice engines. So the result is not judged only against
  the reference engine the gains were fitted to. Relative budgets then follow PhysiCell's mean.

## Code

Every Gate 4 run uses the commit that adds this protocol on branch `m4-modes`. Its engine code is main at 6afac83
(0.2.0) plus the mapping gains (e4e71a4):
- Morpheus exports at gain 1 are byte-identical to 0.2.0's.
- CompuCell3D's steppable reads the gain, so its run keys differ from Gate 3's.

Fresh seeds mean no Gate 3 run is reused anyway.

## What is reported

1. **The calibrated gains**, each with its interval, distance, evaluations and held-out check.
2. **The headline count:** how many of the 7 DISAGREEs become AGREE. A comparison counts as converted only if it is
   AGREE. INCONCLUSIVE and DISAGREE count as not converted and are reported as found.
3. **Every other verdict.** Any of the other 23 that becomes DISAGREE is reported as a regression, with its
   difference.
4. **PhysiCell against each calibrated lattice engine**, from the second set of comparisons, as its own line.
5. **Claims.** Which threshold claims each engine holds or fails, and whether the engines draw the same
   conclusion.

## What stays fixed after seeing results

- No gain, model, seed, budget, metric or claim is changed after any judging run.
- There is no second calibration and no top-up of INCONCLUSIVE comparisons within Gate 4.
- If a calibration or a run fails, the failure is reported. A rerun of the identical file is allowed only for an
  infrastructure fault (a crashed container or a full disk), and it is reported.

## Expected run time

From Gate 3's wall times at 10 runs at a time, with CompuCell3D and Morpheus under amd64 emulation:
- **Calibration:** about 1 hour. Each fit is about 34 evaluations of 10 seeds, of models that take 1 to 18
  seconds per run.
- **Judging:** about 6 to 8 hours. CompuCell3D's spheroid (10 runs of about 2.6 hours), drug pulse and chemotaxis
  dominate.
- **Second baseline:** minutes, served by the store.

Results go to `results/` on the local branch `gate4-results`, which is never pushed.

## What counts as done

Every row above is reported as found in `RESULTS.md` on `gate4-results`, with the files that support it.
