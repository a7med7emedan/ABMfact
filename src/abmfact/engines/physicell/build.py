"""Find a way to build PhysiCell, and build a project once per source hash.

Two routes, tried in this order:

* **native**: a GNU ``g++`` with OpenMP on this machine. ``PHYSICELL_CPP`` names it; otherwise
  ``g++-15`` down to ``g++-11`` (Homebrew's names on macOS) and plain ``g++`` are tried, and the one used
  must compile ``-fopenmp``. Apple's ``g++`` is clang without OpenMP and is skipped. The PhysiCell sources
  come from the project itself (a full PhysiCell tree) or from ``PHYSICELL_SOURCE``.
* **docker**: the image from ``containers/physicell/Dockerfile`` (PhysiCell 1.14.2 sources in
  ``/opt/PhysiCell``), run with Docker or Podman. The image digest pins the build.

A project is the folder PhysiCell's ``make <sample>`` populates: ``main.cpp``, ``Makefile``,
``custom_modules/`` and ``config/``. Its build folder gets those files plus the PhysiCell core
(``BioFVM``, ``core``, ``modules``, ``addons``), and ``make`` runs with portable compiler flags: the
template's ``-march=native -mfpmath=both -m64`` do not compile on 64-bit ARM, so they are replaced.
"""

from __future__ import annotations

import hashlib
import os
import shutil
import subprocess
import sys
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ...digest import content_bytes
from ..base import EngineError

IMAGE = "abmfact/physicell:1.14.2"
BINARY = "abmfact-physicell"
PROJECT_FILES = ("main.cpp", "Makefile")
PROJECT_DIRS = ("custom_modules",)
CORE_DIRS = ("BioFVM", "core", "modules", "addons")

BUILD_SCRIPT = """#!/bin/sh
# Written by ABMfact: build a PhysiCell project against the sources in $PHYSICELL_SOURCE.
set -eu
if [ -n "${PHYSICELL_SOURCE:-}" ]; then
  for d in BioFVM core modules addons; do
    [ -e "$d" ] || cp -R "$PHYSICELL_SOURCE/$d" .
  done
fi
case "$(uname -m)" in
  aarch64|arm64) FLAGS="-O3 -fomit-frame-pointer -fopenmp -std=c++11" ;;
  *) FLAGS="-march=x86-64 -O3 -fomit-frame-pointer -mfpmath=both -fopenmp -m64 -std=c++11" ;;
esac
if [ "${ABMFACT_MABOSS:-0}" = "1" ]; then
  make Compile_MaBoSS
fi
make -j"${ABMFACT_JOBS:-4}" CFLAGS="$FLAGS" PROGRAM_NAME=abmfact-physicell \
  ${PHYSICELL_CPP:+PHYSICELL_CPP=$PHYSICELL_CPP}
"""

Runner = Callable[..., Any]


def native_sources_set() -> bool:
    """True when ``PHYSICELL_SOURCE`` points at a PhysiCell tree, so native builds work for any project."""
    source = os.environ.get("PHYSICELL_SOURCE")
    if not source:
        return False
    return all((Path(source) / d).is_dir() for d in CORE_DIRS[:3])


@dataclass(frozen=True)
class Toolchain:
    kind: str  # native, docker or none
    detail: str
    compiler: str | None = None
    image: str | None = None
    runtime: str | None = None

    @property
    def ok(self) -> bool:
        return self.kind != "none"


def _is_gnu_with_openmp(compiler: str, runner: Runner) -> bool:
    try:
        version = runner([compiler, "--version"], capture_output=True, text=True, timeout=30, check=False)
        if "Free Software Foundation" not in (version.stdout or ""):
            return False
        test = runner(
            [compiler, "-fopenmp", "-x", "c++", "-", "-o", os.devnull],
            input="#include <omp.h>\nint main(){return omp_get_max_threads()>0?0:1;}\n",
            capture_output=True, text=True, timeout=60, check=False,
        )  # fmt: skip
    except (OSError, subprocess.TimeoutExpired):
        return False
    return test.returncode == 0


def find_compiler(which: Callable[[str], str | None] = shutil.which, runner: Runner = subprocess.run) -> str | None:
    """A GNU g++ that compiles OpenMP code, or ``None``."""
    names = [os.environ["PHYSICELL_CPP"]] if os.environ.get("PHYSICELL_CPP") else []
    names += [f"g++-{v}" for v in range(15, 10, -1)] + ["g++"]
    for name in names:
        path = which(name)
        if path and _is_gnu_with_openmp(path, runner):
            return path
    return None


