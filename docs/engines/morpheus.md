# Morpheus

`engine: morpheus` runs [Morpheus](https://morpheus.gitlab.io) 2.4.1 models, MorpheusML files combining cellular
Potts cells, ODEs and PDEs, as they are (tier T0). Translation between ABM-IR and Morpheus comes next; until then
the engine refuses ABM-IR models with that reason, and `abmfact recommend` says so.

## Running Morpheus

The engine tries, in order:

1. **native**: `MORPHEUS_BIN`, a `morpheus` executable.
2. **docker**: the image built from `containers/morpheus/Dockerfile`, which pins a Debian base image by digest
   and verifies Morpheus's static Linux binary by its SHA-256:

   ```bash
   docker build --platform linux/amd64 -t abmfact/morpheus:2.4.1 containers/morpheus
   ```

Morpheus publishes its headless simulator as a static x86-64 binary and no arm64 build, so the image is amd64.
On Apple silicon Docker runs it under emulation; on Intel and most cloud machines it runs natively. The
official runner image on Morpheus's registry dates from 2022 and predates 2.4.1, so it is not used.

## Native runs (tier T0)

For each run the engine copies the model into a run folder, sets any element a parameter names, and adds two
loggers to its `Analysis` section, which are Morpheus's own output components and change no dynamics:

| Logger | Writes |
|---|---|
| cells | each cell's id, type, centre and volume, at time 0 and every `save_every` time units (a tenth of the run by default) |
| fields | every global field, one file per save |

Morpheus then runs with the seed, one thread, gnuplot off and the run's output folder on its command line:

```bash
abmfact run-model path/to/model.xml --engine morpheus --out results/run --params '{"Time/StopTime": 1000}'
```

A parameter `Time/StopTime` sets the stop time; any other parameter is an element path whose `value`
attribute is set, such as `Global/Constant[@symbol='d']`.

## Units

Morpheus models carry their own units: lattice nodes, and time in whatever unit the model chose. The common
output schema uses micron and minutes, so the engine takes `node_length` (micron per node) and `time_minutes`
(minutes per model time unit), both 1 by default; with the defaults every run record says the outputs are in the
model's own units.

## Knobs

Refining the time step or the lattice of a cellular Potts model needs other parameters rescaled with it, so the
engine refuses `dt_scale` and `dx_scale` with the reason. Morpheus has one random stream set by the seed, so the
`rng_streams` check remaps the seed. With one thread Morpheus is deterministic for a seed, which a test checks.

## Tests

The unit tests need neither Morpheus nor Docker. The tests marked `morpheus` run two example models copied
unchanged from Morpheus 2.4.1 (`tests/fixtures/morpheus`, BSD-3-Clause): cell sorting, checked for determinism,
and autocrine chemotaxis, checked for its field. CI runs them in the image.
