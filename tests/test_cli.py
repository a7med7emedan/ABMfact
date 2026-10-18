from __future__ import annotations

import json

import pytest
import yaml

from abmfact import __version__, cli
from abmfact.demo import DemoResult

DOC = {
    "model": {"adapter": "python", "target": "toymodels:order_bug_model"},
    "claims": [{"id": "C1", "metric": "x", "test": "greater_than", "value": 9.0}],
    "budget": {"seeds": 8, "checks": ["update_order", "grid"]},
}


@pytest.fixture
def audit_file(tmp_path):
    p = tmp_path / "audit.yaml"
    p.write_text(yaml.safe_dump(DOC))
    return p


def test_version(capsys):
    with pytest.raises(SystemExit):
        cli.main(["--version"])
    assert __version__ in capsys.readouterr().out


def test_run_and_check(audit_file, tmp_path, capsys):
    assert cli.main(["run", str(audit_file), "--out", str(tmp_path / "r")]) == 0
    out = capsys.readouterr().out
    assert "SHIFTED" in out and "likely cause" in out and (tmp_path / "r" / "report.html").exists()
    assert cli.main(["check", str(audit_file), "-q"]) == 1
    assert "flagged claims: C1" in capsys.readouterr().err
    ok = dict(DOC, budget={"seeds": 8, "checks": ["grid"]})
    audit_file.write_text(yaml.safe_dump(ok))
    assert cli.main(["check", str(audit_file), "--seeds", "6", "--jobs", "1"]) == 0


def test_errors_exit_2(tmp_path, capsys):
    assert cli.main(["run", str(tmp_path / "missing.yaml")]) == 2
    bad = tmp_path / "bad.yaml"
    bad.write_text(yaml.safe_dump({"model": {"adapter": "nope"}, "claims": []}))
    assert cli.main(["check", str(bad)]) == 2
    assert "error" in capsys.readouterr().err


def test_init(tmp_path, capsys):
    target = tmp_path / "audit.yaml"
    assert cli.main(["init", "-o", str(target)]) == 0
    assert yaml.safe_load(target.read_text())["model"]["adapter"] == "python"
    assert cli.main(["init", "-o", str(target)]) == 2
    assert cli.main(["init", "-o", str(target), "--force", "--adapter", "mesa"]) == 0
    assert cli.main(["init", "-o", "-", "--adapter", "command"]) == 0
    assert "adapter: command" in capsys.readouterr().out


def test_replay(audit_file, capsys):
    assert (
        cli.main(
            ["replay", str(audit_file), "--seed", "4", "--knobs", '{"update_order": "id"}', "--params", '{"mu": 0}']
        )
        == 0
    )
    data = json.loads(capsys.readouterr().out)
    assert data["knobs"] == {"update_order": "id"} and data["seed"] == 4
    assert 2 < data["outputs"]["x"] < 8
    assert cli.main(["replay", str(audit_file), "--seed", "1", "--knobs", "{bad"]) == 2


def test_demo_reports_diagonal(monkeypatch, capsys):
    from abmfact.checks import CHECK_NAMES
    from abmfact.zoo import EXPECTED_CHECK

    flags = {"clean": {c: False for c in CHECK_NAMES}}
    for model, check in EXPECTED_CHECK.items():
        flags[model] = {c: c == check for c in CHECK_NAMES}
    monkeypatch.setattr("abmfact.demo.run_demo", lambda **kw: DemoResult(flags, {}))
    assert cli.main(["demo", "--seeds", "5"]) == 0
    assert "CAUGHT" in capsys.readouterr().out
    flags["clean"]["grid"] = True
    flags["boundary"]["grid"] = False
    assert cli.main(["demo", "-q"]) == 1
    out = capsys.readouterr().out
    assert "clean: false alarm from grid" in out and "boundary: missed by grid" in out


def test_progress_helper(monkeypatch):
    assert cli._progress(True) is None
    monkeypatch.setattr("sys.stderr.isatty", lambda: True, raising=False)
    show = cli._progress(False)
    assert show is not None
    show(1, 2)
    show(2, 2)
