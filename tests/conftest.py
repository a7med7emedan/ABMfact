from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

import pytest

HERE = Path(__file__).parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))
# Plots must not depend on the user's matplotlib settings or font cache.
os.environ.setdefault("MPLCONFIGDIR", tempfile.mkdtemp(prefix="abmfact-test-mpl-"))


@pytest.fixture
def examples() -> Path:
    return ROOT / "examples"
