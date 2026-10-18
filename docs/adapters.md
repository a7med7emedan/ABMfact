# Adapters

## Python

```yaml
model:
  adapter: python
  target: mypackage.model:run
  supports:
    update_order: {values: [random, id], default: random}
    agent_order: true
    rng_streams: true
    grid: true
    dt_scale: true
    pop_scale: true
```

`target` is `module:attribute`. It may be a callable `run(params, seed, knobs)` or a ready
`abmfact.PythonAdapter`. The audit file's folder is added to the import path. `knobs` is an
`abmfact.Knobs` object; read the fields your model supports and ignore the rest.

## Mesa

```yaml
model:
  adapter: mesa
  target: mesa_sir:SIRModel
  steps: 60
  outputs:
    attack_rate: mesa_sir:attack_rate        # callable(model) -> number
    prevalence: datacollector:Infected       # a model reporter series
  knob_kwargs:                               # knob -> constructor keyword
    update_order: activation
    agent_order: relabel_seed                # receives a permutation seed, or None
  update_orders: [random, id]
```

The seed is passed as `rng=` (change it with `seed_kwarg`). When `dt_scale` is mapped, `steps` is divided
by it. A runnable example is in [examples/mesa_sir](../examples/mesa_sir).

## NetLogo

```yaml
model:
  adapter: netlogo
  path: model.nlogo
  ticks: 200
  reporters:
    attack_rate: "count turtles with [ever-infected?] / count turtles"
    prevalence[]: "count turtles with [infected?] / count turtles"   # [] = collected every tick
  knob_globals:
    update_order: update-order
  update_orders: [random, id]
  netlogo_home: /Applications/NetLogo 6.4.0                          # optional
```

Parameters and knobs are set with `set <global> <value>`, the seed with `random-seed`, then `setup` and
`go` run (rename them with `setup:` and `go:`). One workspace is kept per worker process. Needs
`pip install "abmfact[netlogo]"`, a NetLogo installation and Java. The adapter is tested against a fake
NetLogo link in CI; please report anything that differs with a real installation.

## Command (any language)

```yaml
model:
  adapter: command
  argv: ["Rscript", "model.R"]
  timeout: 600
  supports: {agent_order: true}
```

The program receives `{"params": {...}, "seed": 17, "knobs": {...}}` as JSON on standard input and must
print one JSON object of outputs. A non-zero exit, a timeout or invalid JSON becomes a clear error.
`{python}` in `argv` stands for the interpreter running ABMfact. A runnable example is in
[examples/command](../examples/command).

## Parallel runs

`budget.jobs` (or `--jobs`) runs model runs in worker processes. The adapter is pickled once per
worker. An adapter that cannot be pickled, such as one wrapping a lambda, runs serially. Results are
keyed by condition, parameter point and seed, so parallel and serial audits give identical results.
