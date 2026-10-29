"""The container backend: run a prepared command inside a Docker, Podman or Apptainer image.

The run's work directory is mounted at ``/work`` and used as the working directory, so an engine
prepares the same files and relative command for local and container runs. The image digest is
recorded in the run handle, which pins the engine build in the run record.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

from ..engines.base import Availability, EngineError, PreparedRun, RunHandle
from .base import Backend
from .local import run_command

RUNTIMES = ("docker", "podman", "apptainer", "singularity")
MOUNT = "/work"
#: The repository whose ``containers/`` folder holds the engine images' Dockerfiles.
REPOSITORY = "https://github.com/a7med7emedan/ABMfact.git"

Runner = Callable[..., Any]


def _daemon_up(runtime: str, runner: Runner) -> bool:
    try:
        proc = runner([runtime, "info"], capture_output=True, text=True, timeout=30, check=False)
    except (OSError, subprocess.TimeoutExpired):
        return False
    return proc.returncode == 0


def build_command(image: str, folder: str, platform: str | None = None, version: str | None = None) -> str:
    """The ``docker build`` command for an engine image, with its Dockerfile fetched from GitHub.

    A release builds from its own tag, so the image matches the installed code. A development version has
    no tag and builds from main. Docker fetches ``containers/<folder>`` itself, so no checkout is needed.
    """
    if version is None:
        from .. import __version__

        version = __version__
    ref = f"v{version}" if re.fullmatch(r"\d+\.\d+\.\d+", version) else "main"
    option = f"--platform {platform} " if platform else ""
    return f'docker build {option}-t {image} "{REPOSITORY}#{ref}:containers/{folder}"'


def detect_runtime(which: Callable[[str], str | None] = shutil.which, runner: Runner = subprocess.run) -> str | None:
    """The first usable container runtime: Docker or Podman with a running daemon, or Apptainer."""
    for rt in RUNTIMES:
        if which(rt) is None:
            continue
        if rt in ("docker", "podman") and not _daemon_up(rt, runner):
            continue
        return rt
    return None


class ContainerBackend(Backend):
    """Run prepared commands in ``image``.

    ``runtime`` is ``"auto"`` or one of docker, podman, apptainer, singularity. For Apptainer the image is
    a ``.sif`` file. Extra read-only mounts come from ``prepared.context["mounts"]`` (host to container).
    """

    name = "container"

    def __init__(
        self,
        image: str,
        runtime: str = "auto",
        platform: str | None = None,
        timeout: float | None = None,
        runner: Runner = subprocess.run,
        which: Callable[[str], str | None] = shutil.which,
    ):
        self.image = image
        self.platform = platform
        self.timeout = timeout
        self._runner = runner
        self._which = which
        self.runtime = detect_runtime(which, runner) if runtime == "auto" else runtime
        self._digest: str | None = None

    @property
    def is_oci(self) -> bool:
        """Whether the runtime is Docker or Podman, which use OCI images rather than ``.sif`` files."""
        return self.runtime in ("docker", "podman")

    def availability(self) -> Availability:
        if self.runtime is None:
            return Availability(False, "no container runtime found (docker, podman or apptainer)")
        if self.is_oci:
            proc = self._runner(
                [self.runtime, "image", "inspect", self.image], capture_output=True, text=True, check=False
            )
            if proc.returncode != 0:
                return Availability(False, f"{self.runtime} is running but image {self.image} is not built or pulled")
        elif not Path(self.image).is_file():
            return Availability(False, f"image file {self.image} not found")
        return Availability(True, f"{self.runtime} image {self.image}", self.image_digest())

    def image_digest(self) -> str | None:
        """The registry digest when the image was pulled, else its local id; a file hash for ``.sif``."""
        if self._digest is not None:
            return self._digest
        if self.runtime is None:
            return None
        if self.is_oci:
            proc = self._runner(
                [self.runtime, "image", "inspect", "--format", "{{json .RepoDigests}}|{{.Id}}", self.image],
                capture_output=True,
                text=True,
                check=False,
            )
            if proc.returncode != 0:
                return None
            self._digest = parse_inspect(proc.stdout)
        elif Path(self.image).is_file():
            self._digest = "sha256:" + hashlib.sha256(Path(self.image).read_bytes()).hexdigest()
        return self._digest

    def command(self, prepared: PreparedRun) -> list[str]:
        """The full command line that runs ``prepared.argv`` in the image."""
        if prepared.argv is None or prepared.workdir is None:
            raise EngineError("the container backend runs native commands only, not in-process calls")
        if self.runtime is None:
            raise EngineError("no container runtime found (docker, podman or apptainer)")
        workdir = str(Path(prepared.workdir).resolve())
        mounts: dict[str, str] = dict(prepared.context.get("mounts", {}))
        if self.is_oci:
            cmd = [self.runtime, "run", "--rm", "-v", f"{workdir}:{MOUNT}", "-w", MOUNT]
            for host, inside in mounts.items():
                cmd += ["-v", f"{Path(host).resolve()}:{inside}:ro"]
            if self.platform:
                cmd += ["--platform", self.platform]
            if sys.platform.startswith("linux") and hasattr(os, "getuid"):
                cmd += ["--user", f"{os.getuid()}:{os.getgid()}"]
            for k, v in sorted(prepared.env.items()):
                cmd += ["-e", f"{k}={v}"]
            return [*cmd, self.image, *prepared.argv]
        cmd = [self.runtime, "exec", "--bind", f"{workdir}:{MOUNT}", "--pwd", MOUNT]
        for host, inside in mounts.items():
            cmd += ["--bind", f"{Path(host).resolve()}:{inside}:ro"]
        for k, v in sorted(prepared.env.items()):
            cmd += ["--env", f"{k}={v}"]
        return [*cmd, self.image, *prepared.argv]

    def submit(self, prepared: PreparedRun) -> RunHandle:
        handle = run_command(prepared, self.command(prepared), self.name, self.timeout)
        handle.image_digest = self.image_digest()
        return handle


def parse_inspect(text: str) -> str | None:
    """Parse ``{{json .RepoDigests}}|{{.Id}}``: the first repo digest's ``sha256:...``, else the image id."""
    digests_json, _, image_id = text.strip().partition("|")
    try:
        digests: Sequence[str] = json.loads(digests_json) or []
    except json.JSONDecodeError:
        digests = []
    for d in digests:
        if "@" in d:
            return d.split("@", 1)[1]
    return image_id.strip() or None
