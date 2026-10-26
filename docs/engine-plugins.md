# Writing an engine plug-in

An engine connects ABMfact to one simulator. Once a package registers an engine, it shows up in
`abmfact engines`, `abmfact run-model --engine NAME` runs models in it, and audit files can name it. The
audit, convergence check, run store and backends then work for its models without further code.

## The contract

An engine subclasses {class}`abmfact.engines.base.Engine` and implements three methods:

| Method | Job |
|---|---|
| {meth}`~abmfact.engines.base.Engine.capabilities` | declare what the engine can do, including the knobs it honours |
| {meth}`~abmfact.engines.base.Engine.prepare` | set up one run: a {class}`~abmfact.records.record.RunRequest` that identifies it, and an in-process `call` or a native `argv` |
| {meth}`~abmfact.engines.base.Engine.collect` | turn the finished run into a {class}`~abmfact.records.record.RunRecord` |

The base class supplies the rest:

- `run` submits the prepared run to a backend: local by default, or a container or Slurm backend.
- `run_once` refuses, prepares, runs and collects one run, and uses the run store when one is given.
- `refuse` rejects knobs that the capabilities do not list. Override it to refuse a particular model,
  with a reason.
- `support_for` turns the capabilities into the knobs an audit may vary.
- `seed_for` remaps the seed for the `rng_streams` check when the engine does not manage its own random
  streams.
- `availability` reports a built-in engine. Override it when the engine needs an installed program.
- `load` turns what the user gave (often a file path) into the model object `prepare` takes.
- `translation` reports how faithfully the engine would run a model, without running it. The default says
  the model runs as given. An engine that translates ABM-IR into its own format returns the fidelity report
  of a dry-run translation, and `abmfact recommend` uses it to rank engines.

## A complete example

This engine runs independent random walkers on a line and reports their mean squared displacement. It
honours the time-step knob and nothing else, so an audit reports the other checks as `NOT_TESTABLE`.

```{doctest}
>>> import numpy as np
>>> from abmfact.engines import Capabilities, Engine, PreparedRun
>>> from abmfact.records import RunOutputs, RunRequest
>>>
>>> class WalkEngine(Engine):
...     name = "walk"
...     description = "independent random walkers on a line"
...
...     def capabilities(self):
...         return Capabilities(name=self.name, mechanics="none", dimensions=(1,), knobs=("dt_scale",))
...
...     def prepare(self, model, params, knobs, seed, workdir=None):
...         settings = {**model, **params}
...         request = RunRequest(self.name, model_hash=str(sorted(model.items())), params=dict(params),
...                              seed=int(seed), knobs=knobs.to_dict(), engine_version="walk 1.0")
...
...         def call():
...             rng = np.random.default_rng(self.seed_for(seed, knobs))
...             dt = settings["dt"] * knobs.dt_scale
...             steps = round(settings["duration"] / dt)
...             x = np.cumsum(rng.normal(0.0, np.sqrt(dt), size=(steps, settings["walkers"])), axis=0)
...             return RunOutputs(scalars={"msd": float(np.mean(x[-1] ** 2))})
...
...         return PreparedRun(self.name, request, workdir=workdir, call=call)
...
...     def collect(self, handle):
...         return self.record(handle)
```

`run_once` runs it. For unit diffusion the mean squared displacement equals the duration, here 10:

```{doctest}
>>> engine = WalkEngine()
>>> model = {"walkers": 2000, "duration": 10.0, "dt": 0.1}
>>> record = engine.run_once(model, seed=1)
>>> record.status
'ok'
>>> 9.0 < record.outputs.scalars["msd"] < 11.0
True
```

A knob outside the capabilities is refused with a reason, and an audit uses the same answer:

```{doctest}
>>> from abmfact import Knobs
>>> engine.refuse(model, Knobs(grid_rotation=90))
['walk does not honour the grid_rotation knob']
>>> engine.support_for(model).dt_scale
True
```

## Rules an engine must keep

- **The request decides the result.** Everything that changes the outputs belongs in the
  {class}`~abmfact.records.record.RunRequest`: model hash, parameters, seed, knobs, and the engine version or
  image digest. The run store serves an identical request from disk, so anything left out can make a
  stale result look current.
- **Declare, then refuse.** List only the knobs the engine truly honours, and refuse a model it cannot
  run as written. An audit then reports `NOT_TESTABLE` with the reason, instead of running something
  else.
- **Report failures in the record.** A native run that fails should give a record with
  `status="failed"` and the error, not an exception, so a long audit can finish and report it.
- **Outputs use the common schema.** Fill the cells table, field frames and observables of
  {class}`~abmfact.records.schema.RunOutputs` where the simulator has them, and `scalars` for the values that
  claims test. Observables are computed by {mod}`abmfact.records.observables`, so they mean the same
  thing in every engine.
- **Native runs go through a backend.** Return `argv` and `workdir` rather than starting a process, so
  the same engine runs on this machine, in a container or on a Slurm cluster.

## Registering the engine

A package makes its engine available through the `abmfact.engines` entry point group in its
`pyproject.toml`:

```toml
[project.entry-points."abmfact.engines"]
walk = "walkpackage.engine:WalkEngine"
```

After `pip install`, `abmfact engines` lists it. An installed engine never replaces a built-in engine of
the same name. An audit file then names it. The `model` path, relative to the audit file, goes to the
engine's `load`, and `options` go to its constructor as keyword arguments:

```yaml
model:
  adapter: engine
  engine: walk
  model: walkers.yaml
```

The PhysiCell engine, {class}`abmfact.engines.physicell.engine.PhysiCellEngine`, is a full example of a
native engine: it builds a binary once per source hash, writes a work folder per run, runs it through a
backend, and reads the outputs into the common schema.
