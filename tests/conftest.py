from __future__ import annotations

import sys
from pathlib import Path

import pytest

HERE = Path(__file__).parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))


@pytest.fixture
def examples() -> Path:
    return ROOT / "examples"
