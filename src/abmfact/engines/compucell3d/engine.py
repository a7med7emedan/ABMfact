"""CompuCell3D under the adapter contract.

Native mode (fidelity tier T0) runs a CompuCell3D project as it is. For each run the engine copies the
project into a run folder and edits only what a run controls in its CC3DML model: the seed in
``Potts/RandomSeed``, any parameters given as element paths (such as ``Potts/Steps``), and the
``CenterOfMass`` plugin, which tracks centroids and changes no dynamics. A small driver then starts
CompuCell3D's own runner on the project with one extra steppable registered, which writes each cell's centre,
type and volume, and every chemical field, at MCS 0, every ``save_every`` steps and the last step. The
project's Python code runs as written.

CompuCell3D works in lattice sites and Monte Carlo steps (MCS). Outputs are reported in micron and minutes
through ``site_length`` and ``mcs_minutes``, both 1 by default, which keeps the native units. A Monte Carlo
step is not a time step, so ``dt_scale`` and ``dx_scale`` are refused; the seed is remapped for
``rng_streams``. CompuCell3D reads a seed of 0 as "seed from the clock", so seed 0 is passed as ``ZERO_SEED``.

CompuCell3D 4.10.0 runs either with a Python that has the ``cc3d`` package (``CC3D_PYTHON``), or in the image
built from ``containers/compucell3d/Dockerfile``. That image is amd64 only, as CompuCell3D publishes no
linux-aarch64 build, so on Apple silicon it runs under emulation.
"""

from __future__ import annotations

import os
import shutil
import tempfile
from collections.abc import Mapping, Sequence
from importlib import resources
from pathlib import Path
from typing import Any

from ...knobs import Knobs
from ...records import FidelityReport, RunRecord, RunRequest
from ...records.record import canonical
from ..base import Availability, Capabilities, Engine, EngineError, PreparedRun, RunHandle
from .outputs import read_output
from .project import CC3DProject, is_project

VERSION = "4.10.0"
IMAGE = f"abmfact/compucell3d:{VERSION}"
PLATFORM = "linux/amd64"
DRIVER = "abmfact_cc3d_driver.py"
# CompuCell3D 4.10.0 (Simulator::getRNGSeed) seeds from the clock when Potts/RandomSeed is 0, and it parses the
# seed with stoi. Seed 0 is passed as the largest seed it accepts instead, which no other seed maps to: larger
# seeds are refused.
ZERO_SEED = 0x7FFF_FFFF
#: Micron per site for ABM-IR exports: a cell of PhysiCell's default volume covers about 55 sites in 2D.
DEFAULT_SITE_LENGTH = 2.0


def driver_source() -> str:
    """The driver script, shipped with the package and copied into each run folder."""
    return (resources.files(__package__) / "driver.py.in").read_text(encoding="utf-8")


def default_observables(project: CC3DProject) -> list[dict[str, Any]]:
    """``cells``, then ``cells_<type>`` for each cell type, then ``<field>_mean`` for each chemical field."""
    obs: list[dict[str, Any]] = [{"name": "cells", "kind": "live_count"}]
    for t in project.cell_types():
        obs.append({"name": f"cells_{_slug(t)}", "kind": "live_count", "cell_type": t})
    for f in project.fields():
        obs.append({"name": f"{_slug(f)}_mean", "kind": "substrate_mean", "substrate": f})
    return obs


def _slug(name: str) -> str:
    import re

    s = re.sub(r"\W+", "_", name).strip("_").lower()
    return s if s and not s[0].isdigit() else f"_{s}"


def _log_error(handle: RunHandle) -> str:
    """The last error line in the run's log, so a failure says why; CompuCell3D can fail with exit status 0."""
    log = handle.log_path or (handle.prepared.workdir / "run.log" if handle.prepared.workdir else None)
    if log is None or not Path(log).is_file():
        return ""
    lines = Path(log).read_text(encoding="utf-8", errors="replace").splitlines()
    errors = [ln.strip() for ln in lines if "Error" in ln and not ln.strip().startswith("File ")]
    return f"; CompuCell3D said: {errors[-1]}" if errors else ""


def _project_hash(folder: Path) -> str:
    import hashlib

    from ...digest import content_bytes

    h = hashlib.sha256()
    for f in sorted(p for p in folder.rglob("*") if p.is_file() and "__pycache__" not in p.parts):
        h.update(f.relative_to(folder).as_posix().encode())
        h.update(content_bytes(f))
    return h.hexdigest()


