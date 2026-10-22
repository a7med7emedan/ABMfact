"""PhysiCell and PhysiBoSS under the adapter contract.

Native mode (fidelity tier T0) runs a PhysiCell project as it is. For each run the engine copies the
project's ``config/`` folder into a work directory and edits only the settings that a run controls: the
seed, the three time steps (times ``dt_scale``), the voxel size (times ``dx_scale``), the output folder
and interval, SVG snapshots (off; they do not affect dynamics), and any parameters the request sets. The
binary is built once per source hash (see :mod:`.build`) and the run record pins the PhysiCell version
and, for container builds, the image digest.

An ABM-IR model (fidelity tier T1) is exported onto PhysiCell's template project first (see
:mod:`.translate`), once per model hash; ABM-IR parameter paths in the request change the model before
export, and settings paths (``overall/max_time``, ``user_parameters.<name>``) change the exported XML.

Outputs are read into the common schema. Observables are the ones passed as ``observables`` (ABM-IR
observable specs), or by default: ``cells``, ``live``, ``dead``, ``live_<type>`` for each cell type and
``<substrate>_mean`` for each substrate.
"""

from __future__ import annotations

import re
import shutil
import tempfile
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from ...knobs import Knobs
from ...records import RunRecord, RunRequest
from ...records.record import canonical
from ..base import Availability, Capabilities, Engine, EngineError, PreparedRun, RunHandle
from .build import BINARY, IMAGE, Toolchain, build_project, detect_toolchain, is_project, source_hash
from .outputs import read_output
from .settings import SettingsXML

VERSION = "1.14.2"
SETTINGS = Path("config") / "PhysiCell_settings.xml"


def default_observables(settings: SettingsXML) -> list[dict[str, Any]]:
    def slug(name: str) -> str:
        return re.sub(r"\W+", "_", name).strip("_").lower() or "x"

    obs: list[dict[str, Any]] = [
        {"name": "cells", "kind": "cell_count"},
        {"name": "live", "kind": "live_count"},
        {"name": "dead", "kind": "dead_count"},
    ]
    for cd in settings.cell_definitions():
        name = cd.get("name", "")
        obs.append({"name": f"live_{slug(name)}", "kind": "live_count", "cell_type": name})
    for v in settings.substrates():
        name = v.get("name", "")
        obs.append({"name": f"{slug(name)}_mean", "kind": "substrate_mean", "substrate": name})
    return obs


