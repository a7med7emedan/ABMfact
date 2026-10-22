"""Write the ABM-IR JSON Schema to schemas/, generated from the pydantic models, never by hand.

Run from the repository root:  python scripts/make_ir_reference.py
"""

from __future__ import annotations

from pathlib import Path

from abmfact.ir import SCHEMA_VERSION, export_schema

ROOT = Path(__file__).resolve().parent.parent


def main() -> None:
    path = export_schema(ROOT / "schemas" / f"abm-ir-{SCHEMA_VERSION}.json")
    print(f"wrote {path.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
