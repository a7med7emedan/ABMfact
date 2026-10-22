"""``import abmfact`` stays light: no pydantic, no engines beyond the v0.1 adapters, no optional extras."""

from __future__ import annotations

import json
import subprocess
import sys

HEAVY = ("pydantic", "abmfact.ir", "abmfact.reference", "abmfact.engines.reference", "abmfact.engines.physicell",
         "abmfact.records.store", "abmfact.runtime.container", "abmfact.runtime.slurm", "abmfact.verify",
         "pyarrow", "duckdb", "zarr", "xarray", "pcdl", "mesa")  # fmt: skip


def test_import_abmfact_loads_no_engine_or_pydantic():
    code = "import json, sys, abmfact; print(json.dumps(sorted(sys.modules)))"
    loaded = json.loads(subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=True).stdout)
    assert [m for m in HEAVY if m in loaded] == []
