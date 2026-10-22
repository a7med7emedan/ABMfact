"""Runtime backends: local, container and Slurm. The container and Slurm backends load on first use."""

from __future__ import annotations

from typing import Any

from .base import Backend
from .local import LocalBackend

__all__ = ["Backend", "ContainerBackend", "LocalBackend", "SlurmBackend", "detect_runtime"]

_LAZY = {
    "ContainerBackend": ".container",
    "detect_runtime": ".container",
    "SlurmBackend": ".slurm",
}


def __getattr__(name: str) -> Any:
    if name in _LAZY:
        from importlib import import_module

        return getattr(import_module(_LAZY[name], __name__), name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
