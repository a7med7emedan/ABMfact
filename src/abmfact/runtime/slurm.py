"""The Slurm backend: write one job array for many prepared runs, and submit it only when asked.

Each array task reads its line from ``manifest.tsv`` (task index, work directory, command), changes to
the work directory and runs the command with output in ``run.log``. Collect the runs with the engine
once the array has finished.
"""

from __future__ import annotations

import re
import shlex
import shutil
import subprocess
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

from ..engines.base import Availability, EngineError, PreparedRun, RunHandle
from .base import Backend
from .container import ContainerBackend

Runner = Callable[..., Any]


class SlurmBackend(Backend):
    name = "slurm"

    def __init__(
        self,
        partition: str | None = None,
        time: str = "01:00:00",
        mem: str = "4G",
        cpus_per_task: int = 1,
        account: str | None = None,
        max_parallel: int | None = None,
        job_name: str = "abmfact",
        directives: Sequence[str] = (),
        container: ContainerBackend | None = None,
        runner: Runner = subprocess.run,
    ):
        self.partition = partition
        self.time = time
        self.mem = mem
        self.cpus_per_task = int(cpus_per_task)
        self.account = account
        self.max_parallel = max_parallel
        self.job_name = job_name
        self.directives = list(directives)
        self.container = container
        self._runner = runner

    def availability(self) -> Availability:
        if shutil.which("sbatch") is None:
            return Availability(False, "sbatch is not on PATH; scripts can still be written and copied to a cluster")
        return Availability(True, "sbatch found")

    def _command(self, prepared: PreparedRun) -> list[str]:
        if prepared.argv is None or prepared.workdir is None:
            raise EngineError("Slurm runs native commands only, not in-process calls")
        return self.container.command(prepared) if self.container else list(prepared.argv)

    def write_array_script(self, prepared: Sequence[PreparedRun], path: str | Path) -> Path:
        """Write ``path`` (the sbatch script) and ``manifest.tsv`` beside it. Nothing is submitted."""
        if not prepared:
            raise EngineError("no runs to write")
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        manifest = path.parent / "manifest.tsv"
        lines = []
        for i, p in enumerate(prepared):
            lines.append(f"{i}\t{Path(str(p.workdir)).resolve()}\t{shlex.join(self._command(p))}")
        manifest.write_text("\n".join(lines) + "\n", encoding="utf-8")
        limit = f"%{self.max_parallel}" if self.max_parallel else ""
        sbatch = [
            f"--job-name={self.job_name}",
            f"--array=0-{len(prepared) - 1}{limit}",
            f"--time={self.time}",
            f"--mem={self.mem}",
            f"--cpus-per-task={self.cpus_per_task}",
            f"--output={path.parent.resolve()}/slurm-%A_%a.out",
        ]
        if self.partition:
            sbatch.append(f"--partition={self.partition}")
        if self.account:
            sbatch.append(f"--account={self.account}")
        sbatch += self.directives
        script = ["#!/bin/bash", *(f"#SBATCH {d}" for d in sbatch), "", "set -euo pipefail"]
        script += [
            f"MANIFEST={shlex.quote(str(manifest.resolve()))}",
            'LINE=$(awk -F"\\t" -v i="$SLURM_ARRAY_TASK_ID" \'$1 == i\' "$MANIFEST")',
            'WORKDIR=$(printf "%s" "$LINE" | cut -f2)',
            'CMD=$(printf "%s" "$LINE" | cut -f3-)',
            'cd "$WORKDIR"',
            'eval "$CMD" > run.log 2>&1',
            "",
        ]
        path.write_text("\n".join(script), encoding="utf-8")
        path.chmod(0o755)
        return path

    def submit_array(self, prepared: Sequence[PreparedRun], path: str | Path, submit: bool = False) -> list[RunHandle]:
        """Write the array script; with ``submit=True`` also run ``sbatch`` and record the job id."""
        script = self.write_array_script(prepared, path)
        job_id: str | None = None
        status = "written"
        if submit:
            proc = self._runner(["sbatch", str(script)], capture_output=True, text=True, check=False)
            if proc.returncode != 0:
                raise EngineError(f"sbatch failed: {proc.stderr.strip()}")
            m = re.search(r"Submitted batch job (\d+)", proc.stdout)
            job_id = m.group(1) if m else None
            status = "submitted"
        return [
            RunHandle(
                p,
                self.name,
                status=status,
                job_id=f"{job_id}_{i}" if job_id else None,
                log_path=Path(str(p.workdir)) / "run.log",
            )
            for i, p in enumerate(prepared)
        ]

    def submit(self, prepared: PreparedRun) -> RunHandle:
        if prepared.workdir is None:
            raise EngineError("Slurm runs need a work directory")
        return self.submit_array([prepared], Path(prepared.workdir) / "job.sbatch")[0]

    def wait(self, handle: RunHandle) -> RunHandle:
        """Mark a run finished once its log exists; Slurm itself is not polled."""
        if handle.log_path is not None and Path(handle.log_path).exists():
            handle.status = "done"
        return handle
