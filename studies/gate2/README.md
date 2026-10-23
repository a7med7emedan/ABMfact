# Gate 2 study

Gate 2 asks whether ABMfact's checks find real issues in published PhysiCell models. This folder holds
what was fixed before any run: the protocol (`PROTOCOL.md`), the claims (`heterogeneity.yaml`,
`biorobots.yaml`), the metric code (`gate2_models.py`) and the runner (`run_gate2.py`).

The study has been run. Its results are held for review by the maintainers before they are published,
so they are not in this repository yet.

To run the study yourself, build the PhysiCell image, point the runner at a PhysiCell 1.14.2 source tree
and a scratch folder, and run its phases in order:

```bash
docker build -t abmfact/physicell:1.14.2 containers/physicell
export ABMFACT_GATE2_PHYSICELL=/path/to/PhysiCell-1.14.2 ABMFACT_GATE2_CACHE=/path/to/scratch
python studies/gate2/run_gate2.py timing
python studies/gate2/run_gate2.py audit
python studies/gate2/run_gate2.py convergence
python studies/gate2/run_gate2.py summary
```

The full protocol takes about ten hours on 12 CPUs.
