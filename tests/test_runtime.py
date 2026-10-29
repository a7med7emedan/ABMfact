from __future__ import annotations

import json
import os
import stat
import sys
from types import SimpleNamespace

import pytest

from abmfact.engines.base import EngineError, PreparedRun
from abmfact.records import RunRequest
from abmfact.runtime import ContainerBackend, SlurmBackend, detect_runtime
from abmfact.runtime.container import build_command, parse_inspect

SHIM = """#!{python}
import json, os, sys
args = sys.argv[1:]
if args[:1] == ["info"]:
    sys.exit(0)
if args[:2] == ["image", "inspect"]:
    if "--format" in args:
        print('["ghcr.io/x/physicell@sha256:abc123"]|sha256:localid')
    sys.exit(0 if "missing" not in args[-1] else 1)
if args[:1] == ["run"]:
    with open("calls.json", "w") as fh:
        json.dump(args, fh)
    print("ran inside")
    sys.exit(0)
sys.exit(2)
"""


@pytest.fixture
def fake_docker(tmp_path, monkeypatch):
    if sys.platform == "win32":
        pytest.skip("the fake docker is a script with a shebang, which Windows cannot run")
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    shim = bin_dir / "docker"
    shim.write_text(SHIM.format(python=sys.executable))
    shim.chmod(shim.stat().st_mode | stat.S_IEXEC)
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ['PATH']}")
    return shim


def prepared(workdir, argv=("./project", "config/settings.xml")):
    return PreparedRun("physicell", RunRequest("physicell", "h", {}, 1, {}), workdir=workdir, argv=list(argv),
                       env={"OMP_NUM_THREADS": "1"}, context={"mounts": {str(workdir): "/opt/build"}})  # fmt: skip


def test_detect_runtime_order_and_daemon_check():
    up = SimpleNamespace(returncode=0)
    down = SimpleNamespace(returncode=1)
    have = {"docker": "/x/docker", "apptainer": "/x/apptainer"}.get
    assert detect_runtime(have, lambda *a, **k: up) == "docker"
    assert detect_runtime(have, lambda *a, **k: down) == "apptainer"
    assert detect_runtime(lambda n: None) is None

    def boom(*a, **k):
        raise OSError("no daemon")

    assert detect_runtime({"podman": "/x/podman"}.get, boom) is None


def test_parse_inspect():
    assert parse_inspect('["a/b@sha256:abc"]|sha256:id') == "sha256:abc"
    assert parse_inspect("[]|sha256:id\n") == "sha256:id"
    assert parse_inspect("null|sha256:id") == "sha256:id"
    assert parse_inspect("garbage|") is None


def test_build_command_fetches_the_dockerfile_at_the_release_tag():
    url = "https://github.com/a7med7emedan/ABMfact.git"
    assert build_command("abmfact/physicell:1.14.2", "physicell", version="0.2.0") == (
        f'docker build -t abmfact/physicell:1.14.2 "{url}#v0.2.0:containers/physicell"'
    )
    dev = build_command("abmfact/morpheus:2.4.1", "morpheus", "linux/amd64", version="0.3.0.dev0")
    assert dev == f'docker build --platform linux/amd64 -t abmfact/morpheus:2.4.1 "{url}#main:containers/morpheus"'


def test_container_run_mounts_workdir_and_records_digest(tmp_path, fake_docker):
    work = tmp_path / "w"
    work.mkdir()
    backend = ContainerBackend("abmfact/physicell:1.14.2", runtime="docker", platform="linux/arm64")
    assert backend.availability().ok and backend.image_digest() == "sha256:abc123"
    handle = backend.submit(prepared(work))
    assert handle.status == "done" and handle.image_digest == "sha256:abc123"
    args = json.loads((work / "calls.json").read_text())
    assert args[:3] == ["run", "--rm", "-v"] and args[3] == f"{work.resolve()}:/work"
    assert "-w" in args and "--platform" in args and "OMP_NUM_THREADS=1" in args
    assert f"{work.resolve()}:/opt/build:ro" in args
    assert args[-3:] == ["abmfact/physicell:1.14.2", "./project", "config/settings.xml"]
    assert "ran inside" in handle.log_path.read_text()
    assert not ContainerBackend("missing:tag", runtime="docker").availability().ok


