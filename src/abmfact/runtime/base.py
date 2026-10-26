"""Backends: where a prepared run executes (this machine, a container, or a cluster)."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ..engines.base import Availability, PreparedRun, RunHandle


class Backend(ABC):
    """Base class for the places a prepared run can execute.

    Attributes:
        name: The short backend name recorded on each ``RunHandle``.
    """

    name: str = "backend"

    @abstractmethod
    def availability(self) -> Availability:
        """Report whether this backend can run here now, with a reason and, where known, a version."""

    @abstractmethod
    def submit(self, prepared: PreparedRun) -> RunHandle:
        """Start a run. Local and container backends finish it before returning."""

    def wait(self, handle: RunHandle) -> RunHandle:
        """Block until a submitted run finishes. Backends that finish in ``submit`` return it unchanged."""
        return handle
