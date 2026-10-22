"""The local backend: in-process calls, native commands as subprocesses, and the v0.1 process pool."""

from __future__ import annotations

import os
import subprocess
import time
from pathlib import Path

from ..engines.base import Availability, EngineError, PreparedRun, RunHandle
from .base import Backend


class LocalBackend(Backend):
    """Run on this machine.

    An in-process ``call`` runs directly and its exceptions propagate unchanged, as v0.1 adapters always
    behaved. A native ``argv`` runs in the prepared work directory with stdout and stderr in ``run.log``.
    """

    name = "local"

    def __init__(self, workers: int = 1, timeout: float | None = None):
        self.workers = max(1, int(workers))
        self.timeout = timeout

    def availability(self) -> Availability:
        return Availability(True, f"this machine ({os.cpu_count()} CPUs)")

    def submit(self, prepared: PreparedRun) -> RunHandle:
        start = time.perf_counter()
        if prepared.call is not None:
            outputs = prepared.call()
            return RunHandle(prepared, self.name, outputs=outputs, wall_time=time.perf_counter() - start)
        if not prepared.argv or prepared.workdir is None:
            raise EngineError(f"{prepared.engine}: a prepared run needs a call, or an argv and a work directory")
        return run_command(prepared, prepared.argv, self.name, self.timeout)


def run_command(prepared: PreparedRun, argv: list[str], backend: str, timeout: float | None) -> RunHandle:
    """Run ``argv`` in the work directory, logging to ``run.log``; never raises on a failed run."""
    assert prepared.workdir is not None
    log = Path(prepared.workdir) / "run.log"
    env = {**os.environ, **prepared.env}
    start = time.perf_counter()
    limit = prepared.timeout or timeout
    with log.open("w", encoding="utf-8") as fh:
        try:
            proc = subprocess.run(  # noqa: S603 - argv is built by an engine from the user's own model
                argv, cwd=prepared.workdir, stdout=fh, stderr=subprocess.STDOUT, env=env, timeout=limit, check=False
            )
        except subprocess.TimeoutExpired:
            return RunHandle(
                prepared, backend, status="failed", wall_time=time.perf_counter() - start, log_path=log,
                error=f"timed out after {limit:g}s",
            )  # fmt: skip
        except OSError as exc:
            return RunHandle(prepared, backend, status="failed", log_path=log, error=str(exc))
    wall = time.perf_counter() - start
    if proc.returncode != 0:
        tail = log.read_text(encoding="utf-8", errors="replace").strip().splitlines()[-5:]
        return RunHandle(
            prepared, backend, status="failed", returncode=proc.returncode, wall_time=wall, log_path=log,
            error=f"exited with {proc.returncode}: " + " | ".join(tail),
        )  # fmt: skip
    return RunHandle(prepared, backend, returncode=0, wall_time=wall, log_path=log)
