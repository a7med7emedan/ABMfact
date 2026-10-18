"""Tiny models for tests. Each returns quickly and has a known answer."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import numpy as np

from abmfact.adapters import PythonAdapter
from abmfact.knobs import Knobs

ALL = {
    "update_order": {"values": ["random", "id"], "default": "random"},
    "agent_order": True,
    "rng_streams": True,
    "grid": True,
    "dt_scale": True,
    "pop_scale": True,
}


def _rng(seed: int, knobs: Knobs) -> np.random.Generator:
    salt = 99 if knobs.rng_streams == "independent" else 0
    return np.random.default_rng([seed, salt])


def noisy(params: Mapping[str, Any], seed: int, knobs: Knobs) -> dict[str, Any]:
    """x ~ Normal(mu + slope * a, 1). Correct: ignores every knob."""
    rng = _rng(seed, knobs)
    mu = float(params.get("mu", 10.0)) + float(params.get("slope", 0.0)) * float(params.get("a", 0.0))
    series = rng.normal(mu, 1.0, size=5)
    return {"x": float(series.mean()), "series": series.tolist()}


def order_bug(params: Mapping[str, Any], seed: int, knobs: Knobs) -> dict[str, Any]:
    """Shifts by +5 under id activation order."""
    out = noisy(params, seed, knobs)
    if knobs.update_order == "id":
        out["x"] += 5.0
    return out


def spread_bug(params: Mapping[str, Any], seed: int, knobs: Knobs) -> dict[str, Any]:
    """Same mean, four times the spread when the step is halved."""
    rng = _rng(seed, knobs)
    sd = 4.0 if knobs.dt_scale != 1.0 else 1.0
    return {"x": float(rng.normal(10.0, sd))}


def constant(params: Mapping[str, Any], seed: int, knobs: Knobs) -> dict[str, Any]:
    return {"x": 10.0}


noisy_model = PythonAdapter(noisy, supports=ALL, name="noisy")
order_bug_model = PythonAdapter(order_bug, supports=ALL, name="order_bug")
spread_bug_model = PythonAdapter(spread_bug, supports=ALL, name="spread_bug")
constant_model = PythonAdapter(constant, supports=ALL, name="constant")
bare_noisy = PythonAdapter(noisy, supports=None, name="bare")
