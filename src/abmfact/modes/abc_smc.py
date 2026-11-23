"""ABC-SMC calibration through pyABC: a weighted posterior sample of the free parameters.

Approximate Bayesian computation with sequential Monte Carlo draws particles from the prior (uniform on each
parameter's range, or log-uniform on a log scale), keeps those whose distance to the target falls below a
shrinking threshold, and reweights them generation by generation. The posterior median is reported as the
calibrated value, with a ``1 - 2*alpha`` credible interval from the weighted quantiles.

Every particle is one evaluation: the model over its seeds, scored with the calibration's distance. That is many
runs, so ABC-SMC suits cheap models; Bayesian optimisation finds a best fit with far fewer.

Toni, T., Welch, D., Strelkowa, N., Ipsen, A. and Stumpf, M. P. H. (2009). Approximate Bayesian computation scheme
for parameter inference and model selection in dynamical systems. *Journal of the Royal Society Interface*, 6(31),
187-202. Schaelte, Y. et al. (2022). pyABC: efficient and robust easy-to-use approximate Bayesian computation.
*Journal of Open Source Software*, 7(74), 4304.
"""

from __future__ import annotations

import logging
import math
import tempfile
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import TYPE_CHECKING, Any

import numpy as np

if TYPE_CHECKING:
    from .calibrate import Problem

SETTINGS = {"population", "generations", "min_epsilon", "max_simulations", "seed"}


def _quiet() -> None:
    """pyABC logs every generation at INFO level; keep only its warnings."""
    for name in ("ABC", "Sampler", "Epsilon", "Distance", "Acceptor", "Transitions", "History", "Redis", "pyabc"):
        logging.getLogger(name).setLevel(logging.WARNING)
    for name in list(logging.root.manager.loggerDict):
        if name.startswith(("ABC", "pyabc")):
            logging.getLogger(name).setLevel(logging.WARNING)


def weighted_quantile(x: np.ndarray, w: np.ndarray, q: float) -> float:
    """The ``q`` quantile of values ``x`` with weights ``w`` (the inverse of the weighted empirical CDF)."""
    xs, ws = np.asarray(x, dtype=float), np.asarray(w, dtype=float)
    order = np.argsort(xs)
    xs, ws = xs[order], ws[order]
    cdf = np.cumsum(ws) / np.sum(ws)
    return float(xs[min(int(np.searchsorted(cdf, q)), xs.size - 1)])


