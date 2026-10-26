"""The adapter contract every engine implements: capabilities, prepare, run and collect."""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Callable, Mapping
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, ClassVar

from ..knobs import KNOB_NAMES, Knobs, Support, derived_seed
from ..records import FidelityReport, RunOutputs, RunRecord, RunRequest
from ..records.cache import cache_bypassed

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
        """The capabilities as a JSON-ready dict, with tuples as lists and ``ir_features`` sorted."""
        d = asdict(self)
        d["ir_features"] = sorted(self.ir_features)
        for k in ("dimensions", "intracellular", "knobs", "native_formats", "update_orders"):
            d[k] = list(d[k])
        return d


@dataclass(frozen=True)
class Availability:
    """Whether an engine can run on this machine now, as reported by :meth:`Engine.availability`.

    Attributes:
        ok: True when the engine can prepare and run models here.
        detail: A short human-readable explanation, for example what is installed or what is missing.
        version: The version of the simulator found, when the engine knows it; otherwise ``None``.
    """

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
    def capabilities(self) -> Capabilities:
        """Declare what this engine can do, for every model it runs.

        It takes no model, so it describes the engine as a whole; :meth:`refuse` reports the limits for a
        particular model. The default :meth:`refuse` and :meth:`support_for` read ``knobs`` and
        ``update_orders`` from it.

        Returns:
            The engine's :class:`Capabilities`.
        """

    def availability(self) -> Availability:
        """Report whether the engine can run on this machine, for example whether its simulator is installed.

        The default reports a built-in engine that is always available. Override it when the engine needs
        an external program, library or container, and report what is missing in the result rather than raise.

        Returns:
            An :class:`Availability` with ``ok``, a short ``detail`` and, when known, the ``version``.
        """
        return Availability(True, "built in")

    def load(self, model: Any) -> Any:
        """Turn a user-supplied model (often a path) into what :meth:`prepare` takes. Default: unchanged."""
        return model

    def translation(self, model: Any) -> FidelityReport | None:
        """How faithfully this engine would run ``model``, from a dry run of its translation.

        ``None`` means the engine runs the model as given, with nothing translated: a native project, or an
        ABM-IR model that the engine reads directly. An engine that translates ABM-IR into its own format
        overrides this and returns the fidelity report of that translation, without running anything.
        """
        return None

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
    ) -> PreparedRun:
        """Set up one run without starting it.

        The engine builds the :class:`~abmfact.records.RunRequest` that identifies the run, applies the
        parameters, knobs and seed, and returns either an in-process ``call`` or a native ``argv`` with its
        ``workdir``. The request must include everything that decides the result, because records are
        cached by its key.

        Args:
            model: The model as the user gave it or as returned by :meth:`load`; built-in engines accept both.
            params: Parameter values to set for this run; their names follow the engine's own conventions.
            knobs: The knob settings; :meth:`run_once` checks them with :meth:`refuse` before calling this.
            seed: The seed requested by the user. An engine that does not manage its own random streams
                passes it through :meth:`seed_for` before use, and records the requested seed.
            workdir: A folder the engine may use for this run's files, or ``None`` to choose its own.

        Returns:
            A :class:`PreparedRun` that a backend can start.

        Raises:
            EngineError: The model cannot be run as requested.
        """

    def run(self, prepared: PreparedRun, backend: Backend | None = None) -> RunHandle:
        """Start a prepared run on a backend, by default the local one.

        Local and container backends finish the run before returning. A failed native run is reported in
        the handle's ``status`` and ``error`` rather than raised. Override this when the engine must choose
        the backend itself.

        Args:
            prepared: The run returned by :meth:`prepare`.
            backend: The backend to submit to, or ``None`` for a :class:`~abmfact.runtime.local.LocalBackend`.

        Returns:
            A :class:`RunHandle` for the started or finished run.
        """
        from ..runtime.local import LocalBackend

        return (backend or LocalBackend()).submit(prepared)

    @abstractmethod
    def collect(self, handle: RunHandle) -> RunRecord:
        """Turn a finished run into a run record.

        The engine reads its outputs into the common schema when the handle does not already hold them,
        and adds output paths and warnings. :meth:`record` builds the record and sets ``status`` from the
        handle. A failed run should give a record with ``status="failed"`` rather than raise.

        Args:
            handle: The finished run returned by :meth:`run`.

        Returns:
            The :class:`~abmfact.records.RunRecord` for the run.
        """

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
        successful run is written to it, except while the cache is bypassed (determinism reruns), when the
        run always executes and the store is left as it was.
        """
        knobs = knobs or Knobs()
        reasons = self.refuse(model, knobs)
        if reasons:
            raise EngineError("; ".join(reasons))
        prepared = self.prepare(model, dict(params or {}), knobs, seed, workdir)
        if store is not None and (hit := store.get(prepared.request)) is not None:
            return hit
        record = self.collect(self.run(prepared, backend))
        if store is not None and record.status == "ok" and not cache_bypassed():
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
