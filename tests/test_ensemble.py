"""Ensemble mode: many seeds of one setting, summarised with intervals."""

from __future__ import annotations

import csv
import json
from pathlib import Path

import numpy as np
import pytest

from abmfact.cli import main
from abmfact.modes.ensemble import run_ensemble, series_by_time, summarise

ROOT = Path(__file__).parent.parent
MODEL = ROOT / "examples" / "ir" / "monolayer.yaml"
SHORT = {"experiment.max_time": 120, "experiment.save_interval": 60}


def test_summaries_match_the_audit_interval_and_numpy_quantiles():
    x = [1.0, 2.0, 4.0, 8.0]
    s = summarise(x, 0.05)
    assert s["n"] == 4 and s["mean"] == pytest.approx(3.75)
    assert s["low"] < 3.75 < s["high"]
    assert s["q50"] == pytest.approx(np.median(x))
    assert s["sd"] == pytest.approx(np.std(x, ddof=1))


def test_series_are_aligned_by_time_across_runs():
    runs = [{"time": [0, 10], "a": [1.0, 2.0], "skip": 5.0}, {"time": [0, 10, 20], "a": [3.0, 4.0, 6.0]}]
    by = series_by_time(runs)
    assert by["a"] == {0.0: [1.0, 3.0], 10.0: [2.0, 4.0], 20.0: [6.0]}
    assert "skip" not in by


def test_an_ensemble_summarises_every_series_and_metric_and_reuses_the_store(tmp_path):
    res = run_ensemble(
        MODEL,
        "reference",
        3,
        params=SHORT,
        metrics=["last(cells)", "max(nosuch)"],
        store=tmp_path / "store",
        workdir=tmp_path / "w",
    )
    d = res.data
    assert d["runs"]["ok"] == 3 and d["runs"]["failed"] == 0
    assert set(d["series"]) == {"cells", "spread"}
    assert [r["time"] for r in d["series"]["cells"]] == [0.0, 60.0, 120.0]
    m = d["metrics"]["last(cells)"]
    assert m["n"] == 3 and m["seeds"] == [0, 1, 2] and m["mean"] == pytest.approx(np.mean(m["values"]))
    assert d["metrics"]["max(nosuch)"]["values"] == [] and d["metrics"]["max(nosuch)"]["errors"]
    again = run_ensemble(MODEL, "reference", 4, params=SHORT, store=tmp_path / "store", workdir=tmp_path / "w2")
    assert again.data["runs"]["cached"] == 3 and again.data["runs"]["ok"] == 1
    assert again.data["runs"]["by_seed"]["0"] == d["runs"]["by_seed"]["0"]


def test_bad_settings_are_refused_before_any_run():
    with pytest.raises(ValueError, match="at least 2 seeds"):
        run_ensemble(MODEL, "reference", 1)
    with pytest.raises(ValueError, match="cannot run"):
        run_ensemble(MODEL, "nosuch", 3)


def test_the_command_writes_json_csv_and_a_report(tmp_path, capsys):
    out = tmp_path / "out"
    code = main(
        [
            "ensemble",
            str(MODEL),
            "--seeds",
            "2",
            "--param",
            "experiment.max_time=120",
            "--param",
            "experiment.save_interval=60",
            "--metric",
            "last(cells)",
            "--out",
            str(out),
        ]
    )
    assert code == 0
    d = json.loads((out / "ensemble.json").read_text())
    assert d["ensemble"]["params"] == SHORT
    rows = list(csv.DictReader((out / "ensemble.csv").open()))
    assert {r["observable"] for r in rows} == {"cells", "spread"}
    assert "<h1>Ensemble: monolayer.yaml in reference</h1>" in (out / "report.html").read_text()
    assert "last(cells)" in capsys.readouterr().out
    assert main(["ensemble", str(MODEL), "--seeds", "2", "--param", "oops", "--out", str(out)]) == 2