class CompuCell3DEngine(Engine):
    """Runs CompuCell3D 4.10.0 projects as they are (tier T0).

    Args:
        toolchain: ``"auto"`` (a native ``CC3D_PYTHON``, then the container), ``"native"`` or ``"docker"``.
        image: The container image.
        cache_dir: The folder for run folders; default ``~/.cache/abmfact/compucell3d``.
        save_every: Save outputs every this many MCS; default a tenth of the run, at least 1.
        site_length: Micron per lattice site: 1 for native projects (lattice units) and 2 for ABM-IR exports,
            unless given.
        mcs_minutes: Minutes per Monte Carlo step, used to report times.
        observables: ABM-IR observable specs to compute; ``None`` uses :func:`default_observables`.
        timeout: The time limit for each run, in seconds; ``None`` for no limit.
    """

    name = "compucell3d"
    description = "CompuCell3D 4.10.0 projects (cellular Potts), natively or in a container"

    def __init__(
        self,
        toolchain: str = "auto",
        image: str = IMAGE,
        cache_dir: str | Path | None = None,
        save_every: int | None = None,
        site_length: float | None = None,
        mcs_minutes: float = 1.0,
        observables: Sequence[Mapping[str, Any]] | None = None,
        timeout: float | None = None,
    ):
        self.prefer = toolchain
        self.image = image
        self.cache_dir = Path(cache_dir) if cache_dir else Path.home() / ".cache" / "abmfact" / "compucell3d"
        self.save_every = save_every
        self.site_length = float(site_length) if site_length is not None else None
        self.mcs_minutes = float(mcs_minutes)
        self.observables = [dict(o) for o in observables] if observables else None
        self.timeout = timeout

    # -- discovery ---------------------------------------------------------------------------------------

    def _route(self) -> tuple[str, str, str | None]:
        """``(kind, detail, runtime)``: ``native`` with ``CC3D_PYTHON``, ``docker`` with the image, or ``none``."""
        from ...runtime.container import ContainerBackend

        missing = []
        if self.prefer in ("auto", "native"):
            python = os.environ.get("CC3D_PYTHON")
            if python and Path(python).is_file():
                return "native", f"CC3D_PYTHON={python}", None
            missing.append("CC3D_PYTHON is not set to a Python with the cc3d package")
        if self.prefer in ("auto", "docker"):
            backend = ContainerBackend(self.image, runtime="auto", platform=PLATFORM)
            if backend.runtime in ("docker", "podman"):
                avail = backend.availability()
                if avail.ok:
                    return "docker", f"{backend.runtime} image {self.image}", backend.runtime
                missing.append(f"{avail.detail}; build it with: docker build --platform {PLATFORM} -t {self.image} "
                               "containers/compucell3d")  # fmt: skip
            else:
                missing.append("no running Docker or Podman")
        return "none", "; ".join(missing), None

    def capabilities(self) -> Capabilities:
        return Capabilities(
            name=self.name,
            mechanics="cellular Potts on a lattice (CompuCell3D)",
            dimensions=(2, 3),
            diffusion=True,
            intracellular=("sbml", "maboss"),
            knobs=(),
            native_formats=("compucell3d project folder",),
            notes="native projects run unchanged (tier T0); a Monte Carlo step is not a time step, so dt_scale and "
            "dx_scale are refused; the seed is remapped for rng_streams, and seed 0, which CompuCell3D reads as "
            "a clock seed, is passed as 2147483647",
        )

    def seed_for(self, seed: int, knobs: Knobs) -> int:
        """The seed written to ``Potts/RandomSeed``: never 0, which CompuCell3D would replace with a clock seed."""
        value = super().seed_for(seed, knobs)
        if not 0 <= value < ZERO_SEED:
            raise EngineError(f"CompuCell3D takes seeds from 0 to {ZERO_SEED - 1}, not {value}")
        return value or ZERO_SEED

    def availability(self) -> Availability:
        """Available when ``CC3D_PYTHON`` names a Python with cc3d, or the container image is present."""
        kind, detail, _ = self._route()
        return Availability(kind != "none", detail, f"CompuCell3D {VERSION}" if kind != "none" else None)

    def load(self, model: Any) -> Any:
        """A CompuCell3D project folder (tier T0), or an ABM-IR model or file (tier T1, exported before running)."""
        from ...ir import IRModel, load_model

        if isinstance(model, IRModel):
            return model
        path = Path(model)
        if path.is_file() and path.suffix.lower() in (".yaml", ".yml", ".json"):
            return load_model(path)
        if not is_project(path):
            raise EngineError(f"{path} is not a CompuCell3D project (a folder with one .cc3d file)")
        return path.resolve()

    def _scales(self, translated: bool) -> tuple[float, float]:
        """``(site_length, mcs_minutes)``: native projects keep lattice units unless told otherwise."""
        default_site = DEFAULT_SITE_LENGTH if translated else 1.0
        return (self.site_length if self.site_length is not None else default_site), self.mcs_minutes

    def translation(self, model: Any) -> FidelityReport | None:
        """The fidelity report of exporting an ABM-IR model to a CompuCell3D project, or ``None`` for a project."""
        from ...ir import IRModel
        from .translate import export_project

        loaded = self.load(model)
        if not isinstance(loaded, IRModel):
            return None
        site, mcs = self._scales(True)
        with tempfile.TemporaryDirectory() as tmp:
            return export_project(loaded, Path(tmp) / "project", site_length=site, mcs_minutes=mcs)

    def _export(self, model: Any, params: Mapping[str, Any]) -> tuple[Path, dict[str, Any]]:
        """Export an ABM-IR model after its IR parameters; return the project and the CC3DML parameters."""
        from ...ir import apply_params, model_hash
        from .translate import export_project, exporter_fingerprint

        ir_params = {k: v for k, v in params.items() if "/" not in k}
        xml_params = {k: v for k, v in params.items() if k not in ir_params}
        m = apply_params(model, ir_params)
        site, mcs = self._scales(True)
        key = f"{model_hash(m)[:16]}-{exporter_fingerprint()[:12]}-{site:g}-{mcs:g}"
        out = self.cache_dir / "exports" / key
        if not (out / ".exported").exists():
            report = export_project(m, out, site_length=site, mcs_minutes=mcs)
            (out / "fidelity.md").write_text(report.to_markdown(), encoding="utf-8")
            (out / ".exported").write_text(m.name, encoding="utf-8")
        return out, xml_params

    def refuse(self, model: Any, knobs: Knobs) -> list[str]:
        from ...ir import IRModel

        try:
            loaded = self.load(model)
            if not isinstance(loaded, IRModel):
                CC3DProject.read(loaded).steps()
        except (EngineError, OSError, ValueError) as exc:
            return [str(exc)]
        reasons = super().refuse(model, knobs)
        if isinstance(loaded, IRModel) and loaded.space.dimensions == 3:
            reasons.append("ABM-IR models export to 2D CompuCell3D projects only, for now")
        return reasons

    # -- contract ----------------------------------------------------------------------------------------

    def prepare(
        self, model: Any, params: Mapping[str, Any], knobs: Knobs, seed: int, workdir: Path | None = None
    ) -> PreparedRun:
        """Copy the project into a run folder, set the seed and parameters, and add the output driver.

        Every parameter is an element path in the CC3DML model, such as ``Potts/Steps`` or
        ``Plugin[@Name='Volume']/VolumeEnergyParameters[@CellType='Condensing']``; the element's text is set.
        """
        from ...ir import IRModel

        loaded = self.load(model)
        translated = isinstance(loaded, IRModel)
        ir_observables: list[dict[str, Any]] | None = None
        if translated:
            source, params = self._export(loaded, params)
            ir_observables = [o.model_dump(mode="json") for o in loaded.observables] or None
            type_names = [c.name for c in loaded.cell_types]
            b = loaded.space.bounds
            origin = (b.x_min, b.y_min, b.z_min if loaded.space.dimensions == 3 else 0.0)
        else:
            source = loaded
        site, mcs = self._scales(translated)
        kind, detail, runtime = self._route()
        if kind == "none":
            raise EngineError(f"CompuCell3D cannot run here: {detail}")
        digest = None
        if kind == "docker":
            from ...runtime.container import ContainerBackend

            digest = ContainerBackend(self.image, runtime=runtime or "docker", platform=PLATFORM).image_digest()
        identity = f"image {self.image}@{digest}" if kind == "docker" else detail
        options = {"save_every": self.save_every, "site_length": site, "mcs_minutes": mcs,
                   "observables": self.observables}  # fmt: skip
        request = RunRequest(self.name, _project_hash(source), dict(params), int(seed), knobs.to_dict(),
                             engine_version=f"CompuCell3D {VERSION} ({identity})", image_digest=digest,
                             outputs_spec=canonical(options))  # fmt: skip
        if workdir is not None:
            work = Path(workdir) / request.key()[:16]
            if work.exists():
                shutil.rmtree(work)
            work.parent.mkdir(parents=True, exist_ok=True)
        else:
            runs = self.cache_dir / "runs"
            runs.mkdir(parents=True, exist_ok=True)
            work = Path(tempfile.mkdtemp(prefix="run-", dir=runs))
            work.rmdir()
        shutil.copytree(source, work / "project", ignore=shutil.ignore_patterns("__pycache__"))
        project = CC3DProject.read(work / "project")
        project.set_seed(self.seed_for(seed, knobs))
        for path, value in params.items():
            project.set(path, value)
        project.ensure_center_of_mass()
        project.write_xml()
        (work / DRIVER).write_text(driver_source(), encoding="utf-8")
        if self.save_every:
            every = self.save_every
        elif translated:
            every = max(1, round(loaded.experiment.save_interval / mcs))
        else:
            every = max(1, project.steps() // 10)
        cc3d_file = project.cc3d_file.relative_to(work).as_posix()
        args = [DRIVER, cc3d_file, "output", str(every), *project.fields()]
        context: dict[str, Any] = {
            "toolchain": kind,
            "observables": self.observables or ir_observables or default_observables(project),
            "type_names": project.cell_types(),
            "ir_type_names": type_names if translated else None,
            "scales": (site, mcs),
            "origin": origin if translated else (0.0, 0.0, 0.0),
        }
        if kind == "docker":
            argv = ["python", *args]
            context["runtime"] = runtime
        else:
            argv = [os.environ["CC3D_PYTHON"], *args]
        # CompuCell3D writes settings under the home folder, which a container user may not own.
        env = {"HOME": "/tmp", "QT_QPA_PLATFORM": "offscreen"}  # noqa: S108 - a folder inside the container
        return PreparedRun(self.name, request, workdir=work, argv=argv, env=env, image=self.image,
                           timeout=self.timeout, context=context)  # fmt: skip

    def run(self, prepared: PreparedRun, backend: Any = None) -> RunHandle:
        """Start a prepared run; a container run uses a container backend for the amd64 image."""
        from ...runtime.container import ContainerBackend
        from ...runtime.local import LocalBackend

        if prepared.context.get("toolchain") == "docker" and not isinstance(backend, ContainerBackend):
            backend = ContainerBackend(self.image, runtime=prepared.context.get("runtime") or "docker",
                                       platform=PLATFORM, timeout=self.timeout)  # fmt: skip
        return (backend or LocalBackend(timeout=self.timeout)).submit(prepared)

    def collect(self, handle: RunHandle) -> RunRecord:
        """Read the driver's output into the common schema and compute the observables."""
        from ...ir import Observable
        from ...records.observables import compute_series

        if handle.status != "done" or handle.error:
            return self.record(handle)
        assert handle.prepared.workdir is not None
        out = handle.prepared.workdir / "output"
        site, mcs = handle.prepared.context["scales"]
        try:
            outputs = read_output(out, handle.prepared.context["type_names"], site, mcs,
                                  tuple(handle.prepared.context.get("origin", (0.0, 0.0, 0.0))))  # fmt: skip
        except (FileNotFoundError, ValueError, KeyError) as exc:
            handle.error = f"could not read CompuCell3D output: {exc}{_log_error(handle)}"
            return self.record(handle)
        if handle.prepared.context.get("ir_type_names"):
            outputs.type_names = list(handle.prepared.context["ir_type_names"])
        compute_series([Observable.model_validate(o) for o in handle.prepared.context["observables"]], outputs)
        handle.outputs = outputs
        warnings = []
        if site == 1.0 and mcs == 1.0 and not handle.prepared.context.get("ir_type_names"):
            warnings.append("outputs are in lattice sites and Monte Carlo steps (site_length and mcs_minutes are 1)")
        return self.record(handle, output_paths={"output": str(out)}, warnings=warnings)
