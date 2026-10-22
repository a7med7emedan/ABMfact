"""The PhysiCell engine: toolchain, build cache, run preparation and output reading.

Everything here runs without a compiler or Docker, except the tests marked ``physicell``, which build and
run the template project and are skipped, with the reason, when neither is available.
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

from abmfact.engines import EngineError, get_engine
from abmfact.engines.physicell import PhysiCellEngine, SettingsXML, read_output
from abmfact.engines.physicell import build as build_mod
from abmfact.engines.physicell.build import Toolchain, build_project, detect_toolchain, find_compiler, source_hash
from abmfact.engines.physicell.outputs import _state
from abmfact.knobs import Knobs
from abmfact.records import APOPTOTIC, LIVE, NECROTIC

FIX = Path(__file__).parent / "fixtures" / "physicell"
TEMPLATE = FIX / "template"
OUTPUT = FIX / "output_tiny"
NATIVE = Toolchain("native", "fake g++", compiler="/fake/g++-14")


def ok(stdout: str = "", returncode: int = 0) -> SimpleNamespace:
    return SimpleNamespace(returncode=returncode, stdout=stdout, stderr="")


def test_find_compiler_skips_clang_and_needs_openmp(monkeypatch):
    monkeypatch.delenv("PHYSICELL_CPP", raising=False)
    which = {"g++-14": "/opt/g++-14", "g++": "/usr/bin/g++"}.get

    def runner(argv, **kw):
        if argv[1] == "--version":
            return ok("g++-14 (Homebrew GCC) Free Software Foundation" if "14" in argv[0] else "Apple clang")
        return ok(returncode=0)

    assert find_compiler(which, runner) == "/opt/g++-14"
    assert find_compiler({"g++": "/usr/bin/g++"}.get, runner) is None
    no_omp = lambda argv, **kw: ok("Free Software Foundation") if argv[1] == "--version" else ok(returncode=1)  # noqa: E731
    assert find_compiler(which, no_omp) is None
    monkeypatch.setenv("PHYSICELL_CPP", "my-gcc")
    assert find_compiler({"my-gcc": "/x/my-gcc"}.get, runner) is None


def test_detect_toolchain_reports_what_is_missing():
    tc = detect_toolchain("auto", which=lambda n: None)
    assert tc.kind == "none" and "PHYSICELL_CPP" in tc.detail and "Docker" in tc.detail

    def docker(argv, **kw):
        return ok() if argv[1] == "info" else ok(returncode=1)

    tc = detect_toolchain("docker", which={"docker": "/x/docker"}.get, runner=docker)
    assert tc.kind == "none" and "docker build -t abmfact/physicell:1.14.2" in tc.detail

    def docker_ready(argv, **kw):
        return ok('["x@sha256:abc"]|sha256:id') if "--format" in argv else ok()

    tc = detect_toolchain("docker", which={"docker": "/x/docker"}.get, runner=docker_ready)
    assert tc.kind == "docker" and tc.image == "abmfact/physicell:1.14.2"


def test_source_hash_tracks_sources_and_toolchain(tmp_path):
    project = tmp_path / "p"
    shutil.copytree(TEMPLATE, project)
    h = source_hash(project, NATIVE, "id")
    assert source_hash(project, NATIVE, "id") == h and source_hash(project, NATIVE, "other") != h
    (project / "custom_modules" / "custom.cpp").write_text("// changed")
    assert source_hash(project, NATIVE, "id") != h


def fake_make(calls):
    def runner(argv, cwd=None, **kw):
        calls.append(argv)
        Path(cwd, build_mod.BINARY).write_text("binary")
        return ok()

    return runner


def test_build_is_cached_and_docker_builds_in_the_image(tmp_path):
    calls: list = []
    src = tmp_path / "src"
    for d in ("BioFVM", "core", "modules"):
        (src / d).mkdir(parents=True)
    b1 = build_project(TEMPLATE, NATIVE, tmp_path / "cache", "id", source=src, runner=fake_make(calls))
    b2 = build_project(TEMPLATE, NATIVE, tmp_path / "cache", "id", source=src, runner=fake_make(calls))
    assert b1 == b2 and len(calls) == 1 and calls[0] == ["sh", "build.sh"]
    assert (b1 / "core").is_dir() and (b1 / "custom_modules" / "custom.cpp").exists()
    assert "-fomit-frame-pointer -fopenmp" in (b1 / "build.sh").read_text()
    docker = Toolchain("docker", "fake", image="abmfact/physicell:1.14.2", runtime="docker")
    calls.clear()
    b3 = build_project(TEMPLATE, docker, tmp_path / "cache", "img", maboss=True, runner=fake_make(calls))
    assert calls[0][:3] == ["docker", "run", "--rm"] and "ABMFACT_MABOSS=1" in calls[0]
    assert calls[0][-3:] == ["abmfact/physicell:1.14.2", "sh", "build.sh"] and not (b3 / "core").exists()


def test_build_errors(tmp_path, monkeypatch):
    monkeypatch.delenv("PHYSICELL_SOURCE", raising=False)
    with pytest.raises(EngineError, match="cannot build"):
        build_project(TEMPLATE, Toolchain("none", "nothing"), tmp_path, "id")
    with pytest.raises(EngineError, match="PhysiCell sources"):
        build_project(TEMPLATE, NATIVE, tmp_path, "id", runner=fake_make([]))
    src = tmp_path / "src"
    (src / "core").mkdir(parents=True)
    failing = lambda argv, cwd=None, stdout=None, **kw: (stdout.write("error: boom\n"), ok(returncode=2))[1]  # noqa: E731
    with pytest.raises(EngineError, match="boom"):
        build_project(TEMPLATE, NATIVE, tmp_path / "c2", "id", source=src, runner=failing)


@pytest.fixture
def engine(tmp_path, monkeypatch):
    e = PhysiCellEngine(cache_dir=tmp_path / "cache", threads=1, save_interval=30)
    e._toolchain = NATIVE

    def fake_build(project, toolchain, cache_dir, identity, **kw):
        d = tmp_path / "cache" / "builds" / "fake"
        d.mkdir(parents=True, exist_ok=True)
        return d

    monkeypatch.setattr("abmfact.engines.physicell.engine.build_project", fake_build)
    return e


def test_prepare_edits_only_what_a_run_controls(engine, tmp_path):
    knobs = Knobs(dt_scale=0.5, dx_scale=0.5)
    params = {"overall/max_time": 60, "user_parameters.number_of_cells": 3}
    prep = engine.prepare(TEMPLATE, params, knobs, 11, workdir=tmp_path / "work")
    s = SettingsXML.read(prep.workdir / "config" / "PhysiCell_settings.xml")
    assert s.get("options/random_seed") == "11" and s.get("parallel/omp_num_threads") == "1"
    assert s.time_steps() == {"dt_diffusion": 0.005, "dt_mechanics": 0.05, "dt_phenotype": 3.0}
    assert s.voxel()["dx"] == 10.0 and s.get("overall/max_time") == "60"
    assert s.get("save/SVG/enable") == "false" and s.get("save/full_data/interval") == "30"
    assert s.user_parameters()["number_of_cells"][1] == "3"
    assert prep.argv[0].endswith("abmfact-physicell") and prep.argv[1] == "config/PhysiCell_settings.xml"
    assert (prep.workdir / "output").is_dir() and prep.request.engine_version.startswith("PhysiCell 1.14.2")
    original = SettingsXML.read(TEMPLATE / "config" / "PhysiCell_settings.xml")
    assert original.get("options/random_seed") == "0"  # the project itself is never edited
    other = engine.prepare(TEMPLATE, params, Knobs(rng_streams="independent"), 11)
    assert SettingsXML.read(other.workdir / "config" / "PhysiCell_settings.xml").get("options/random_seed") != "11"


def test_refusals_and_support(engine, tmp_path):
    assert "not a PhysiCell project" in engine.refuse(tmp_path, Knobs())[0]
    assert engine.refuse(TEMPLATE, Knobs(pop_scale=2.0)) == ["physicell does not honour the pop_scale knob"]
    support = engine.support_for(TEMPLATE)
    assert support.dt_scale and support.dx_scale and not support.grid and not support.update_order
    assert isinstance(get_engine("physicell"), PhysiCellEngine)


def test_collect_reads_output_and_computes_observables(engine, tmp_path):
    prep = engine.prepare(TEMPLATE, {}, Knobs(), 1, workdir=tmp_path / "w")
    shutil.rmtree(prep.workdir / "output")
    shutil.copytree(OUTPUT, prep.workdir / "output")
    from abmfact.engines.base import RunHandle

    rec = engine.collect(RunHandle(prep, "local", wall_time=0.1))
    assert rec.status == "ok" and rec.outputs.scalars["cells"] == [4.0, 4.0]
    assert rec.outputs.scalars["live_default"] == [4.0, 4.0] and "substrate_mean" in rec.outputs.scalars
    failed = engine.collect(RunHandle(prep, "local", status="failed", error="exited with 1"))
    assert failed.status == "failed" and failed.error == "exited with 1"
    shutil.rmtree(prep.workdir / "output")
    (prep.workdir / "output").mkdir()
    assert "could not read" in engine.collect(RunHandle(prep, "local")).error


@pytest.mark.parametrize("reader", ["minimal", "pcdl"])
def test_reader_on_real_physicell_output(reader):
    if reader == "pcdl":
        pytest.importorskip("pcdl")
    out = read_output(OUTPUT, reader=reader)
    assert out.type_names == ["default"] and out.save_times == [0.0, 60.0]
    assert out.cells["x"].size == 8 and set(out.cells["state"].tolist()) == {LIVE}
    assert out.fields[0].values.shape == (10, 10) and out.fields[0].origin == (-90.0, -90.0)
    assert out.fields[0].spacing == (20.0, 20.0) and out.fields[0].substrate == "substrate"


def test_readers_agree_exactly():
    pytest.importorskip("pcdl")
    a, b = read_output(OUTPUT, reader="minimal"), read_output(OUTPUT, reader="pcdl")
    for c in a.cells:
        np.testing.assert_array_equal(a.cells[c], b.cells[c])
    for fa, fb in zip(a.fields, b.fields, strict=True):
        np.testing.assert_array_equal(fa.values, fb.values)


def test_death_states_and_missing_output(tmp_path):
    state = _state(np.array([0.0, 1.0, 1.0, 1.0]), np.array([5.0, 100.0, 101.0, 6.0]))
    assert state.tolist() == [LIVE, APOPTOTIC, NECROTIC, NECROTIC]
    with pytest.raises(FileNotFoundError):
        read_output(tmp_path)


# -- integration: build and run PhysiCell for real --------------------------------------------------------


def _toolchain_or_skip() -> Toolchain:
    tc = detect_toolchain()
    if not tc.ok:
        pytest.skip(f"PhysiCell cannot be built here: {tc.detail}")
    return tc


@pytest.mark.physicell
def test_template_project_runs_and_is_deterministic(tmp_path):
    tc = _toolchain_or_skip()
    cache = Path(os.environ.get("ABMFACT_PHYSICELL_CACHE", tmp_path / "cache"))
    engine = PhysiCellEngine(cache_dir=cache, threads=1, save_interval=30)
    params = {"overall/max_time": 60, "user_parameters.number_of_cells": 6}
    a = engine.run_once(TEMPLATE, params, 5, Knobs(), workdir=tmp_path / "a")
    b = engine.run_once(TEMPLATE, params, 5, Knobs(), workdir=tmp_path / "b")
    assert a.status == "ok", a.error
    assert a.outputs.scalars["cells"][0] == 6 and a.outputs.save_times == [0.0, 30.0, 60.0]
    for c in a.outputs.cells:
        np.testing.assert_array_equal(a.outputs.cells[c], b.outputs.cells[c])
    if tc.kind == "docker":
        assert a.request.image_digest and a.request.image_digest.startswith("sha256:")
