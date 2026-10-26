# Citing ABMfact

If ABMfact helps your work, please cite the software. The repository's
[`CITATION.cff`](https://github.com/a7med7emedan/ABMfact/blob/main/CITATION.cff) holds the metadata, and
GitHub turns it into a "Cite this repository" button. Each release will also be archived with a DOI.

```bibtex
@software{hemedan_abmfact,
  author  = {Hemedan, Ahmed},
  title   = {{ABMfact}: run agent-based models across simulators and check their conclusions},
  url     = {https://github.com/a7med7emedan/ABMfact},
  license = {Apache-2.0},
  year    = {2026}
}
```

Please cite the version you used, as printed by `abmfact --version`. An audit's `results.json` and its
RO-Crate record name that version too.

## Work that ABMfact builds on

The distinction between errors and artefacts that the audit tests comes from:

> Galán, J. M., Izquierdo, L. R., Izquierdo, S. S., Santos, J. I., del Olmo, R., López-Paredes, A., and
> Edmonds, B. (2009). Errors and artefacts in agent-based modelling. *Journal of Artificial Societies and
> Social Simulation*, 12(1), 1. <https://www.jasss.org/12/1/1.html>

The grid convergence index used by the convergence check is from:

> Roache, P. J. (1994). Perspective: a method for uniform reporting of grid refinement studies. *Journal of
> Fluids Engineering*, 116(3), 405 to 413.

When you run models in PhysiCell or PhysiBoSS through ABMfact, please also cite those projects as their
documentation asks.

## Licence

ABMfact is released under the Apache License 2.0. The PhysiCell template files bundled with the
PhysiCell engine keep their BSD-3-Clause licence.
