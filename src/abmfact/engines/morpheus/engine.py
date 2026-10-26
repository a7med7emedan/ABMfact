"""Morpheus under the adapter contract.

Native mode (fidelity tier T0) runs a MorpheusML model as it is. An ABM-IR model is first translated to a
MorpheusML model (tier T1, :mod:`abmfact.engines.morpheus.translate`) and the export is cached by the model's
hash, the translator's own hash and the two scales. For each run the engine copies the model into a
run folder, sets any element a parameter names, and adds two loggers to its ``Analysis`` section: one for each
cell's id, type, centre and volume, one for every field. Loggers are Morpheus's own output components and change
no dynamics. Morpheus then runs with the seed, one thread unless told otherwise, gnuplot off, and the run's
output folder, all on its command line.

Morpheus models carry their own units: lattice nodes, and time in whatever unit the model chose. Outputs are
reported in micron and minutes through ``node_length`` and ``time_minutes``. Both are 1 by default for native
models, which keeps the model's own units; translated models use 2 micron per node and 1 minute per step.
Refining the time step or the lattice of a cellular Potts model needs other parameters rescaled with it, so
``dt_scale`` and ``dx_scale`` are refused. Morpheus has one random stream, set by the seed on its command line,
so ``rng_streams`` is tested by remapping the seed.

Morpheus 2.4.1 runs either from ``MORPHEUS_BIN`` (a ``morpheus`` executable) or in the image built from
``containers/morpheus/Dockerfile``, which holds the static x86-64 binary Morpheus publishes; on Apple silicon
that image runs under emulation.
"""

from __future__ import annotations

import os
import re
import shutil
import tempfile
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from ...knobs import Knobs
from ...records import FidelityReport, RunRecord, RunRequest
from ...records.record import canonical
from ..base import Availability, Capabilities, Engine, EngineError, PreparedRun, RunHandle
from .model import MorpheusModel, is_model
from .outputs import read_output

VERSION = "2.4.1"
IMAGE = f"abmfact/morpheus:{VERSION}"
PLATFORM = "linux/amd64"
MODEL_FILE = "model.xml"
DEFAULT_NODE_LENGTH = 2.0


def _slug(name: str) -> str:
    s = re.sub(r"\W+", "_", name).strip("_").lower()
    return s if s and not s[0].isdigit() else f"_{s}"


def default_observables(model: MorpheusModel) -> list[dict[str, Any]]:
    """``cells``, then ``cells_<type>`` for each cell type, then ``<field>_mean`` for each field."""
    obs: list[dict[str, Any]] = [{"name": "cells", "kind": "live_count"}]
    for t in model.biological_types():
        obs.append({"name": f"cells_{_slug(t)}", "kind": "live_count", "cell_type": t})
    for f in model.fields():
        obs.append({"name": f"{_slug(f)}_mean", "kind": "substrate_mean", "substrate": f})
    return obs


