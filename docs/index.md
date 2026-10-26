# ABMfact

````{only} html
**Is it a finding or an artefact?** ABMfact reruns an agent-based model under changes that a correct
implementation should not care about. These include activation order, agent labels, random streams,
grid placement, time step, voxel size and population size. It then reports which of the model's stated
conclusions survive, with intervals and a command that reproduces each run.

Version 0.2 adds a platform core for biological models: a shared model description (ABM-IR), one
contract for every simulation engine, run records and a store, local, container and Slurm backends, a
small reference engine, a PhysiCell and PhysiBoSS engine, and a convergence check.

This manual is built from one source as web pages and as a PDF:

- [Getting started](getting-started.md): install, a first audit and a first biological model.
- **User guide:** [concepts](concepts.md), [claims](claims.md), [checks](checks.md),
  [adapters](adapters.md), [statistics](statistics.md) and [convergence](convergence.md).
- **Models and engines:** the [architecture](architecture.md), the [ABM-IR reference](ir-reference.md),
  the [reference engine](engines/reference.md), and [PhysiCell and PhysiBoSS](engines/physicell.md).
- [Recipes](howto.md) for common tasks.
- **Reference:** the [command line](cli-reference.md) and the [Python API](api/index.md).
- **Validation:** [the planted-fault zoo](zoo.md).
- **Developer guide:** [writing an engine plug-in](engine-plugins.md) and [contributing](contributing.md).
- [Changelog](changelog.md) and [citation](citation.md).
````

```{toctree}
:hidden:
:caption: Getting started
:maxdepth: 2

getting-started
```

```{toctree}
:hidden:
:caption: User guide
:maxdepth: 2

concepts
claims
checks
adapters
statistics
convergence
```

```{toctree}
:hidden:
:caption: Models and engines
:maxdepth: 2

architecture
ir-reference
engines/reference
engines/physicell
```

```{toctree}
:hidden:
:caption: Recipes
:maxdepth: 2

howto
```

```{toctree}
:hidden:
:caption: Reference
:maxdepth: 2

cli-reference
api/index
```

```{toctree}
:hidden:
:caption: Validation
:maxdepth: 2

zoo
```

```{toctree}
:hidden:
:caption: Developer guide
:maxdepth: 2

engine-plugins
contributing
```

```{toctree}
:hidden:
:caption: About
:maxdepth: 1

changelog
citation
```
