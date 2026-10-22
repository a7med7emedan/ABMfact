"""Engines: one adapter contract for every simulator, discovered through entry points."""

from __future__ import annotations

from .base import Availability, Capabilities, Engine, EngineError, PreparedRun, RunHandle
from .registry import get_engine, list_engines

__all__ = [
    "Availability",
    "Capabilities",
    "Engine",
    "EngineError",
    "PreparedRun",
    "RunHandle",
    "get_engine",
    "list_engines",
]
