"""Run many jobs of one model: each job is an engine, its options, parameters and a seed.

This is the run loop every mode shares, and the one ``abmfact compare`` uses. Jobs run on the local backend,
one process per worker; an engine that works in a container starts the container itself. Workers read the run
store but never write it: the parent stores each finished run, so a mode that is run again, or resumed after an
interruption, reuses every run that already finished.
"""

from __future__ import annotations

import json
import shutil
import tempfile
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

from ..knobs import Knobs

if TYPE_CHECKING:
    from ..engines.base import Engine
    from ..records.record import RunRecord


@dataclass(frozen=True)
class Job:
    """One run: an engine with its options, ABM-IR or native parameters, and a seed.

    Attributes:
        engine: The engine name, as for ``run-model --engine``.
        seed: The seed.
        params: Parameters for this run: ABM-IR dotted paths, or native paths for engines that take them.
        options: Engine options, as for ``run-model --option``.
        label: A name for the setting, such as a design point; runs with a label get their own folder.
    """

    engine: str
    seed: int
    params: Mapping[str, Any] = field(default_factory=dict)
    options: Mapping[str, Any] = field(default_factory=dict)
    label: str = ""

    def folder(self, root: Path) -> Path:
        """Where this run's working files go under ``root``."""
        base = root / self.engine
        return (base / self.label if self.label else base) / f"seed-{self.seed}"


@dataclass
class JobResult:
    """What one job gave: a run record, or the error that stopped it before a record existed.

    Attributes:
        job: The job.
        record: The run record; its status says whether the run worked, and ``cached`` when it was reused.
        error: Why no record exists.
    """

    job: Job
    record: RunRecord | None = None
    error: str | None = None

    @property
    def ok(self) -> bool:
        """True when the run worked and its outputs are loaded."""
        return self.record is not None and self.record.status in ("ok", "cached") and self.record.outputs is not None

    @property
    def problem(self) -> str | None:
        """Why the run gave no outputs, or None when it did."""
        if self.ok:
            return None
        if self.error is not None:
            return self.error
        assert self.record is not None
        return self.record.error or "no outputs"


def _options_key(name: str, options: Mapping[str, Any]) -> str:
    return name + json.dumps(dict(options), sort_keys=True, default=str)


def make_engine(name: str, options: Mapping[str, Any] | None = None) -> Engine:
    """The engine ``name`` with ``options``."""
    from ..engines import get_engine

    return get_engine(name, **dict(options or {}))


class _BatchTask:
    """One job in a worker process. Engines are built once per worker and kept for its later jobs."""

    def __init__(self, model: str, workdir: Path, store_root: Path | None):
        self.model = model
        self.workdir = workdir
        self.store_root = store_root
        self._engines: dict[str, Engine] = {}

    def __getstate__(self) -> dict[str, Any]:
        return {**self.__dict__, "_engines": {}}

    def __call__(self, job: Job) -> JobResult:
        from ..records.store import RunStore

        store = RunStore(self.store_root) if self.store_root else None
        try:
            key = _options_key(job.engine, job.options)
            if key not in self._engines:
                self._engines[key] = make_engine(job.engine, job.options)
            engine = self._engines[key]
            prepared = engine.prepare(self.model, dict(job.params), Knobs(), job.seed, job.folder(self.workdir))
            if store is not None and (hit := store.get(prepared.request)) is not None:
                return JobResult(job, hit)
            return JobResult(job, engine.collect(engine.run(prepared)))
        except Exception as exc:  # noqa: BLE001 - one failed run is reported, not fatal
            return JobResult(job, error=f"{type(exc).__name__}: {exc}")


def warm_up(
    model: str | Path, engine: str, options: Mapping[str, Any], params: Mapping[str, Any], workdir: Path
) -> None:
    """Prepare one run in this process, so builds and translations are cached before workers start."""
    eng = make_engine(engine, options)
    with tempfile.TemporaryDirectory(dir=workdir) as tmp:
        eng.prepare(str(model), dict(params), Knobs(), 0, Path(tmp))
        shutil.rmtree(tmp, ignore_errors=True)


def engine_status(
    name: str, model: str | Path, options: Mapping[str, Any] | None = None
) -> tuple[str | None, dict[str, Any]]:
    """``(reason not to run, description)`` for one engine, with its fidelity report when it translates."""
    try:
        engine = make_engine(name, options)
    except Exception as exc:  # noqa: BLE001 - an unknown or broken engine is reported
        return f"{type(exc).__name__}: {exc}", {}
    info: dict[str, Any] = {"description": getattr(engine, "description", "")}
    reasons = engine.refuse(str(model), Knobs())
    if reasons:
        return "; ".join(reasons), info
    avail = engine.availability()
    info["version"] = avail.version
    if not avail.ok:
        return f"not installed here: {avail.detail}", info
    translate = getattr(engine, "translation", None)
    report = translate(str(model)) if translate else None
    if report is not None:
        info["tier"] = report.tier
        info["fidelity"] = report.counts()
        info["losses"] = [[e.path, e.status, e.note] for e in report.entries if e.status != "exact"]
    else:
        info["tier"] = "T0"
    return None, info


def run_batch(
    model: str | Path,
    jobs: Sequence[Job],
    *,
    workdir: str | Path | None = None,
    store: str | Path | None = None,
    workers: int = 1,
    warm: bool = True,
    progress: Callable[[int, int], None] | None = None,
) -> list[JobResult]:
    """Run every job and return their results in job order.

    Args:
        model: The model file or native project every job runs.
        jobs: The runs.
        workdir: Where run folders go; a temporary folder by default, removed afterwards.
        store: A run store. Runs already in it are reused; new runs that worked are added to it.
        workers: Runs at once.
        warm: Prepare one run per distinct engine, options and parameters first, in this process, so builds and
            translations exist before workers start. A setting that cannot be prepared fails its jobs.
        progress: Called with ``(done, total)`` after each run.
    """
    from ..records.store import RunStore
    from ..runtime.local import LocalBackend

    owned = tempfile.TemporaryDirectory() if workdir is None else None
    root = Path(owned.name if owned else workdir)  # type: ignore[arg-type]
    root.mkdir(parents=True, exist_ok=True)

    def setting(job: Job) -> str:
        return _options_key(job.engine, job.options) + json.dumps(dict(job.params), sort_keys=True, default=str)

    blocked: dict[str, str | None] = {}
    if warm:
        for job in jobs:
            if setting(job) in blocked:
                continue
            try:
                warm_up(model, job.engine, job.options, job.params, root)
                blocked[setting(job)] = None
            except Exception as exc:  # noqa: BLE001 - a setting that cannot prepare fails its own jobs only
                blocked[setting(job)] = f"could not prepare a run: {type(exc).__name__}: {exc}"

    def _blocked(job: Job) -> str | None:
        return blocked.get(setting(job))

    todo = [j for j in jobs if _blocked(j) is None]
    task = _BatchTask(str(model), root, Path(store) if store else None)
    done = iter(LocalBackend(workers=workers).map(task, todo, progress))
    results = [JobResult(j, error=why) if (why := _blocked(j)) is not None else next(done) for j in jobs]
    if store:
        run_store = RunStore(store)
        for r in results:
            if r.record is not None and r.record.status == "ok" and r.record.outputs is not None:
                outputs = r.record.outputs
                r.record = run_store.put(r.record)
                r.record.outputs = outputs
    if owned is not None:
        owned.cleanup()
    return results
