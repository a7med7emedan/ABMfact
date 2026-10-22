"""Runtime backends: local, container and Slurm."""

from __future__ import annotations

from .base import Backend
from .local import LocalBackend

__all__ = ["Backend", "LocalBackend"]
