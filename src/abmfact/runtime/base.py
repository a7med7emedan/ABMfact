"""Backends: where a prepared run executes (this machine, a container, or a cluster)."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ..engines.base import Availability, PreparedRun, RunHandle


class Backend(ABC):
    name: str = "backend"

    @abstractmethod
    def availability(self) -> Availability: ...

    @abstractmethod
    def submit(self, prepared: PreparedRun) -> RunHandle:
        """Start a run. Local and container backends finish it before returning."""

    def wait(self, handle: RunHandle) -> RunHandle:
        """Block until a submitted run finishes. Backends that finish in ``submit`` return it unchanged."""
        return handle
