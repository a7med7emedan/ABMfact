from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType

from abmfact.cli import build_parser

ROOT = Path(__file__).parent.parent


def _generator() -> ModuleType:
    spec = importlib.util.spec_from_file_location("make_cli_reference", ROOT / "scripts" / "make_cli_reference.py")
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_cli_reference_page_is_current():
    page = (ROOT / "docs" / "cli-reference.md").read_text(encoding="utf-8")
    assert page == _generator().render(build_parser()), "regenerate with: python scripts/make_cli_reference.py"


def test_every_command_has_an_exit_status():
    mod = _generator()
    assert set(mod.EXIT_CODES) == set(mod._subcommands(build_parser()).choices)
