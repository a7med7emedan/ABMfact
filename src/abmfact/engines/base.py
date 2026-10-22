"""The adapter contract every engine implements: capabilities, prepare, run and collect."""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Callable, Mapping
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, ClassVar

from ..knobs import KNOB_NAMES, Knobs, Support, derived_seed
from ..records import RunOutputs, RunRecord, RunRequest

if TYPE_CHECKING:
    from ..records.store import RunStore
    from ..runtime.base import Backend


class EngineError(RuntimeError):
    """An engine cannot run a model, or a run failed."""


@dataclass(frozen=True)
class Capabilities:
    """What an engine can do, declared rather than assumed (platform design rule 5).

    ``knobs`` lists the knobs the engine honours for every model; an engine may honour fewer for a
    particular model and says so in :meth:`Engine.refuse`. ``ir_features`` names the ABM-IR features it
    runs (for example ``substrates``, ``rules``, ``chemotaxis``).
    """

    name: str
    mechanics: str
    dimensions: tuple[int, ...] = ()
    diffusion: bool = False
    intracellular: tuple[str, ...] = ()
    gpu: bool = False
    knobs: tuple[str, ...] = ()
    native_formats: tuple[str, ...] = ()
    ir_features: frozenset[str] = frozenset()
    notes: str = ""
    update_orders: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["ir_features"] = sorted(self.ir_features)
        for k in ("dimensions", "intracellular", "knobs", "native_formats", "update_orders"):
            d[k] = list(d[k])
        return d


@dataclass(frozen=True)
class Availability:
    ok: bool
    detail: str
    version: str | None = None


@dataclass
class PreparedRun:
    """A run ready to start: either an in-process ``call`` or a native ``argv`` run in ``workdir``."""

    engine: str
    request: RunRequest
    workdir: Path | None = None
    argv: list[str] | None = None
    call: Callable[[], RunOutputs] | None = None
    env: dict[str, str] = field(default_factory=dict)
    image: str | None = None
    timeout: float | None = None
    context: dict[str, Any] = field(default_factory=dict)


@dataclass
class RunHandle:
    """A started (or finished) run on a backend."""

    prepared: PreparedRun
    backend: str
    status: str = "done"
    outputs: RunOutputs | None = None
    returncode: int | None = None
    wall_time: float | None = None
    log_path: Path | None = None
    error: str | None = None
    job_id: str | None = None
    image_digest: str | None = None


def changed_knobs(knobs: Knobs) -> list[str]:
    """Knob names that differ from their defaults, in :data:`KNOB_NAMES` order."""
    changed = knobs.changed()
    return [n for n in KNOB_NAMES if n in changed]


class Engine(ABC):
    """Base class for engines. Subclasses set ``name`` and implement the four contract methods."""

    name: ClassVar[str] = "engine"
    description: ClassVar[str] = ""

    @abstractmethod
    def capabilities(self) -> Capabilities: ...

    def availability(self) -> Availability:
        return Availability(True, "built in")

    def load(self, model: Any) -> Any:
        """Turn a user-supplied model (often a path) into what :meth:`prepare` takes. Default: unchanged."""
        return model

    def support_for(self, model: Any) -> Support:
        """The knobs this engine honours for this model, found by asking :meth:`refuse` about each one.

        ``update_order`` lists :attr:`Capabilities.update_orders` (the first is the default). ``rng_streams``
        is declared only when the engine manages its own streams; otherwise the seed is remapped.
        """
        caps = self.capabilities()

        def ok(knob: str, *probes: Knobs) -> bool:
            return knob in caps.knobs and all(not self.refuse(model, k) for k in probes)

        orders = tuple(v for v in caps.update_orders if ok("update_order", Knobs(update_order=v)))
        return Support(
            update_order=orders if len(orders) > 1 else (),
            update_order_default=orders[0] if len(orders) > 1 else None,
            agent_order=ok("agent_order", Knobs(agent_order="shuffled", agent_perm_seed=1)),
            rng_streams=ok("rng_streams", Knobs(rng_streams="independent")),
            grid=ok("grid_offset", Knobs(grid_offset=(0.5, 0.5))) and ok("grid_rotation", Knobs(grid_rotation=90)),
            dt_scale=ok("dt_scale", Knobs(dt_scale=0.5)),
            pop_scale=ok("pop_scale", Knobs(pop_scale=2.0)),
            dx_scale=ok("dx_scale", Knobs(dx_scale=0.5)),
        )

    def refuse(self, model: Any, knobs: Knobs) -> list[str]:
        """Reasons this engine cannot honour the request; empty when it can run it.

        The default refuses knobs outside :attr:`Capabilities.knobs`. ``rng_streams`` is always honoured,
        because :meth:`seed_for` remaps the seed when an engine does not manage its own streams.
        """
        honoured = set(self.capabilities().knobs) | {"rng_streams"}
        return [f"{self.name} does not honour the {k} knob" for k in changed_knobs(knobs) if k not in honoured]

    def seed_for(self, seed: int, knobs: Knobs) -> int:
        """The seed to pass on: remapped for ``rng_streams=independent`` unless the engine honours it."""
        if knobs.rng_streams == "independent" and "rng_streams" not in self.capabilities().knobs:
            return derived_seed(seed)
        return int(seed)

    @abstractmethod
    def prepare(
        self, model: Any, params: Mapping[str, Any], knobs: Knobs, seed: int, workdir: Path | None = None
    ) -> PreparedRun: ...

    def run(self, prepared: PreparedRun, backend: Backend | None = None) -> RunHandle:
        from ..runtime.local import LocalBackend

        return (backend or LocalBackend()).submit(prepared)

    @abstractmethod
    def collect(self, handle: RunHandle) -> RunRecord: ...

    def run_once(
        self,
        model: Any,
        params: Mapping[str, Any] | None = None,
        seed: int = 0,
        knobs: Knobs | None = None,
        backend: Backend | None = None,
        workdir: Path | None = None,
        store: RunStore | None = None,
    ) -> RunRecord:
        """Prepare, run and collect one run, refusing first if the engine cannot honour it.

        With a ``store``, an identical earlier request is returned from it (``status="cached"``) and a new
        successful run is written to it.
        """
        knobs = knobs or Knobs()
        reasons = self.refuse(model, knobs)
        if reasons:
            raise EngineError("; ".join(reasons))
        prepared = self.prepare(model, dict(params or {}), knobs, seed, workdir)
        if store is not None and (hit := store.get(prepared.request)) is not None:
            return hit
        record = self.collect(self.run(prepared, backend))
        if store is not None and record.status == "ok":
            outputs = record.outputs
            record = store.put(record)
            record.outputs = outputs
        return record

    def record(self, handle: RunHandle, **extra: Any) -> RunRecord:
        """A run record from a finished handle; subclasses add output paths and warnings."""
        from .. import __version__

        status = "ok" if handle.status == "done" and not handle.error else "failed"
        return RunRecord(
            request=handle.prepared.request,
            status=status,
            wall_time=handle.wall_time,
            log_path=str(handle.log_path) if handle.log_path else None,
            backend=handle.backend,
            abmfact_version=__version__,
            error=handle.error,
            outputs=handle.outputs,
            **extra,
        )
