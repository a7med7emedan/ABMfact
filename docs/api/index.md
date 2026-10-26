# API reference

Every public module, class and function, generated from the docstrings. The top-level package
`abmfact` re-exports the names most scripts need: `load_audit`, `run_audit`, `Knobs`, `Metric`, and the
adapters. The subpackages for the platform core are imported only when used, so `import abmfact`
stays light.

```{toctree}
:maxdepth: 1

audit
ir
engines
records
runtime
reference
verify
zoo
```
