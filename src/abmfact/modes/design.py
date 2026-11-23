"""Parameters and designs for sweeps: full grids, Latin hypercubes and scrambled Sobol sequences.

A parameter is named the way runs take it:

* an ABM-IR dotted path such as ``cell_types[tumour].cycle.rates[0]``;
* a native path with ``/`` for engines that read their own files, such as ``overall/dt_mechanics``;
* ``option:NAME`` for an engine option, such as ``option:pressure_gain``.

Designs are drawn on the unit cube with :mod:`scipy.stats.qmc` and scaled to each parameter's range, linearly
or on a log scale. Sobol points keep their balance only in powers of two, so other sizes give a warning.
"""

from __future__ import annotations

import math
import warnings
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Literal

import numpy as np

METHODS = ("grid", "lhs", "sobol")


class DesignError(ValueError):
    """A parameter or design is not valid."""


class DesignWarning(UserWarning):
    """A design that runs but is weaker than it could be."""


@dataclass(frozen=True)
class Parameter:
    """One varied parameter and its range.

    Attributes:
        name: The parameter as runs take it: an ABM-IR path, a native path with ``/``, or ``option:NAME``.
        low: The lower end of the range.
        high: The upper end of the range.
        scale: ``linear``, or ``log`` for a range that spans orders of magnitude (both ends positive).
        integer: Round values to whole numbers.
    """

    name: str
    low: float
    high: float
    scale: Literal["linear", "log"] = "linear"
    integer: bool = False

    def __post_init__(self) -> None:
        if not self.name:
            raise DesignError("a parameter needs a name")
        if not (math.isfinite(self.low) and math.isfinite(self.high)) or self.low >= self.high:
            raise DesignError(f"{self.name}: low must be below high, got {self.low} and {self.high}")
        if self.scale not in ("linear", "log"):
            raise DesignError(f"{self.name}: scale must be linear or log, got {self.scale!r}")
        if self.scale == "log" and self.low <= 0:
            raise DesignError(f"{self.name}: a log scale needs a positive range")

    @property
    def kind(self) -> str:
        """``option`` for engine options, ``native`` for native paths, else ``ir``."""
        if self.name.startswith("option:"):
            return "option"
        return "native" if "/" in self.name else "ir"

    @property
    def key(self) -> str:
        """The name runs use: the option name for options, else the parameter path."""
        return self.name.removeprefix("option:")

    def scaled(self, u: np.ndarray) -> np.ndarray:
        """Map points ``u`` in [0, 1] onto this parameter's range."""
        u = np.clip(np.asarray(u, dtype=float), 0.0, 1.0)
        if self.scale == "log":
            v = np.exp(math.log(self.low) + u * (math.log(self.high) - math.log(self.low)))
        else:
            v = self.low + u * (self.high - self.low)
        return np.round(v) if self.integer else v

    def unit(self, v: np.ndarray) -> np.ndarray:
        """Map values on this parameter's range back to [0, 1]."""
        v = np.asarray(v, dtype=float)
        if self.scale == "log":
            return (np.log(v) - math.log(self.low)) / (math.log(self.high) - math.log(self.low))
        return (v - self.low) / (self.high - self.low)

    @classmethod
    def from_mapping(cls, raw: Mapping[str, Any]) -> Parameter:
        """A parameter from ``{name, low, high, scale, integer}``."""
        unknown = sorted(set(raw) - {"name", "low", "high", "scale", "integer"})
        if unknown:
            raise DesignError(f"unknown parameter keys {unknown}")
        try:
            return cls(
                str(raw["name"]),
                float(raw["low"]),
                float(raw["high"]),
                raw.get("scale", "linear"),
                bool(raw.get("integer", False)),
            )
        except KeyError as exc:
            raise DesignError(f"a parameter needs name, low and high: {dict(raw)!r}") from exc


def _check(params: Sequence[Parameter]) -> None:
    if not params:
        raise DesignError("a design needs at least one parameter")
    names = [p.name for p in params]
    if len(set(names)) != len(names):
        raise DesignError(f"each parameter may appear once: {names}")


def grid(params: Sequence[Parameter], levels: int | Sequence[int]) -> np.ndarray:
    """Every combination of evenly spaced levels; one level is the middle of the range. Rows are points."""
    _check(params)
    per = [int(levels)] * len(params) if isinstance(levels, int) else [int(x) for x in levels]
    if len(per) != len(params) or min(per) < 1:
        raise DesignError("give one level count of 1 or more per parameter")
    axes = [p.scaled(np.linspace(0, 1, n) if n > 1 else np.array([0.5])) for p, n in zip(params, per, strict=True)]
    mesh = np.meshgrid(*axes, indexing="ij")
    return np.stack([m.ravel() for m in mesh], axis=1)


def latin_hypercube(params: Sequence[Parameter], n: int, seed: int = 0) -> np.ndarray:
    """``n`` points with one point in each of ``n`` equal strata of every parameter."""
    from scipy.stats import qmc

    _check(params)
    if n < 2:
        raise DesignError("a Latin hypercube needs at least 2 points")
    u = qmc.LatinHypercube(d=len(params), rng=np.random.default_rng(seed)).random(n)
    return np.stack([p.scaled(u[:, i]) for i, p in enumerate(params)], axis=1)


def sobol(params: Sequence[Parameter], n: int, seed: int = 0, scramble: bool = True) -> np.ndarray:
    """``n`` points of a (scrambled) Sobol sequence; balanced only when ``n`` is a power of two."""
    from scipy.stats import qmc

    _check(params)
    if n < 2:
        raise DesignError("a Sobol design needs at least 2 points")
    if n & (n - 1):
        warnings.warn(f"Sobol points are balanced only in powers of two; {n} is not one", DesignWarning, stacklevel=2)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", UserWarning)  # scipy repeats the power-of-two warning
        u = qmc.Sobol(d=len(params), scramble=scramble, rng=np.random.default_rng(seed)).random(n)
    return np.stack([p.scaled(u[:, i]) for i, p in enumerate(params)], axis=1)


def make_design(
    params: Sequence[Parameter],
    method: str,
    n: int | None = None,
    levels: int | Sequence[int] | None = None,
    seed: int = 0,
) -> np.ndarray:
    """The points of a ``grid`` (with ``levels``), ``lhs`` or ``sobol`` design (with ``n``)."""
    if method == "grid":
        if levels is None:
            raise DesignError("a grid needs levels")
        return grid(params, levels)
    if method not in METHODS:
        raise DesignError(f"design method must be one of {METHODS}, got {method!r}")
    if n is None:
        raise DesignError(f"a {method} design needs n")
    return latin_hypercube(params, n, seed) if method == "lhs" else sobol(params, n, seed)


def setting(
    params: Sequence[Parameter],
    point: Sequence[float],
    base_params: Mapping[str, Any] | None = None,
    base_options: Mapping[str, Any] | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """The run parameters and engine options for one design point, on top of the fixed ones."""
    run_params, options = dict(base_params or {}), dict(base_options or {})
    for p, v in zip(params, point, strict=True):
        value: Any = int(v) if p.integer else float(v)
        (options if p.kind == "option" else run_params)[p.key] = value
    return run_params, options
