"""Run an engine's model inside a v0.1 audit: the bridge from the adapter contract back to ``run()``."""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any

from ..adapters import Adapter, AdapterError
from ..knobs import Knobs
from .base import EngineError
from .registry import get_engine


class EngineModelAdapter(Adapter):
    """A model plus an engine, as a v0.1 adapter.

    The model's observables become the run's outputs (one series each, plus ``time``), so claims use the
    usual metric grammar, for example ``last(cells)``. The engine decides which knobs the model supports.
    With a ``store`` directory, each run is cached there; the index is JSON lines so that parallel audit
    workers can append to it safely.
    """

    def __init__(
        self,
        engine: str,
        model: Any,
        options: Mapping[str, Any] | None = None,
        store: str | Path | None = None,
        name: str | None = None,
    ):
        self.engine_name = engine
        self.engine = get_engine(engine, **dict(options or {}))
        self.model = self.engine.load(model)
        self.store_path = Path(store) if store is not None else None
        self.name = name or f"{engine}:{getattr(self.model, 'name', model)}"
        self.support = self.engine.support_for(self.model)

    def _store(self) -> Any:
        if self.store_path is None:
            return None
        from ..records.store import RunStore

        return RunStore(self.store_path, index="jsonl")

    def _run(self, params: dict[str, Any], seed: int, knobs: Knobs) -> Mapping[str, Any]:
        from ..ir.errors import IRError

        try:
            record = self.engine.run_once(self.model, params, seed, knobs, store=self._store())
        except (IRError, EngineError) as exc:
            raise AdapterError(f"{self.name} (seed {seed}): {exc}") from exc
        if record.status not in ("ok", "cached") or record.outputs is None:
            raise AdapterError(f"{self.name} failed (seed {seed}): {record.error}")
        return record.outputs.scalars

    def describe(self) -> dict[str, Any]:
        """The adapter description (type, name and declared support) plus the ``engine`` name."""
        return {**super().describe(), "engine": self.engine_name}
