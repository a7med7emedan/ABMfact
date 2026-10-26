"""Claims: a model's conclusions written as checkable statements."""

from __future__ import annotations

import itertools
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

import numpy as np

TESTS = ("greater_than", "less_than", "between", "monotone_increasing", "monotone_decreasing")
FUNCTIONS = ("mean", "sum", "min", "max", "argmax", "argmin", "last", "first", "std")

_NAME = r"[A-Za-z_][A-Za-z0-9_]*"
_RE_PLAIN = re.compile(rf"^\s*({_NAME})\s*$")
_RE_INDEX = re.compile(rf"^\s*({_NAME})\s*\[\s*(-?\d+)\s*\]\s*$")
_RE_FUNC = re.compile(rf"^\s*({_NAME})\s*\(\s*({_NAME})\s*\)\s*$")


class ClaimError(ValueError):
    """A claim or metric expression is not valid."""


@dataclass(frozen=True)
class Metric:
    """A parsed metric expression: ``name``, ``name[i]`` or ``f(name)``."""

    expr: str
    name: str
    func: str | None = None
    index: int | None = None

    @classmethod
    def parse(cls, expr: str) -> Metric:
        """Parse a metric expression, raising :class:`ClaimError` when it is malformed or uses an unknown function."""
        if not isinstance(expr, str):
            raise ClaimError(f"metric must be a string, got {type(expr).__name__}")
        if m := _RE_PLAIN.match(expr):
            return cls(expr.strip(), m.group(1))
        if m := _RE_INDEX.match(expr):
            return cls(expr.strip(), m.group(1), index=int(m.group(2)))
        if m := _RE_FUNC.match(expr):
            func, name = m.group(1), m.group(2)
            if func not in FUNCTIONS:
                raise ClaimError(f"unknown function {func!r} in metric {expr!r}; allowed: {', '.join(FUNCTIONS)}")
            return cls(expr.strip(), name, func=func)
        raise ClaimError(f"cannot parse metric {expr!r}; use name, name[i] or f(name)")

    def evaluate(self, outputs: Mapping[str, Any]) -> float:
        """Compute the metric from one run's outputs.

        A plain name must refer to a scalar output. ``std`` is the sample standard deviation, and 0 for a
        single value. Raises :class:`ClaimError` when the output is missing or empty, or the index is out of
        range.
        """
        if self.name not in outputs:
            raise ClaimError(f"metric {self.expr!r} needs output {self.name!r}; model returned {sorted(outputs)}")
        value = outputs[self.name]
        if self.func is None and self.index is None:
            if isinstance(value, list):
                raise ClaimError(f"output {self.name!r} is a series; use e.g. {self.name}[-1] or mean({self.name})")
            return float(value)
        arr = np.atleast_1d(np.asarray(value, dtype=float))
        if arr.size == 0:
            raise ClaimError(f"output {self.name!r} is empty")
        if self.index is not None:
            try:
                return float(arr[self.index])
            except IndexError as exc:
                raise ClaimError(f"index {self.index} out of range for {self.name!r} (length {arr.size})") from exc
        f = self.func
        if f == "mean":
            return float(arr.mean())
        if f == "sum":
            return float(arr.sum())
        if f == "min":
            return float(arr.min())
        if f == "max":
            return float(arr.max())
        if f == "argmax":
            return float(np.argmax(arr))
        if f == "argmin":
            return float(np.argmin(arr))
        if f == "last":
            return float(arr[-1])
        if f == "first":
            return float(arr[0])
        return float(arr.std(ddof=1)) if arr.size > 1 else 0.0  # std