class MorpheusEngine(Engine):
    """Runs Morpheus 2.4.1 models as they are (tier T0), and ABM-IR models through a translation (tier T1).

    Args:
        toolchain: ``"auto"`` (a native ``MORPHEUS_BIN``, then the container), ``"native"`` or ``"docker"``.
        image: The container image.
        cache_dir: The folder for run folders; default ``~/.cache/abmfact/morpheus``.
        save_every: Save outputs every this many model time units; default a tenth of the run.
        node_length: Micron per lattice node: 1 for native models (lattice units) and 2 for ABM-IR exports,
            unless given.
        time_minutes: Minutes per Morpheus time unit, used to report times.
        threads: Morpheus threads; 1 by default, so a seed always gives the same run.
        observables: ABM-IR observable specs to compute; ``None`` uses :func:`default_observables`.
        timeout: The time limit for each run, in seconds; ``None`` for no limit.
    """

    name = "morpheus"
    description = "Morpheus 2.4.1 models (cellular Potts, ODE and PDE), natively or in a container"

    def __init__(
        self,
        toolchain: str = "auto",
        image: str = IMAGE,
        cache_dir: str | Path | None = None,
        save_every: float | None = None,
        node_length: float | None = None,
        time_minutes: float = 1.0,
        threads: int = 1,
        observables: Sequence[Mapping[str, Any]] | None = None,
        timeout: float | None = None,
    ):
        self.prefer = toolchain
        self.image = image
        self.cache_dir = Path(cache_dir) if cache_dir else Path.home() / ".cache" / "abmfact" / "morpheus"
        self.save_every = save_every
        self.node_length = float(node_length) if node_length is not None else None
        self.time_minutes = float(time_minutes)
        self.threads = int(threads)
        self.observables = [dict(o) for o in observables] if observables else None
        self.timeout = timeout

    # -- discovery ---------------------------------------------------------------------------------------

    def _route(self) -> tuple[str, str, str | None]:
        """``(kind, detail, runtime)``: ``native`` with ``MORPHEUS_BIN``, ``docker`` with the image, or ``none``."""
        from ...runtime.container import ContainerBackend

        missing = []
        if self.prefer in ("auto", "native"):
            binary = os.environ.get("MORPHEUS_BIN")
            if binary and Path(binary).is_file():
                return "native", f"MORPHEUS_BIN={binary}", None
            missing.append("MORPHEUS_BIN is not set to a morpheus executable")
        if self.prefer in ("auto", "docker"):
            backend = ContainerBackend(self.image, runtime="auto", platform=PLATFORM)
            if backend.runtime in ("docker", "podman"):
                avail = backend.availability()
                if avail.ok:
                    return "docker", f"{backend.runtime} image {self.image}", backend.runtime
                missing.append(f"{avail.detail}; build it with: docker build --platform {PLATFORM} -t {self.image} "
                               "containers/morpheus")  # fmt: skip
            else:
                missing.append("no running Docker or Podman")
        return "none", "; ".join(missing), None

    def capabilities(self) -> Capabilities:
        return Capabilities(
            name=self.name,
            mechanics="cellular Potts on a lattice (Morpheus)",
            dimensions=(2, 3),
            diffusion=True,
            intracellular=("sbml",),
            knobs=(),
            native_formats=("morpheusml model file",),
            notes="native models run unchanged (tier T0); ABM-IR models are translated (tier T1); dt_scale and "
            "dx_scale are refused, as refining a cellular Potts model needs other parameters rescaled; the seed is "
            "remapped for rng_streams",
        )

    def availability(self) -> Availability:
        """Available when ``MORPHEUS_BIN`` names a morpheus executable, or the container image is present."""
        kind, detail, _ = self._route()
        return Availability(kind != "none", detail, f"Morpheus {VERSION}" if kind != "none" else None)

    def load(self, model: Any) -> Any:
        """A MorpheusML model file (tier T0), or an ABM-IR model or file (tier T1, translated before running)."""
        from ...ir import IRModel, load_model

        if isinstance(model, IRModel):
            return model
        path = Path(model)
        if path.is_file() and path.suffix.lower() in (".yaml", ".yml", ".json"):
            return load_model(path)
        if not is_model(path):
            raise EngineError(f"{path} is not a MorpheusML model file")
        return path.resolve()

    def _scales(self, translated: bool) -> tuple[float, float]:
        """``(node_length, time_minutes)``: native models keep lattice units unless told otherwise."""
        default_node = DEFAULT_NODE_LENGTH if translated else 1.0
        return (self.node_length if self.node_length is not None else default_node), self.time_minutes

    def translation(self, model: Any) -> FidelityReport | None:
        """The fidelity report of translating an ABM-IR model to Morpheus, or ``None`` for a MorpheusML model."""
        from ...ir import IRModel
        from .translate import export_model

        loaded = self.load(model)
        if not isinstance(loaded, IRModel):
            return None
        node, minutes = self._scales(True)
        with tempfile.TemporaryDirectory() as tmp:
            return export_model(loaded, Path(tmp) / "model", node_length=node, time_minutes=minutes)

    def _export(self, model: Any, params: Mapping[str, Any]) -> tuple[Path, dict[str, Any]]:
        """Translate an ABM-IR model after its IR parameters; return the model file and the MorpheusML parameters."""
        from ...ir import apply_params, model_hash
        from .translate import export_model, translator_fingerprint

        ir_params = {k: v for k, v in params.items() if "/" not in k}
        xml_params = {k: v for k, v in params.items() if k not in ir_params}
        m = apply_params(model, ir_params)
        node, minutes = self._scales(True)
        key = f"{model_hash(m)[:16]}-{translator_fingerprint()[:12]}-{node:g}-{minutes:g}"
        out = self.cache_dir / "exports" / key
        if not (out / ".exported").exists():
            report = export_model(m, out, node_length=node, time_minutes=minutes)
            (out / "fidelity.md").write_text(report.to_markdown(), encoding="utf-8")
            (out / ".exported").write_text(m.name, encoding="utf-8")
        return out / MODEL_FILE, xml_params

    def refuse(self, model: Any, knobs: Knobs) -> list[str]:
        from ...ir import IRModel

        try:
            loaded = self.load(model)
            if not isinstance(loaded, IRModel):
                m = MorpheusModel.read(loaded)
                m.stop_time()
                m.lattice()
        except (EngineError, OSError, ValueError) as exc:
            return [str(exc)]
        reasons = super().refuse(model, knobs)
        if isinstance(loaded, IRModel) and loaded.space.dimensions == 3:
            reasons.append("ABM-IR models translate to 2D Morpheus models only, for now")
        return reasons

    # -- contract ----------------------------------------------------------------------------------------

    def prepare(
        self, model: Any, params: Mapping[str, Any], knobs: Knobs, seed: int, workdir: Path | None = None
    ) -> PreparedRun:
        """Copy the model into a run folder, set parameters, add the loggers, and build the command line.

        A parameter ``Time/StopTime`` sets the stop time; a parameter with a ``/`` is an element path whose
        ``value`` attribute is set, such as ``Global/Constant[@symbol='d']``. For an ABM-IR model, a dotted
        parameter such as ``cell_types[tumour].volume`` is applied to the model before it is translated.
        """
        from ...ir import IRModel, model_hash

        loaded = self.load(model)
        translated = isinstance(loaded, IRModel)
        ir_observables: list[dict[str, Any]] | None = None
        field_names: dict[str, str] = {}
        origin = (0.0, 0.0, 0.0)
        if translated:
            from .translate import symbol

            source, params = self._export(loaded, params)
            ir_observables = [o.model_dump(mode="json") for o in loaded.observables] or None
            field_names = {symbol(x.name): x.name for x in loaded.substrates}
            b = loaded.space.bounds
            origin = (b.x_min, b.y_min, 0.0)
        else:
            source = loaded
        node, minutes = self._scales(translated)
        kind, detail, runtime = self._route()
        if kind == "none":
            raise EngineError(f"Morpheus cannot run here: {detail}")
        digest = None
        if kind == "docker":
            from ...runtime.container import ContainerBackend

            digest = ContainerBackend(self.image, runtime=runtime or "docker", platform=PLATFORM).image_digest()
        identity = f"image {self.image}@{digest}" if kind == "docker" else detail
        options = {"save_every": self.save_every, "node_length": node, "time_minutes": minutes,
                   "threads": self.threads, "observables": self.observables}  # fmt: skip
        from ...digest import content_digest

        digest_of = model_hash(loaded) if translated else content_digest(source)
        request = RunRequest(self.name, digest_of, dict(params), int(seed), knobs.to_dict(),
                             engine_version=f"Morpheus {VERSION} ({identity})", image_digest=digest,
                             outputs_spec=canonical(options))  # fmt: skip
        if workdir is not None:
            work = Path(workdir) / request.key()[:16]
            if work.exists():
                shutil.rmtree(work)
            work.mkdir(parents=True)
        else:
            runs = self.cache_dir / "runs"
            runs.mkdir(parents=True, exist_ok=True)
            work = Path(tempfile.mkdtemp(prefix="run-", dir=runs))
        m = MorpheusModel.read(source)
        for path, value in params.items():
            if path == "Time/StopTime":
                m.set_stop_time(float(value))
            else:
                m.set(path, value)
        if self.save_every:
            every = self.save_every
        elif translated:
            every = loaded.experiment.save_interval / minutes
        else:
            every = max(m.stop_time() / 10.0, 1e-12)
        loggers = m.add_output_loggers(every, ("state",) if translated else ())
        m.write(work / MODEL_FILE)
        run_seed = self.seed_for(seed, knobs)
        args = ["-f", MODEL_FILE, "--seed", str(run_seed), "--outdir", "output", "--num-threads", str(self.threads),
                "--no-gnuplot"]  # fmt: skip
        argv = ["morpheus", *args] if kind == "docker" else [os.environ["MORPHEUS_BIN"], *args]
        context: dict[str, Any] = {
            "toolchain": kind,
            "runtime": runtime,
            "observables": self.observables or ir_observables or default_observables(m),
            "type_names": m.cell_types(),
            "ir_type_names": [c.name for c in loaded.cell_types] if translated else None,
            "field_names": field_names,
            "loggers": loggers,
            "dimensions": m.dimensions(),
            "scales": (node, minutes),
            "origin": origin,
        }
        return PreparedRun(self.name, request, workdir=work, argv=argv, image=self.image, timeout=self.timeout,
                           context=context)  # fmt: skip

    def run(self, prepared: PreparedRun, backend: Any = None) -> RunHandle:
        """Start a prepared run; a container run uses a container backend for the amd64 image."""
        from ...runtime.container import ContainerBackend
        from ...runtime.local import LocalBackend

        if prepared.context.get("toolchain") == "docker" and not isinstance(backend, ContainerBackend):
            backend = ContainerBackend(self.image, runtime=prepared.context.get("runtime") or "docker",
                                       platform=PLATFORM, timeout=self.timeout)  # fmt: skip
        return (backend or LocalBackend(timeout=self.timeout)).submit(prepared)

    def collect(self, handle: RunHandle) -> RunRecord:
        """Read the loggers' output into the common schema and compute the observables."""
        from ...ir import Observable
        from ...records.observables import compute_series

        if handle.status != "done" or handle.error:
            return self.record(handle)
        assert handle.prepared.workdir is not None
        ctx = handle.prepared.context
        out = handle.prepared.workdir / "output"
        node, minutes = ctx["scales"]
        try:
            outputs = read_output(out, ctx["type_names"], tuple(ctx["loggers"]), node, minutes,
                                  tuple(ctx["origin"]), dimensions=ctx["dimensions"])  # fmt: skip
        except (FileNotFoundError, ValueError, KeyError, StopIteration) as exc:
            handle.error = f"could not read Morpheus output: {exc}"
            return self.record(handle)
        if ctx.get("ir_type_names"):
            outputs.type_names = list(ctx["ir_type_names"])
        for frame in outputs.fields:
            frame.substrate = ctx.get("field_names", {}).get(frame.substrate, frame.substrate)
        compute_series([Observable.model_validate(o) for o in ctx["observables"]], outputs)
        handle.outputs = outputs
        warnings = []
        if node == 1.0 and minutes == 1.0 and not ctx.get("ir_type_names"):
            warnings.append(
                "outputs are in lattice nodes and the model's time units (node_length and time_minutes are 1)"
            )
        return self.record(handle, output_paths={"output": str(out)}, warnings=warnings)
