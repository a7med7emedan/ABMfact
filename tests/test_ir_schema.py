from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

from abmfact.ir import load_model, schema_dict

ROOT = Path(__file__).parent.parent
SCHEMA = ROOT / "schemas" / "abm-ir-0.1.json"
EXAMPLES = sorted((ROOT / "examples" / "ir").glob("*.yaml"))
MODELS = [p for p in EXAMPLES if not p.name.startswith("audit")]


def test_schema_file_is_current():
    assert json.loads(SCHEMA.read_text()) == schema_dict(), "regenerate with: python scripts/make_ir_reference.py"


def test_three_examples_exist():
    assert {p.stem for p in MODELS} >= {"monolayer", "spheroid", "chemotaxis"}


@pytest.mark.parametrize("path", MODELS, ids=lambda p: p.stem)
def test_examples_load_and_match_the_schema(path):
    jsonschema = pytest.importorskip("jsonschema")
    load_model(path)
    jsonschema.validate(yaml.safe_load(path.read_text()), json.loads(SCHEMA.read_text()))