def abc_smc(
    problem: Problem,
    settings: Mapping[str, Any],
    *,
    abcsmc_class: Callable[..., Any] | None = None,
    transition: Any = None,
    method_detail: str = "pyABC ABC-SMC",
) -> dict[str, Any]:
    """Calibrate by ABC-SMC.

    ``settings``: ``population`` (particles per generation, default 50), ``generations`` (at most, default 8),
    ``min_epsilon`` (stop once the threshold is this small, default 0), ``max_simulations`` (stop after this many
    evaluations, default 2000) and ``seed``. ``abcsmc_class`` and ``transition`` replace pyABC's ``ABCSMC`` and its
    multivariate normal kernel, so plug-ins can supply their own.
    """
    from .calibrate import CalibrationError

    try:
        import pyabc
    except ImportError as exc:  # pragma: no cover - depends on the environment
        raise ImportError('ABC-SMC needs pyABC: pip install "abmfact[calibrate]"') from exc
    unknown = sorted(set(settings) - SETTINGS)
    if unknown:
        raise CalibrationError(f"unknown abc_smc settings: {unknown}")
    population, generations = int(settings.get("population", 50)), int(settings.get("generations", 8))
    min_epsilon, max_simulations = float(settings.get("min_epsilon", 0.0)), int(settings.get("max_simulations", 2000))
    seed = int(settings.get("seed", 0))
    if population < 5 or generations < 1 or max_simulations < population:
        raise CalibrationError(
            "abc_smc needs a population of 5 or more, at least one generation, and "
            "max_simulations of at least the population"
        )
    _quiet()
    params = problem.parameters
    names = [f"u{i}" for i in range(len(params))]

    def values(u: Mapping[str, float]) -> list[float]:
        return [float(p.scaled(np.array([u[n]]))[0]) for p, n in zip(params, names, strict=True)]

    def model(par: Mapping[str, float]) -> dict[str, float]:
        d = problem.evaluate(values(par), {"phase": "abc"})
        return {"distance": d if math.isfinite(d) else 1e12}

    def distance(x: Mapping[str, float], x0: Mapping[str, float]) -> float:
        return float(x["distance"])

    prior = pyabc.Distribution(**{n: pyabc.RV("uniform", 0.0, 1.0) for n in names})
    np.random.seed(seed)  # pyABC draws from NumPy's global generator
    cls = abcsmc_class or pyabc.ABCSMC
    abc = cls(
        model,
        prior,
        distance,
        population_size=population,
        sampler=pyabc.sampler.SingleCoreSampler(),
        transitions=transition if transition is not None else pyabc.MultivariateNormalTransition(),
    )
    with tempfile.TemporaryDirectory() as tmp:
        abc.new("sqlite:///" + str(Path(tmp) / "abc.db"), {"distance": 0.0})
        history = abc.run(
            minimum_epsilon=min_epsilon, max_nr_populations=generations, max_total_nr_simulations=max_simulations
        )
        df, w = history.get_distribution(m=0, t=history.max_t)
        pops = history.get_all_populations()
        ess = []
        for t in range(history.max_t + 1):
            _, wt = history.get_distribution(m=0, t=t)
            ess.append(float(pyabc.weighted_statistics.effective_sample_size(wt)))
    alpha = problem.spec.alpha
    posterior: dict[str, Any] = {}
    best: dict[str, float] = {}
    interval: dict[str, list[float]] = {}
    for p, n in zip(params, names, strict=True):
        x = p.scaled(df[n].to_numpy())
        best[p.name] = weighted_quantile(x, w, 0.5)
        interval[p.name] = [weighted_quantile(x, w, alpha), weighted_quantile(x, w, 1 - alpha)]
        posterior[p.name] = {"values": [float(v) for v in x], "weights": [float(v) for v in w]}
    median_distance = problem.evaluate([best[p.name] for p in params], {"phase": "posterior median"})
    generations_run: list[dict[str, Any]] = [
        {
            "generation": int(r["t"]),
            "epsilon": float(r["epsilon"]),
            "evaluations": int(r["samples"]),
            "particles": int(r.get("particles", population)),
            "ess": ess[int(r["t"])] if int(r["t"]) < len(ess) else None,
        }
        for _, r in pops.iterrows()
        if int(r["t"]) >= 0
    ]
    warnings = []
    if ess and ess[-1] < population / 3:
        warnings.append(f"the last generation's effective sample size is {ess[-1]:.0f} of {population} particles")
    final_eps = float(generations_run[-1]["epsilon"]) if generations_run else math.inf
    if final_eps > min_epsilon and len(generations_run) < generations:
        warnings.append(
            f"stopped by the simulation cap ({max_simulations}) before reaching {generations} "
            f"generations or epsilon {min_epsilon:g}"
        )
    for p in params:
        lo, hi = p.unit(np.array(interval[p.name]))
        if hi - lo > 0.9:
            warnings.append(
                f"{p.name}: the credible interval spans {hi - lo:.0%} of the prior range, so the target "
                "barely informs it"
            )
    return {
        "best": best,
        "best_distance": median_distance,
        "best_evaluation": len(problem.evaluations) - 1,
        "interval": interval,
        "interval_meaning": f"{1 - 2 * alpha:.0%} credible interval from the weighted posterior quantiles; the "
        "calibrated value is the posterior median",
        "posterior": posterior,
        "diagnostics": {
            "method": method_detail,
            "evaluations": len(problem.evaluations),
            "population": population,
            "generations": generations_run,
            "final_epsilon": final_eps,
        },
        "warnings": warnings,
    }
