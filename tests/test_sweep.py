"""Sweep mode: settings files, runs per point, resuming through the store, and the Slurm array path."""

from __future__ import annotations

import csv
import json
import shlex
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

from abmfact.cli import main
from abmfact.modes.design import DesignError
from abmfact.modes.sweep import load_sweep, run_sweep, write_slurm

ROOT = Path(__file__).parent.parent
MODEL = ROOT / "examples" / "ir" / "monolayer.yaml"


def sweep_file(tmp_path: Path, **extra) -> Path:
    raw = {
        "model": str(MODEL),
        "engine": "reference",
        "params": {"experiment.max_time": 120, "experiment.save_interval": 60},
        "parameters": [{"name": "rules[0].half_max", "low": 0.25, "high": 1.0}],
        "design": {"method": "grid", "levels": 2},
        "seeds_per_point": 2,
        "metrics": ["last(cells)"],
        **extra,
    }
    path = tmp_path / "sweep.yaml"
    path.write_text(yaml.safe_dump(raw), encoding="utf-8")
    return path


def test_every_point_runs_the_same_seeds_and_is_summarised(tmp_path):
    spec = load_sweep(sweep_file(tmp_path))
    res = run_sweep(spec, workdir=tmp_path / "w")
    d = res.data
    assert [p["values"]["rules[0].half_max"] for p in d["design"]] == [0.25, 1.0]
    assert [(r["point"], r["seed"]) for r in d["runs"]] == [("p0000", 0), ("p0000", 1), ("p0001", 0), ("p0001", 1)]
    assert all(p["metrics"]["last(cells)"]["n"] == 2 for p in d["summary"])
    assert res.values("last(cells)").shape == (2,)


def test_a_store_lets_a_sweep_resume(tmp_path):
    spec = load_sweep(sweep_file(tmp_path))
    run_sweep(spec, store=tmp_path / "store")
    again = run_sweep(spec, store=tmp_path / "store")
    assert again.data["counts"]["cached"] == 4 and again.data["counts"]["ok"] == 0


@pytest.mark.parametrize(
    ("extra", "match"),
    [
        ({"colour": "blue"}, "unknown sweep keys"),
        ({"design": {"method": "lhs"}}, "needs n"),
        ({"metrics": []}, "at least one metric"),
        ({"parameters": [{"name": "rules[9].half_max", "low": 0, "high": 1}]}, "does not fit the model"),
        ({"slurm": {"queue": "x"}}, "unknown slurm keys"),
    ],
)
def test_bad_sweep_files_fail_before_any_run(tmp_path, extra, match):
    with pytest.raises(DesignError, match=match):
        load_sweep(sweep_file(tmp_path, **extra))


def test_the_command_writes_its_tables(tmp_path, capsys):
    out = tmp_path / "out"
    assert main(["sweep", str(sweep_file(tmp_path)), "--out", str(out), "-q"]) == 0
    assert {p.name for p in out.iterdir()} == {"sweep.json", "design.csv", "runs.csv", "summary.csv", "report.html"}
    runs = list(csv.DictReader((out / "runs.csv").open()))
    assert len(runs) == 4 and all(r["status"] == "ok" for r in runs)
    summary = list(csv.DictReader((out / "summary.csv").open()))
    assert float(summary[0]["last(cells)_n"]) == 2
    assert "Sweep: sweep" in (out / "report.html").read_text()


@pytest.mark.skipif(sys.platform == "win32", reason="Slurm array tasks are POSIX shell command lines")
def test_the_slurm_path_writes_an_array_that_runs_and_collects(tmp_path):
    path = sweep_file(tmp_path, slurm={"partition": "batch", "command": f"{sys.executable} -m abmfact"})
    spec = load_sweep(path)
    root = tmp_path / "out" / "slurm"
    script, written, cached = write_slurm(spec, root)
    assert written == 4 and cached == 0
    text = script.read_text()
    assert "#SBATCH --array=0-3" in text and "#SBATCH --partition=batch" in text
    lines = (root / "manifest.tsv").read_text().splitlines()
    assert len(lines) == 4
    missing = run_sweep(spec, collect_from=root)
    assert missing.data["counts"]["failed"] == 4
    for line in lines[:3]:  # what each array task does, without Slurm
        _, workdir, cmd = line.split("\t")
        subprocess.run(shlex.split(cmd), cwd=workdir, check=True)
    res = run_sweep(spec, collect_from=root, store=tmp_path / "store")
    assert res.data["counts"]["failed"] == 1 and "not finished" in res.data["counts"]["errors"][0]
    _, workdir, cmd = lines[3].split("\t")
    subprocess.run(shlex.split(cmd), cwd=workdir, check=True)
    assert main(["sweep", str(path), "--collect", "--out", str(tmp_path / "out"), "-q"]) == 0
    assert json.loads((tmp_path / "out" / "sweep.json").read_text())["counts"]["failed"] == 0
    script2, written2, cached2 = write_slurm(spec, tmp_path / "again", store=tmp_path / "store")
    assert script2 is not None and (written2, cached2) == (1, 3)  # only the first three were collected to the store


def test_the_command_reports_bad_files_as_errors(tmp_path, capsys):
    assert main(["sweep", str(sweep_file(tmp_path, colour="blue")), "--out", str(tmp_path / "o")]) == 2
    assert "unknown sweep keys" in capsys.readouterr().err