def detect_toolchain(
    prefer: str = "auto",
    image: str = IMAGE,
    which: Callable[[str], str | None] = shutil.which,
    runner: Runner = subprocess.run,
) -> Toolchain:
    """The first usable route (native, then docker), or ``kind="none"`` with what is missing."""
    missing = []
    if prefer in ("auto", "native"):
        compiler = find_compiler(which, runner)
        if compiler:
            source = os.environ.get("PHYSICELL_SOURCE")
            where = f"sources in {source}" if source else "sources only from projects that are full PhysiCell trees"
            return Toolchain(
                "native", f"g++ with OpenMP: {compiler}; {where} (set PHYSICELL_SOURCE)", compiler=compiler
            )
        missing.append("no GNU g++ with OpenMP (set PHYSICELL_CPP, e.g. Homebrew g++-14)")
    if prefer in ("auto", "docker"):
        from ...runtime.container import ContainerBackend

        backend = ContainerBackend(image, runtime="auto", runner=runner, which=which)
        if backend.runtime in ("docker", "podman"):
            avail = backend.availability()
            if avail.ok:
                return Toolchain("docker", f"{backend.runtime} image {image}", image=image, runtime=backend.runtime)
            missing.append(f"{avail.detail}; build it with: docker build -t {image} containers/physicell")
        else:
            missing.append("no running Docker or Podman")
    return Toolchain("none", "; ".join(missing))


def is_project(path: Path) -> bool:
    return all((path / f).is_file() for f in PROJECT_FILES) and (path / "config").is_dir()


def source_hash(project: Path, toolchain: Toolchain, identity: str) -> str:
    """Hash of everything that decides the binary: project sources, build script and toolchain identity."""
    h = hashlib.sha256()
    files = [project / f for f in PROJECT_FILES]
    for d in PROJECT_DIRS:
        if (project / d).is_dir():
            files += sorted(p for p in (project / d).rglob("*") if p.is_file())
    for f in files:
        h.update(f.relative_to(project).as_posix().encode())
        h.update(content_bytes(f))
    h.update(BUILD_SCRIPT.encode())
    h.update(f"{toolchain.kind}|{identity}".encode())
    return h.hexdigest()


def _locked(path: Path) -> Any:
    """An exclusive lock on ``path`` (POSIX), so parallel workers build a project only once."""
    fh = path.open("a+")
    if sys.platform != "win32":
        import fcntl

        fcntl.flock(fh, fcntl.LOCK_EX)
    return fh


def build_project(
    project: Path,
    toolchain: Toolchain,
    cache_dir: Path,
    identity: str,
    source: Path | None = None,
    maboss: bool = False,
    jobs: int = 4,
    runner: Runner = subprocess.run,
) -> Path:
    """Build ``project`` once per source hash and return the build folder (the binary is ``BINARY`` in it)."""
    if not toolchain.ok:
        raise EngineError(f"cannot build PhysiCell: {toolchain.detail}")
    key = source_hash(project, toolchain, identity)
    build = cache_dir / "builds" / key[:16]
    done = build / ".built"
    cache_dir.mkdir(parents=True, exist_ok=True)
    lock = _locked(cache_dir / f"build-{key[:16]}.lock")
    try:
        if done.exists():
            return build
        if build.exists():
            shutil.rmtree(build)
        build.mkdir(parents=True)
        for f in PROJECT_FILES:
            shutil.copy2(project / f, build / f)
        for d in PROJECT_DIRS:
            if (project / d).is_dir():
                shutil.copytree(project / d, build / d)
        if toolchain.kind == "native":
            src = source or (Path(os.environ["PHYSICELL_SOURCE"]) if os.environ.get("PHYSICELL_SOURCE") else None)
            if src is None and all((project / d).is_dir() for d in CORE_DIRS[:3]):
                src = project
            if src is None:
                raise EngineError("native builds need the PhysiCell sources: pass source= or set PHYSICELL_SOURCE")
            for d in CORE_DIRS:
                if (src / d).is_dir():
                    shutil.copytree(src / d, build / d)
        (build / "build.sh").write_text(BUILD_SCRIPT, encoding="utf-8")
        env = {"ABMFACT_JOBS": str(jobs), "ABMFACT_MABOSS": "1" if maboss else "0"}
        if toolchain.kind == "docker":
            assert toolchain.runtime and toolchain.image
            env_args = [a for k, v in env.items() for a in ("-e", f"{k}={v}")]
            user = ["--user", f"{os.getuid()}:{os.getgid()}"] if sys.platform.startswith("linux") else []
            argv = [toolchain.runtime, "run", "--rm", "-v", f"{build.resolve()}:/work", "-w", "/work", *user,
                    *env_args, toolchain.image, "sh", "build.sh"]  # fmt: skip
            proc_env = None
        else:
            argv = ["sh", "build.sh"]
            proc_env = {**os.environ, **env, "PHYSICELL_CPP": str(toolchain.compiler)}
        with (build / "build.log").open("w", encoding="utf-8") as log:
            proc = runner(argv, cwd=build, stdout=log, stderr=subprocess.STDOUT, env=proc_env, check=False)
        if proc.returncode != 0 or not (build / BINARY).exists():
            tail = (build / "build.log").read_text(encoding="utf-8", errors="replace").strip().splitlines()[-8:]
            raise EngineError(f"PhysiCell build failed in {build}: " + " | ".join(tail))
        done.write_text(identity, encoding="utf-8")
        return build
    finally:
        lock.close()
