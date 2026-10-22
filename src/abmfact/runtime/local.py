"""The local backend: in-process calls, native commands as subprocesses, and the v0.1 process pool."""

from __future__ import annotations

import os
import pickle
import subprocess
import sys
import time
from collections.abc import Callable, Sequence
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path
from typing import Any, TypeVar

from ..engines.base import Availability, EngineError, PreparedRun, RunHandle
from .base import Backend

J = TypeVar("J")
R = TypeVar("R")

_WORKER_TASK: Any = None


def _init_worker(blob: bytes, paths: list[str]) -> None:
    global _WORKER_TASK
    for path in paths:
        if path not in sys.path:
            sys.path.insert(0, path)
    _WORKER_TASK = pickle.loads(blob)  # noqa: S301 - the task was pickled by this same process


def _call(job: Any) -> Any:
    return _WORKER_TASK(job)


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

    def map(
        self,
        task: Callable[[J], R],
        jobs: Sequence[J],
        progress: Callable[[int, int], None] | None = None,
    ) -> list[R]:
        """Apply ``task`` to every job, in a process pool when ``workers > 1``. Results keep job order.

        The task is pickled once and installed in each worker. A task that cannot be pickled (a lambda,
        say) runs serially in this process instead.
        """
        total = len(jobs)
        blob: bytes | None = None
        if self.workers > 1 and total > 1:
            try:
                blob = pickle.dumps(task)
            except Exception:  # noqa: BLE001 - an unpicklable task simply runs serially
                blob = None
        results: list[R] = []
        if blob is not None:
            chunk = max(1, total // (self.workers * 8))
            paths = [p for p in sys.path if p]
            with ProcessPoolExecutor(max_workers=self.workers, initializer=_init_worker, initargs=(blob, paths)) as ex:
                for i, out in enumerate(ex.map(_call, jobs, chunksize=chunk), 1):
                    results.append(out)
                    if progress:
                        progress(i, total)
            return results
        for i, job in enumerate(jobs, 1):
            results.append(task(job))
            if progress:
                progress(i, total)
        return results


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
