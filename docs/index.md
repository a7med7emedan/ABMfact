# ABMfact

````{only} html
**ABMfact runs agent-based models in several simulators through one interface, and tells you whether
their conclusions are findings or implementation artefacts.**

A model stays in its own format, or is written once in ABM-IR, a shared description that ABMfact
translates for each simulator with a report of what it kept exactly, approximated or dropped. Every run
is recorded with its engine version, settings and seed. On top sit the checks: ABMfact reruns the model
under changes that a correct implementation must not care about, and refines its time step and voxel
size, then reports which of the model's stated conclusions survive.

```{image} ../assets/platform.svg
:class: only-light
:alt: How ABMfact fits together, from a model through translation, an engine, a runtime and the run store to the checks.
```

```{image} ../assets/platform-dark.svg
:class: only-dark
:alt: How ABMfact fits together, from a model through translation, an engine, a runtime and the run store to the checks.
```

Today ABMfact runs PhysiCell and PhysiBoSS projects and its own reference engine natively, and Mesa,
NetLogo, Python and any other program through adapters. CompuCell3D and Morpheus come next; the
[architecture](architecture.md) page lists every engine and its status.

This manual is built from one source as web pages and as a PDF:

- [Getting started](getting-started.md): install, a first audit and a first biological model.
- **User guide:** [concepts](concepts.md), [claims](claims.md), [checks](checks.md),
  [adapters](adapters.md), [statistics](statistics.md) and [convergence](convergence.md).
- **Models and engines:** the [architecture](architecture.md), the [ABM-IR reference](ir-reference.md),
  the [reference engine](engines/reference.md), and [PhysiCell and PhysiBoSS](engines/physicell.md).
- [Recipes](howto.md) for common tasks.
- **Reference:** the [command line](cli-reference.md) and the [Python API](api/index.md).
- **Validation:** [the planted-fault zoo](zoo.md) and [the reference problems](suite.md).
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
suite
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
