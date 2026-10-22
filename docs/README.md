# ABMfact documentation

Auditing a model's conclusions (v0.1):

- [Concepts](concepts.md): models, knobs, conditions, claims and verdicts.
- [Writing claims](claims.md): the claim format and the metric grammar.
- [The checks](checks.md): what each check changes and what a failure usually means.
- [Adapters](adapters.md): Python, Mesa, NetLogo, any other language, and models run by an engine.
- [Statistics](statistics.md): exactly how every verdict is decided.
- [The planted-fault zoo](zoo.md): the validation behind the checks.

The platform core (0.2):

- [Architecture](architecture.md): layers, the adapter contract, run records, the store and backends.
- [ABM-IR reference](ir-reference.md): every field of the shared model description, generated from the schema.
- [The reference engine](engines/reference.md): the built-in 2D simulator.
- [PhysiCell and PhysiBoSS](engines/physicell.md): native runs, builds, translation and its fidelity table.
- [Convergence](convergence.md): refining the time step and the voxel size.