class PhysiCellEngine(Engine):
    name = "physicell"
    description = "PhysiCell 1.14.2 and PhysiBoSS projects, built natively or in a container"

    def __init__(
        self,
        toolchain: str = "auto",
        image: str = IMAGE,
        cache_dir: str | Path | None = None,
        source: str | Path | None = None,
        threads: int | None = None,
        save_interval: float | None = None,
        observables: Sequence[Mapping[str, Any]] | None = None,
        timeout: float | None = None,
        build_jobs: int = 4,
        reader: str = "auto",
        base: str | Path | None = None,
    ):
        self.prefer = toolchain
        self.image = image
        self.cache_dir = Path(cache_dir) if cache_dir else Path.home() / ".cache" / "abmfact" / "physicell"
        self.source = Path(source) if source else None
        self.threads = threads
        self.save_interval = save_interval
        self.observables = [dict(o) for o in observables] if observables else None
        self.timeout = timeout
        self.build_jobs = build_jobs
        self.reader = reader
        self.base = Path(base) if base else None
        self._toolchain: Toolchain | None = None

    # -- discovery ---------------------------------------------------------------------------------------

    @property
    def toolchain(self) -> Toolchain:
        if self._toolchain is None:
            self._toolchain = detect_toolchain(self.prefer, self.image)
        return self._toolchain

    def capabilities(self) -> Capabilities:
        return Capabilities(
            name=self.name,
            mechanics="off-lattice centre-based (PhysiCell)",
            dimensions=(2, 3),
            diffusion=True,
            intracellular=("maboss", "sbml"),
            knobs=("dt_scale", "dx_scale"),
            native_formats=("physicell project folder",),
            notes="native projects run unchanged (tier T0); the seed is remapped for rng_streams",
        )

    def availability(self) -> Availability:
        tc = self.toolchain
        return Availability(tc.ok, tc.detail, f"PhysiCell {VERSION}" if tc.ok else None)

    def load(self, model: Any) -> Any:
        """A project folder (tier T0), or an ABM-IR model or file (tier T1, exported before each run)."""
        from ...ir import IRModel, load_model

        if isinstance(model, IRModel):
            return model
        path = Path(model)
        if path.is_file() and path.suffix.lower() in (".yaml", ".yml", ".json"):
            return load_model(path)
        if not is_project(path):
            raise EngineError(f"{path} is not a PhysiCell project (needs main.cpp, Makefile and config/)")
        return path.resolve()

    def _export(self, model: Any, params: Mapping[str, Any]) -> tuple[Path, dict[str, Any]]:
        """Export an IR model, after applying its IR parameters; return the project and the XML parameters."""
        from ...ir import apply_params, model_hash
        from .translate import export_project

        ir_params = {k: v for k, v in params.items() if "/" not in k and not k.startswith("user_parameters.")}
        xml_params = {k: v for k, v in params.items() if k not in ir_params}
        m = apply_params(model, ir_params)
        out = self.cache_dir / "exports" / model_hash(m)[:16]
        if not (out / ".exported").exists():
            report = export_project(m, out, base=self.base)
            (out / "fidelity.md").write_text(report.to_markdown(), encoding="utf-8")
            (out / ".exported").write_text(m.name, encoding="utf-8")
        return out, xml_params

    def refuse(self, model: Any, knobs: Knobs) -> list[str]:
        try:
            self.load(model)
        except EngineError as exc:
            return [str(exc)]
        return super().refuse(model, knobs)

    # -- contract ----------------------------------------------------------------------------------------

    def _identity(self) -> tuple[str, str | None]:
        """What pins the build: the image digest in a container, the compiler path natively."""
        tc = self.toolchain
        if tc.kind == "docker":
            from ...runtime.container import ContainerBackend

            digest = ContainerBackend(self.image, runtime=tc.runtime or "docker").image_digest()
            return f"image {self.image}@{digest}", digest
        return f"compiler {tc.compiler}", None

    def prepare(
        self, model: Any, params: Mapping[str, Any], knobs: Knobs, seed: int, workdir: Path | None = None
    ) -> PreparedRun:
        loaded = self.load(model)
        model_observables: list[dict[str, Any]] | None = None
        if isinstance(loaded, Path):
            project, xml_params = loaded, dict(params)
        else:
            project, xml_params = self._export(loaded, params)
            model_observables = [o.model_dump(mode="json") for o in loaded.observables] or None
        settings = SettingsXML.read(project / SETTINGS)
        maboss = settings.find("cell_definitions/cell_definition/phenotype/intracellular[@type='maboss']") is not None
        identity, digest = self._identity()
        run_seed = self.seed_for(seed, knobs)
        observables = self.observables or model_observables
        options = {"threads": self.threads, "save_interval": self.save_interval, "observables": observables}
        request = RunRequest(
            self.name,
            source_hash(project, self.toolchain, "") + ":" + _config_hash(project),
            dict(params),
            int(seed),
            knobs.to_dict(),
            engine_version=f"PhysiCell {VERSION} ({identity})",
            image_digest=digest,
            outputs_spec=canonical(options),
        )
        build = build_project(project, self.toolchain, self.cache_dir, identity, source=self.source, maboss=maboss,
                              jobs=self.build_jobs)  # fmt: skip
        # Each run gets its own folder: under ``workdir`` named by the request, else a fresh temporary one.
        if workdir is not None:
            work = Path(workdir) / request.key()[:16]
            if work.exists():
                shutil.rmtree(work)
            work.mkdir(parents=True)
        else:
            work = Path(tempfile.mkdtemp(prefix="run-", dir=self._runs_dir()))
        shutil.copytree(project / "config", work / "config")
        (work / "output").mkdir(parents=True)
        s = SettingsXML.read(work / SETTINGS)
        s.set_seed(run_seed)
        if knobs.dt_scale != 1.0:
            s.scale_time_steps(knobs.dt_scale)
        if knobs.dx_scale != 1.0:
            s.scale_voxels(knobs.dx_scale)
        if self.threads is not None:
            s.set_threads(self.threads)
        s.set_output("output", self.save_interval, svg=False)
        for key, value in xml_params.items():
            s.set_param(key, value)
        s.write(work / SETTINGS)
        if self.toolchain.kind == "docker":
            argv = [f"/build/{BINARY}", SETTINGS.as_posix()]
            context: dict[str, Any] = {"mounts": {str(build): "/build"}, "toolchain": "docker"}
        else:
            argv = [str(build / BINARY), SETTINGS.as_posix()]
            context = {"toolchain": "native"}
        context["observables"] = observables
        steps = s.time_steps()
        if steps["dt_diffusion"] > steps["dt_mechanics"] * (1 + 1e-9):
            context["warnings"] = [
                f"dt_diffusion ({steps['dt_diffusion']:g}) exceeds dt_mechanics ({steps['dt_mechanics']:g}): PhysiCell "
                "runs mechanics at most once per diffusion step with a step fixed at dt_mechanics, so cells move "
                "too slowly"
            ]
        return PreparedRun(self.name, request, workdir=work, argv=argv, image=self.image, timeout=self.timeout,
                           context=context)  # fmt: skip

    def _runs_dir(self) -> Path:
        d = self.cache_dir / "runs"
        d.mkdir(parents=True, exist_ok=True)
        return d

    def run(self, prepared: PreparedRun, backend: Any = None) -> RunHandle:
        from ...runtime.container import ContainerBackend
        from ...runtime.local import LocalBackend

        if prepared.context.get("toolchain") == "docker" and not isinstance(backend, ContainerBackend):
            backend = ContainerBackend(self.image, runtime=self.toolchain.runtime or "docker", timeout=self.timeout)
        return (backend or LocalBackend(timeout=self.timeout)).submit(prepared)

    def collect(self, handle: RunHandle) -> RunRecord:
        from ...ir import Observable
        from ...records.observables import compute_series

        if handle.status != "done" or handle.error:
            return self.record(handle)
        assert handle.prepared.workdir is not None
        work = handle.prepared.workdir
        try:
            outputs = read_output(work / "output", reader=self.reader)
        except (FileNotFoundError, ValueError, KeyError) as exc:
            handle.error = f"could not read PhysiCell output: {exc}"
            return self.record(handle)
        specs = handle.prepared.context.get("observables") or default_observables(SettingsXML.read(work / SETTINGS))
        compute_series([Observable.model_validate(o) for o in specs], outputs)
        handle.outputs = outputs
        record = self.record(
            handle,
            output_paths={"output": str(work / "output")},
            warnings=list(handle.prepared.context.get("warnings", [])),
        )
        if handle.image_digest and record.request.image_digest is None:
            record.warnings.append(f"ran in image {handle.image_digest}")
        return record


def _config_hash(project: Path) -> str:
    import hashlib

    h = hashlib.sha256()
    for f in sorted(p for p in (project / "config").rglob("*") if p.is_file()):
        h.update(f.relative_to(project).as_posix().encode())
        h.update(f.read_bytes())
    return h.hexdigest()[:16]