@dataclass(frozen=True)
class Claim:
    """One conclusion of a model, written as a test on a metric.

    Attributes:
        id: Short identifier, unique within the audit.
        statement: The conclusion in plain words.
        metric: The parsed metric the test applies to.
        test: One of ``greater_than``, ``less_than``, ``between``, ``monotone_increasing`` or
            ``monotone_decreasing``.
        value: The threshold, or a ``(low, high)`` pair for ``between``; ``None`` for monotone tests.
        over: The parameter a monotone claim varies.
        where: Values of each parameter to evaluate the claim at; their combinations form the grid.
        tolerance: Absolute equivalence margin; ``None`` uses :func:`~abmfact.stats.default_tolerance`.
        skip_checks: Checks to skip, each mapped to the reason.
    """

    id: str
    statement: str
    metric: Metric
    test: str
    value: float | tuple[float, float] | None = None
    over: str | None = None
    where: dict[str, list[Any]] = field(default_factory=dict)
    tolerance: float | None = None
    skip_checks: dict[str, str] = field(default_factory=dict)

    @property
    def is_monotone(self) -> bool:
        """True for the ``monotone_increasing`` and ``monotone_decreasing`` tests."""
        return self.test.startswith("monotone")

    def points(self, base: Mapping[str, Any]) -> list[dict[str, Any]]:
        """Parameter dicts this claim is evaluated at: the cartesian product of ``where`` over ``base``."""
        if not self.where:
            return [dict(base)]
        keys = sorted(self.where)
        out = []
        for combo in itertools.product(*(self.where[k] for k in keys)):
            p = dict(base)
            p.update(dict(zip(keys, combo, strict=True)))
            out.append(p)
        return out

    def to_dict(self) -> dict[str, Any]:
        """The claim as a plain dict for ``results.json``; optional fields appear only when set."""
        d: dict[str, Any] = {
            "id": self.id,
            "statement": self.statement,
            "metric": self.metric.expr,
            "test": self.test,
        }
        if self.value is not None:
            d["value"] = list(self.value) if isinstance(self.value, tuple) else self.value
        if self.over:
            d["over"] = self.over
        if self.where:
            d["where"] = self.where
        if self.tolerance is not None:
            d["tolerance"] = self.tolerance
        if self.skip_checks:
            d["skip_checks"] = self.skip_checks
        return d


def parse_claim(raw: Mapping[str, Any], known_checks: Sequence[str] = ()) -> Claim:
    """Parse and validate one claim from an audit file.

    Args:
        raw: The claim mapping as read from YAML.
        known_checks: Check names allowed in ``skip_checks``; when empty, those names are not checked.

    Returns:
        The parsed :class:`Claim`.

    Raises:
        ClaimError: When a required key is missing or a field is not valid for the test.
    """
    if not isinstance(raw, Mapping):
        raise ClaimError("each claim must be a mapping")
    for key in ("id", "metric", "test"):
        if key not in raw:
            raise ClaimError(f"claim is missing {key!r}: {dict(raw)}")
    cid = str(raw["id"])
    test = str(raw["test"])
    if test not in TESTS:
        raise ClaimError(f"claim {cid}: unknown test {test!r}; allowed: {', '.join(TESTS)}")
    metric = Metric.parse(raw["metric"])
    value: float | tuple[float, float] | None = None
    over = raw.get("over")
    where = {str(k): list(v) if isinstance(v, list | tuple) else [v] for k, v in dict(raw.get("where") or {}).items()}
    if test in ("greater_than", "less_than"):
        if "value" not in raw:
            raise ClaimError(f"claim {cid}: test {test} needs a value")
        value = float(raw["value"])
    elif test == "between":
        v = raw.get("value")
        if not isinstance(v, list | tuple) or len(v) != 2 or float(v[0]) >= float(v[1]):
            raise ClaimError(f"claim {cid}: between needs value: [low, high] with low < high")
        value = (float(v[0]), float(v[1]))
    else:
        if not over:
            raise ClaimError(f"claim {cid}: {test} needs 'over: <parameter>'")
        if over not in where or len(where[over]) < 2:
            raise ClaimError(f"claim {cid}: {test} needs at least two values of {over!r} under 'where'")
        try:
            [float(x) for x in where[over]]
        except (TypeError, ValueError) as exc:
            raise ClaimError(f"claim {cid}: values of {over!r} must be numbers") from exc
    tol = raw.get("tolerance")
    if tol is not None and float(tol) <= 0:
        raise ClaimError(f"claim {cid}: tolerance must be positive")
    skip = {str(k): str(v) for k, v in dict(raw.get("skip_checks") or {}).items()}
    if known_checks:
        unknown = sorted(set(skip) - set(known_checks))
        if unknown:
            raise ClaimError(f"claim {cid}: unknown checks in skip_checks: {unknown}")
    return Claim(
        id=cid,
        statement=str(raw.get("statement", "")),
        metric=metric,
        test=test,
        value=value,
        over=str(over) if over else None,
        where=where,
        tolerance=float(tol) if tol is not None else None,
        skip_checks=skip,
    )


def parse_claims(raw: Sequence[Mapping[str, Any]], known_checks: Sequence[str] = ()) -> list[Claim]:
    """Parse a list of claims with :func:`parse_claim`, rejecting an empty list and duplicate ids."""
    if not raw:
        raise ClaimError("an audit needs at least one claim")
    claims = [parse_claim(c, known_checks) for c in raw]
    ids = [c.id for c in claims]
    dupes = sorted({i for i in ids if ids.count(i) > 1})
    if dupes:
        raise ClaimError(f"duplicate claim ids: {dupes}")
    return claims