def test_container_detects_through_path(fake_docker):
    assert ContainerBackend("x").runtime == "docker"


def test_apptainer_command_and_errors(tmp_path):
    sif = tmp_path / "pc.sif"
    sif.write_bytes(b"image")
    b = ContainerBackend(str(sif), runtime="apptainer")
    cmd = b.command(prepared(tmp_path))
    assert (
        cmd[:3] == ["apptainer", "exec", "--bind"]
        and "--pwd" in cmd
        and cmd[-2:] == ["./project", "config/settings.xml"]
    )
    assert b.image_digest().startswith("sha256:") and b.availability().ok
    assert not ContainerBackend(str(tmp_path / "nope.sif"), runtime="apptainer").availability().ok
    with pytest.raises(EngineError):
        b.command(PreparedRun("x", RunRequest("x", "h", {}, 1, {}), call=lambda: None))
    none = ContainerBackend("img", runtime="auto", which=lambda n: None)
    assert none.runtime is None and not none.availability().ok and none.image_digest() is None
    with pytest.raises(EngineError):
        none.command(prepared(tmp_path))


def test_slurm_writes_an_array_script_without_submitting(tmp_path):
    runs = [prepared(tmp_path / f"r{i}") for i in range(3)]
    b = SlurmBackend(partition="batch", time="02:00:00", mem="8G", cpus_per_task=4, account="lcsb", max_parallel=2,
                     directives=["--qos=normal"])  # fmt: skip
    handles = b.submit_array(runs, tmp_path / "job" / "array.sbatch")
    script = (tmp_path / "job" / "array.sbatch").read_text()
    assert "#SBATCH --array=0-2%2" in script and "#SBATCH --partition=batch" in script
    assert "#SBATCH --account=lcsb" in script and "#SBATCH --cpus-per-task=4" in script and "--qos=normal" in script
    assert 'cd "$WORKDIR"' in script and 'eval "$CMD" > run.log 2>&1' in script
    manifest = (tmp_path / "job" / "manifest.tsv").read_text().splitlines()
    assert manifest[1].split("\t")[1] == str((tmp_path / "r1").resolve())
    assert manifest[1].split("\t")[2] == "./project config/settings.xml"
    assert all(h.status == "written" and h.job_id is None for h in handles)
    assert b.wait(handles[0]).status == "written"
    (tmp_path / "r0").mkdir()
    (tmp_path / "r0" / "run.log").write_text("done")
    assert b.wait(handles[0]).status == "done"


def test_slurm_submit_parses_job_id_and_containerises(tmp_path):
    calls = []

    def sbatch(args, **kw):
        calls.append(args)
        return SimpleNamespace(returncode=0, stdout="Submitted batch job 4242\n", stderr="")

    c = ContainerBackend(str(tmp_path / "pc.sif"), runtime="apptainer")
    b = SlurmBackend(container=c, runner=sbatch)
    handles = b.submit_array([prepared(tmp_path / "a"), prepared(tmp_path / "b")], tmp_path / "s.sbatch", submit=True)
    assert calls[0][0] == "sbatch" and [h.job_id for h in handles] == ["4242_0", "4242_1"]
    assert "apptainer exec" in (tmp_path / "manifest.tsv").read_text()
    fail = SlurmBackend(runner=lambda *a, **k: SimpleNamespace(returncode=1, stdout="", stderr="no partition"))
    with pytest.raises(EngineError, match="no partition"):
        fail.submit_array([prepared(tmp_path / "a")], tmp_path / "t.sbatch", submit=True)
    with pytest.raises(EngineError):
        b.write_array_script([], tmp_path / "x.sbatch")
    single = b.submit(prepared(tmp_path / "c"))
    assert single.status == "written" and (tmp_path / "c" / "job.sbatch").exists()
    assert isinstance(SlurmBackend().availability().ok, bool)
