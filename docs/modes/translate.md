# T2 translation by calibration

A structural (T1) translation into a cellular Potts engine maps every ABM-IR concept it can, but two mappings have
no exact counterpart:

- **Pressure.** Cellular Potts has none, so the signal is the fraction of a cell's boundary that touches other
  cells.
- **Motility.** A cell's speed results from a driving strength working against the volume and contact
  constraints.

Gate 3 traced six of its seven disagreements to these two mappings. `abmfact translate` calibrates them: it fits
the engine options `pressure_gain` (a scale on the contact fraction) and `motility_gain` (a scale on the motility
strength) so that chosen observables of the translated model match the source model's ensemble. The result is
tier T2.

```bash
abmfact translate examples/ir/monolayer.yaml --to morpheus --calibrate-on cells --hold-out spread \
    --free pressure_gain=0.1:10 --seeds 10 --source-seeds 20 --jobs 10 --store runs/ --out results/t2
```

or from a file (`examples/modes/translate.yaml`):

```yaml
model: ../ir/monolayer.yaml
to: morpheus
source: {engine: reference, seeds: 20, seed_start: 500}
calibrate_on: [cells]
hold_out: [spread]
free: [{name: pressure_gain, low: 0.1, high: 10}]
params: {experiment.max_time: 720}
seeds_per_evaluation: 8
seed_start: 500
bayesopt: {n_init: 6, n_iter: 10}
```

## What it refuses

- **Anything but a mapping gain.** Biological parameters such as rates, half-max values or adhesion belong to the
  model, not its translation, and are never varied.
- **A fit with nothing held out.** At least one observable must be held out, and no held-out observable may also
  be calibrated on. A T2 translation therefore cannot match, by construction, what is later used to judge it.

The held-out observables are reported at the calibrated gains: each one's end value in the source and the target,
and their difference with its interval. They are reported, not judged.

## Outputs

`project/` (the model exported at the calibrated gains), `translation.json` (tier T2, the gains with their
intervals, the source ensemble, the calibration distance and the held-out check), `fidelity.md` (the T1 report with
each calibration noted on the entries it governs) and the calibration's own files under `calibration/`.

A comparison file can take the gains from it, so a frozen comparison never has to be edited:

```yaml
options_from:
  morpheus: ../t2/translation.json
```

## Limits

A gain fitted on one model and set of observables is a mapping between paradigms, and whether it carries over to
other models is an empirical question. The [Gate 4 study](https://github.com/a7med7emedan/ABMfact/tree/main/studies/gate4)
fits each gain once, on its own calibration model, and judges all six reference problems on fresh seeds.
