"""Engine discovery: built-in engines plus any registered under the ``abmfact.engines`` entry point group.

A third-party package registers an engine in its ``pyproject.toml``::

    [project.entry-points."abmfact.engines"]
    myengine = "mypackage.engine:MyEngine"
"""

from __future__ import annotations

import importlib
from importlib.metadata import entry_points
from typing import Any

from .base import Engine, EngineError

GROUP = "abmfact.engines"

#: Built-in engines, imported only when used.
BUILTIN: dict[str, str] = {
    "python": "abmfact.engines.legacy:PythonEngine",
    "mesa": "abmfact.engines.legacy:MesaEngine",
    "netlogo": "abmfact.engines.legacy:NetLogoEngine",
    "command": "abmfact.engines.legacy:CommandEngine",
    "reference": "abmfact.engines.reference:ReferenceEngine",
    "physicell": "abmfact.engines.physicell:PhysiCellEngine",
    "compucell3d": "abmfact.engines.compucell3d:CompuCell3DEngine",
    "morpheus": "abmfact.engines.morpheus:MorpheusEngine",
}


def _load(target: str) -> type[Engine]:
    module, _, attr = target.partition(":")
    obj = getattr(importlib.import_module(module), attr)
    if not (isinstance(obj, type) and issubclass(obj, Engine)):
        raise EngineError(f"{target} is not an Engine subclass")
    return obj


def engine_targets() -> dict[str, str]:
    """Engine name to ``module:Class``. Installed entry points add to, but never replace, built-ins."""
    targets = dict(BUILTIN)
    for ep in entry_points(group=GROUP):
        targets.setdefault(ep.name, ep.value)
    return targets


def list_engines() -> dict[str, type[Engine]]:
    """Every known engine name and its class, sorted by name; each class is imported.

    Raises:
        EngineError: A registered target is not an :class:`Engine` subclass.
    """
    return {name: _load(target) for name, target in sorted(engine_targets().items())}


def get_engine(name: str, **options: Any) -> Engine:
    """An instance of the engine called ``name``, built with ``options`` as keyword arguments.

    Raises:
        EngineError: No engine has that name, or its target is not an :class:`Engine` subclass.
    """
    targets = engine_targets()
    if name not in targets:
        raise EngineError(f"unknown engine {name!r}; available: {', '.join(sorted(targets))}")
    return _load(targets[name])(**options)
