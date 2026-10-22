"""Run requests and run records: what was asked for, and what happened."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from .schema import RunOutputs

RECORD_VERSION = "1"
STATUSES = ("ok", "failed", "cached")


def canonical(obj: Any) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), allow_nan=False, default=str)


@dataclass(frozen=True)
class RunRequest:
    """Everything that decides a run's result. Two requests with the same key give the same run.

    ``engine_version`` or ``image_digest`` pin the engine; ``outputs_spec`` names what was collected.
    """

    engine: str
    model_hash: str
    params: dict[str, Any]
    seed: int
    knobs: dict[str, Any]
    engine_version: str | None = None
    image_digest: str | None = None
    outputs_spec: str = "default"

    def to_dict(self) -> dict[str, Any]:
        return {
            "engine": self.engine,
            "engine_version": self.engine_version,
            "image_digest": self.image_digest,
            "model_hash": self.model_hash,
            "params": self.params,
            "seed": self.seed,
            "knobs": self.knobs,
            "outputs_spec": self.outputs_spec,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> RunRequest:
        return cls(**d)

    def key(self) -> str:
        """SHA-256 of the canonical JSON of the request: the run's content address."""
        return hashlib.sha256(canonical(self.to_dict()).encode()).hexdigest()


def now() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


@dataclass
class RunRecord:
    """One run: the request, how it went, where its outputs are. ``outputs`` stays in memory only."""

    request: RunRequest
    status: str = "ok"
    wall_time: float | None = None
    log_path: str | None = None
    output_paths: dict[str, str] = field(default_factory=dict)
    output_sha256: dict[str, str] = field(default_factory=dict)
    backend: str = "local"
    abmfact_version: str = ""
    created: str = field(default_factory=now)
    error: str | None = None
    warnings: list[str] = field(default_factory=list)
    outputs: RunOutputs | None = field(default=None, compare=False, repr=False)

    @property
    def run_id(self) -> str:
        return self.request.key()

    def to_dict(self) -> dict[str, Any]:
        return {
            "record_version": RECORD_VERSION,
            "run_id": self.run_id,
            "request": self.request.to_dict(),
            "status": self.status,
            "wall_time": self.wall_time,
            "log_path": self.log_path,
            "output_paths": self.output_paths,
            "output_sha256": self.output_sha256,
            "backend": self.backend,
            "abmfact_version": self.abmfact_version,
            "created": self.created,
            "error": self.error,
            "warnings": self.warnings,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> RunRecord:
        d = dict(d)
        d.pop("record_version", None)
        d.pop("run_id", None)
        d["request"] = RunRequest.from_dict(d["request"])
        return cls(**d)
