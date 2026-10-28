# Gate 3 follow-up protocol (fixed before any run)

The Gate 3 study left six comparisons INCONCLUSIVE. The spheroid one, PhysiCell's `last(oxygen_min)`, has since
been settled by the spheroid reruns. This follow-up asks whether each of the other five agrees with the reference
within its frozen budget, with enough fresh seeds to decide. This protocol, the three comparison files and the run
script were committed before any follow-up run. Nothing in them is changed after seeing results; any deviation is
reported with its reason.

**What was seen before this was written.** The Gate 3 study's results, which are why these five are followed up.
The seed counts below come from them. Approved by Ahmed Hemedan on 28 October 2026, at 90% power.

## The five comparisons

| Problem | Metric | Engine | Study difference (90% interval) | Budget | Study's seeds needed |
|---|---|---|---|---|---|
| Sorting | `last(light_spread)` | compucell3d | −32.3 (−36.3 to −28.3) | ±33.6 | 180 |
| Drug pulse | `last(live)` | morpheus | −26.2 (−32.5 to −20.0) | ±27.5 | 473 |
| Immune | `last(tumour_live)` | physicell | −7.00 (−13.1 to −0.92) | ±9.51 | 118 |
| Immune | `last(tumour_live)` | morpheus | −15.4 (−22.4 to −8.38) | ±9.51 | 29 |
| Immune | `max(tumour_dead)` | compucell3d | −10.5 (−15.5 to −5.55) | ±13.3 | 63 |

Source: the Gate 3 `compare.json` files and the Verdicts section of its `RESULTS.md`.

## What stays fixed

- Models, metrics, budgets and alpha (0.05 on each side, so 90% intervals) are those frozen in cc69375.
- Threshold claims are not evaluated.
- The Gate 3 study's verdicts stay the study of record. This follow-up is reported in its own section.
- Verdicts are reported as found, including INCONCLUSIVE again. There is no second top-up.

## Design

1. **Code.** Everything runs on the commit that adds this protocol, whose engine code is main at 2475869 plus
   `seed_start` (09c8aa2), which changes no run.
   - Morpheus has not changed since the study.
   - PhysiCell's change since the study (6535b96) only touches fields with dt·D/dx² above 0.25. The immune
     model has no field, so its PhysiCell runs are as in the study.
   - CompuCell3D has changed for these problems. Since ea9d837, rules run before attacks within a step, so
     damage is read one step later. Its follow-up therefore tests current code, not the study's.

2. **Fresh seeds, from 2000 upwards.** The study's seeds 0 to 19 have been seen, and pooling them with new
   ones would make this a look-then-extend design. Seeds 1000 and above were used for timing only. Each
   comparison file sets `seed_start: 2000`.

3. **Seeds fixed in advance, at 90% power.** The study's "seeds needed" puts the interval edge on the budget if
   the estimate does not move, which gives about a 50% chance of a decision. Here n is 3.17 times that figure,
   rounded up to 10: about a 90% chance of a decision if the true difference equals the study's estimate. If the
   true difference sits at the budget edge, no n decides it.

4. **Engines.** Only the reference and the engines in question run. In the immune problem PhysiCell, CompuCell3D
   and Morpheus run with their own seed counts. Its other three engine and metric pairs are reported as
   secondary, not as verdicts.

## Seeds and expected run time

Per-run times are the study's, measured with 10 runs at a time.

| Problem | Engine | Seeds (from 2000) | Run time |
|---|---|---|---|
| Immune | reference, physicell | 380 | under 0.2 h |
| Immune | compucell3d | 200 | 0.2 h |
| Immune | morpheus | 100 | under 0.1 h |
| Sorting | reference, compucell3d | 570 | 1.8 h |
| Drug pulse | reference, morpheus | 1500 | 5.6 h |
| **Total** | | | **about 8 h** |

The drug pulse in Morpheus is most of the cost. Its estimate sits 1.3 inside a budget of 27.5, so it may stay
inconclusive.

`run_followup.sh` runs the three comparisons in that order, 10 runs at a time, using the Gate 3 run store
outside the repository. It stops and reports if free disk space falls below 20 GB.

## What counts as done

Each of the five has a verdict at its fixed n. Any DISAGREE gets its cause traced, as in Gate 3: a listed
translation approximation, a difference between modelling paradigms, or a bug.
