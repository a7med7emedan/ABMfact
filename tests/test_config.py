from __future__ import annotations

import pytest
import yaml

from abmfact.config import Budget, ConfigError, load_audit, parse_audit, template

GOOD = {
    "model": {"adapter": "python", "target": "toymodels:noisy_model"},
    "claims": [{"id": "C1", "metric": "x", "test": "greater_than", "value": 1}],
}


def test_load_audit_with_overrides(tmp_path):
    p = tmp_path / "a.yaml"
    p.write_text(yaml.safe_dump(GOOD | {"budget": {"seeds": 50}}))
    a = load_audit(p, {"seeds": 7, "jobs": None})
    assert a.budget.seeds == 7 and a.budget.jobs == 1
    assert a.sha256 and a.source == p and a.title == "ABMfact audit"


@pytest.mark.parametrize(
    ("doc", "match"),
    [
        ([1, 2], "mapping"),
        ({"claims": []}, "model"),
        (GOOD | {"extra": 1}, "unknown top-level"),
        (GOOD | {"claims": []}, "at least one claim"),
        (GOOD | {"budget": {"seeds": 2}}, "at least 3"),
        (GOOD | {"budget": {"alpha": 0.7}}, "alpha"),
        (GOOD | {"budget": {"jobs": 0}}, "jobs"),
        (GOOD | {"budget": {"checks": ["nope"]}}, "unknown checks"),
        (GOOD | {"budget": {"speed": 1}}, "unknown budget"),
    ],
)
def test_parse_audit_errors(doc, match):
    with pytest.raises(ConfigError, match=match):
        parse_audit(doc)


def test_bad_yaml(tmp_path):
    p = tmp_path / "a.yaml"
    p.write_text("model: [unclosed")
    with pytest.raises(ConfigError, match="YAML"):
        load_audit(p)


@pytest.mark.parametrize("adapter", ["python", "mesa", "netlogo", "command"])
def test_templates_are_valid_yaml_with_valid_claims(adapter):
    doc = yaml.safe_load(template(adapter))
    assert doc["model"]["adapter"] == adapter
    from abmfact.claims import parse_claims

    assert len(parse_claims(doc["claims"])) == 2
    Budget.from_mapping(doc["budget"])


def test_template_unknown_adapter():
    with pytest.raises(ConfigError):
        template("julia")
