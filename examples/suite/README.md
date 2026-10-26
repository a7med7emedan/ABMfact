# Reference problems

Six small 2D problems, one for each kind of behaviour a biological agent-based model commonly needs. Each
is an ABM-IR model in `examples/ir/` and an audit file here with two qualitative claims. The claims were
written and committed before any audit of them was run, and they are not changed to fit results.

| Problem | Model | What it exercises | Claims |
|---|---|---|---|
| Monolayer | `monolayer.yaml` | division, contact inhibition by pressure | the colony more than doubles in a day; weaker inhibition gives a larger colony |
| Spheroid | `spheroid.yaml` | an oxygen field, uptake, necrosis by rule | the core falls below the necrosis rule's half-max; a necrotic core forms |
| Differential adhesion | `sorting.yaml` | adhesion affinities between two types | stronger self-adhesion keeps dark cells compact; light cells disperse past their starting spread |
| Chemotaxis | `chemotaxis.yaml` | a secreted gradient, biased motility | cells move up the gradient; a stronger bias takes them further |
| Drug pulse | `drug_pulse.yaml` | a timed boundary change, death by rule | a stronger pulse leaves fewer cells; the drug clears by the end of the day |
| Immune attack | `immune.yaml` | effector attack, damage, death by rule | more attack leaves fewer tumour cells; attacked cells die |

Every threshold comes from the model itself, never from a run: the starting cell count, the starting
geometry, a rule's half-max, or a decay rate. Each audit file names the reference engine. The same model
and claims can be run in any engine that can run the model; `abmfact recommend examples/ir/<model>.yaml`
says which, and how faithfully. The Gate 3 study (`studies/gate3/`) compares all six across the reference,
PhysiCell, CompuCell3D and Morpheus engines with `abmfact compare`.

```bash
abmfact run examples/suite/immune.audit.yaml --out results/immune
```
