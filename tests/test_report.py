from __future__ import annotations

import hashlib
import json

import yaml

from abmfact import load_audit, run_audit
from abmfact.report import render_html, replay_command, write_outputs

DOC = {
    "title": "Toy <audit>",
    "model": {"adapter": "python", "target": "toymodels:order_bug_model"},
    "claims": [
        {"id": "C1", "statement": "x is large", "metric": "x", "test": "greater_than", "value": 9.0},
        {"id": "C2", "metric": "x", "test": "monotone_increasing", "over": "a", "where": {"a": [0, 1]}},
        {"id": "C3", "metric": "x", "test": "between", "value": [5, 30], "skip_checks": {"grid": "n/a"}},
    ],
    "params": {"slope": 1.0},
    "budget": {"seeds": 10},
}


def test_write_outputs(tmp_path):
    src = tmp_path / "audit.src.yaml"
    src.write_text(yaml.safe_dump(DOC))
    res = run_audit(load_audit(src))
    out = tmp_path / "out"
    written = write_outputs(res, out, audit_file=src)
    assert set(written) == {"audit", "results", "report", "rocrate"}
    html = (out / "report.html").read_text()
    assert "Toy &lt;audit&gt;" in html and "SHIFTED" in html and "abmfact replay" in html
    assert "<script" not in html
    crate = json.loads((out / "ro-crate-metadata.json").read_text())
    files = {e["@id"]: e for e in crate["@graph"] if e.get("@type") == "File"}
    assert set(files) == {"audit.yaml", "results.json", "report.html"}
    for name, entry in files.items():
        assert entry["sha256"] == hashlib.sha256((out / name).read_bytes()).hexdigest()
    action = next(e for e in crate["@graph"] if e.get("@type") == "CreateAction")
    assert action["object"] == [{"@id": "audit.yaml"}]
    results = json.loads((out / "results.json").read_text())
    assert results["audit"]["sha256"] == hashlib.sha256(src.read_bytes()).hexdigest()
    # Writing into the directory that already holds audit.yaml must not fail.
    write_outputs(res, out, audit_file=out / "audit.yaml")


def test_write_outputs_without_audit_file(tmp_path):
    from abmfact import parse_audit

    res = run_audit(parse_audit(DOC))
    written = write_outputs(res, tmp_path / "o")
    assert "audit" not in written
    assert render_html(res).startswith("<!doctype html>")


def test_replay_command_quotes():
    cmd = replay_command("my audit.yaml", {"seed": 3, "params": {"a": 1}, "knobs": {"update_order": "id"}})
    assert cmd.startswith("abmfact replay 'my audit.yaml' --seed 3")
    assert "--knobs" in cmd and "--params" in cmd
    assert replay_command(None, {"seed": 1}) == "abmfact replay audit.yaml --seed 1"
