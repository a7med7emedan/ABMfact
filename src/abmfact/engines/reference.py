"""The reference engine under the adapter contract."""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any

from .. import __version__
from ..ir import IRModel, apply_params, load_model, model_hash
from ..knobs import KNOB_NAMES, Knobs
from ..records import RunOutputs, RunRecord, RunRequest
from .base import Capabilities, Engine, EngineError, PreparedRun, RunHandle

FEATURES = frozenset(
    {"substrates", "secretion", "uptake", "rules", "chemotaxis", "motility", "adhesion_affinities",
     "events", "periodic_boundary", "dirichlet_boundary"}
)  # fmt: skip


def as_model(model: Any) -> IRModel:
    if isinstance(model, IRModel):
        return model
    if isinstance(model, str | Path):
        return load_model(model)
    raise EngineError(f"the reference engine runs ABM-IR models, got {type(model).__name__}")


class ReferenceEngine(Engine):
    name = "reference"
    description = "the built-in 2D NumPy reference engine"

    def __init__(self, solver: str = "implicit"):
        self.solver = solver

    def capabilities(self) -> Capabilities:
        return Capabilities(
            name=self.name,
            mechanics="off-lattice centre-based (PhysiCell potentials)",
            dimensions=(2,),
            diffusion=True,
            knobs=tuple(KNOB_NAMES),
            native_formats=("abm-ir",),
            ir_features=FEATURES,
            notes="grid knobs need a periodic domain; no intracellular models, attack or phagocytosis",
            update_orders=("random", "id", "reverse", "synchronous"),
        )

    def load(self, model: Any) -> IRModel:
        return as_model(model)

    def refuse(self, model: Any, knobs: Knobs) -> list[str]:
        from ..reference import refusals

        try:
            m = as_model(model)
        except (EngineError, OSError, ValueError) as exc:
            return [str(exc)]
        return refusals(m, knobs)

    def prepare(
        self, model: Any, params: Mapping[str, Any], knobs: Knobs, seed: int, workdir: Path | None = None
    ) -> PreparedRun:
        from ..reference import simulate

        base = as_model(model)
        m = apply_params(base, params)
        request = RunRequest(self.name, model_hash(base), dict(params), int(seed), knobs.to_dict(),
                             engine_version=f"abmfact {__version__}", outputs_spec=f"solver={self.solver}")  # fmt: skip
        solver = self.solver

        def call() -> RunOutputs:
            return simulate(m, int(seed), knobs, solver=solver)

        return PreparedRun(self.name, request, workdir=workdir, call=call)

    def collect(self, handle: RunHandle) -> RunRecord:
        return self.record(handle, warnings=list(handle.outputs.warnings) if handle.outputs else [])
