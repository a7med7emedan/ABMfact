"""Runtime backends: local, container and Slurm."""

from __future__ import annotations

from .base import Backend
from .container import ContainerBackend, detect_runtime
from .local import LocalBackend
from .slurm import SlurmBackend

__all__ = ["Backend", "ContainerBackend", "LocalBackend", "SlurmBackend", "detect_runtime"]
