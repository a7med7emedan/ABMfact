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
``rng_streams``.

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
from ...records import RunRecord, RunRequest
from ...records.record import canonical
from ..base import Availability, Capabilities, Engine, EngineError, PreparedRun, RunHandle
from .outputs import read_output
from .project import CC3DProject, is_project

VERSION = "4.10.0"
IMAGE = f"abmfact/compucell3d:{VERSION}"
PLATFORM = "linux/amd64"
DRIVER = "abmfact_cc3d_driver.py"


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
        site_length: Micron per lattice site, used to report positions and volumes.
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
        site_length: float = 1.0,
        mcs_minutes: float = 1.0,
        observables: Sequence[Mapping[str, Any]] | None = None,
        timeout: float | None = None,
    ):
        self.prefer = toolchain
        self.image = image
        self.cache_dir = Path(cache_dir) if cache_dir else Path.home() / ".cache" / "abmfact" / "compucell3d"
        self.save_every = save_every
        self.site_length = float(site_length)
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
            "dx_scale are refused; the seed is remapped for rng_streams",
        )

    def availability(self) -> Availability:
        """Available when ``CC3D_PYTHON`` names a Python with cc3d, or the container image is present."""
        kind, detail, _ = self._route()
        return Availability(kind != "none", detail, f"CompuCell3D {VERSION}" if kind != "none" else None)

    def load(self, model: Any) -> Path:
        """A CompuCell3D project folder; ABM-IR models are refused until the translation is added."""
        from ...ir import IRModel

        if isinstance(model, IRModel) or Path(str(model)).suffix.lower() in (".yaml", ".yml", ".json"):
            raise EngineError("ABM-IR models do not yet translate to CompuCell3D; run a CompuCell3D project folder")
        path = Path(model)
        if not is_project(path):
            raise EngineError(f"{path} is not a CompuCell3D project (a folder with one .cc3d file)")
        return path.resolve()

    def refuse(self, model: Any, knobs: Knobs) -> list[str]:
        try:
            project = CC3DProject.read(self.load(model))
            project.steps()
        except EngineError as exc:
            return [str(exc)]
        return super().refuse(model, knobs)

    # -- contract ----------------------------------------------------------------------------------------

    def prepare(
        self, model: Any, params: Mapping[str, Any], knobs: Knobs, seed: int, workdir: Path | None = None
    ) -> PreparedRun:
        """Copy the project into a run folder, set the seed and parameters, and add the output driver.

        Every parameter is an element path in the CC3DML model, such as ``Potts/Steps`` or
        ``Plugin[@Name='Volume']/VolumeEnergyParameters[@CellType='Condensing']``; the element's text is set.
        """
        source = self.load(model)
        kind, detail, runtime = self._route()
        if kind == "none":
            raise EngineError(f"CompuCell3D cannot run here: {detail}")
        digest = None
        if kind == "docker":
            from ...runtime.container import ContainerBackend

            digest = ContainerBackend(self.image, runtime=runtime or "docker", platform=PLATFORM).image_digest()
        identity = f"image {self.image}@{digest}" if kind == "docker" else detail
        options = {"save_every": self.save_every, "site_length": self.site_length, "mcs_minutes": self.mcs_minutes,
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
        every = self.save_every or max(1, project.steps() // 10)
        cc3d_file = project.cc3d_file.relative_to(work).as_posix()
        args = [DRIVER, cc3d_file, "output", str(every), *project.fields()]
        context: dict[str, Any] = {"toolchain": kind, "observables": self.observables or default_observables(project),
                                   "type_names": project.cell_types()}  # fmt: skip
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
        try:
            outputs = read_output(out, handle.prepared.context["type_names"], self.site_length, self.mcs_minutes)
        except (FileNotFoundError, ValueError, KeyError) as exc:
            handle.error = f"could not read CompuCell3D output: {exc}"
            return self.record(handle)
        compute_series([Observable.model_validate(o) for o in handle.prepared.context["observables"]], outputs)
        handle.outputs = outputs
        warnings = []
        if self.site_length == 1.0 and self.mcs_minutes == 1.0:
            warnings.append("outputs are in lattice sites and Monte Carlo steps (site_length and mcs_minutes are 1)")
        return self.record(handle, output_paths={"output": str(out)}, warnings=warnings)
