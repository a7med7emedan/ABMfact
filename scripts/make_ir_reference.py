"""Write the ABM-IR JSON Schema to schemas/ and its reference page to docs/, both generated from the
pydantic models, never by hand.

Run from the repository root:  python scripts/make_ir_reference.py
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from abmfact.ir import SCHEMA_VERSION, export_schema, schema_dict

ROOT = Path(__file__).resolve().parent.parent
REPO = "https://github.com/a7med7emedan/ABMfact"

ORDER = [
    "IRModel", "Units", "Space", "Bounds", "Voxel", "Substrate", "SubstrateBoundary", "CellType", "Cycle", "Death",
    "Integrity", "Mechanics", "Motility", "Chemotaxis", "Secretion", "Interactions", "Rule", "IntracellularModel",
    "InitialConditions", "Placement", "TimedEvent", "Observable", "Experiment", "Provenance", "DerivedFrom",
    "NativeExtension",
]  # fmt: skip


def _type(spec: dict[str, Any]) -> str:
    if "$ref" in spec:
        name = spec["$ref"].rsplit("/", 1)[-1]
        return f"[{name}](#{name.lower()})"
    if "anyOf" in spec:
        return " or ".join(_type(s) for s in spec["anyOf"])
    if "enum" in spec:
        return " \\| ".join(f"`{v}`" for v in spec["enum"])
    if "const" in spec:
        return f"`{spec['const']}`"
    t = spec.get("type", "any")
    if t == "array":
        if "prefixItems" in spec:
            return "(" + ", ".join(_type(s) for s in spec["prefixItems"]) + ")"
        return f"list of {_type(spec.get('items', {}))}"
    if t == "object":
        extra = spec.get("additionalProperties")
        return f"map of {_type(extra)}" if isinstance(extra, dict) else "object"
    bounds = []
    for key, sym in (("minimum", ">="), ("exclusiveMinimum", ">"), ("maximum", "<="), ("exclusiveMaximum", "<")):
        if key in spec:
            bounds.append(f"{sym} {spec[key]}")
    if "minLength" in spec:
        bounds.append("non-empty")
    return t + (f" ({', '.join(bounds)})" if bounds else "")


def _default(spec: dict[str, Any], required: bool) -> str:
    if required:
        return "required"
    if "default" in spec:
        value = spec["default"]
        return "empty" if value == "" else ("none" if value is None else f"`{value}`")
    if spec.get("type") == "array":
        return "empty list"
    if spec.get("type") == "object":
        return "empty"
    return "defaults"


def render(schema: dict[str, Any]) -> str:
    defs = dict(schema.get("$defs", {}))
    defs["IRModel"] = {k: v for k, v in schema.items() if k not in ("$defs", "$schema", "$id")}
    lines = [
        f"# ABM-IR {SCHEMA_VERSION} reference",
        "",
        "Generated from the pydantic models by `scripts/make_ir_reference.py`; the JSON Schema is",
        f"[`schemas/abm-ir-{SCHEMA_VERSION}.json`]({REPO}/blob/main/schemas/abm-ir-{SCHEMA_VERSION}.json).",
        "Units follow the",
        "model's `units` (micron and minute by default). Unknown fields are rejected, and every name a model",
        "refers to (a substrate, a cell type, a rule's signal or behaviour) must exist in the model.",
        "",
    ]
    for name in [n for n in ORDER if n in defs] + sorted(set(defs) - set(ORDER)):
        d = defs[name]
        lines += [f"## {name}", ""]
        doc = (d.get("description") or "").strip()
        if doc:
            lines += [" ".join(doc.split()), ""]
        lines += ["| Field | Type | Default |", "|---|---|---|"]
        required = set(d.get("required", []))
        for field, spec in d.get("properties", {}).items():
            lines.append(f"| `{field}` | {_type(spec)} | {_default(spec, field in required)} |")
        lines.append("")
    return "\n".join(lines)


def main() -> None:
    path = export_schema(ROOT / "schemas" / f"abm-ir-{SCHEMA_VERSION}.json")
    print(f"wrote {path.relative_to(ROOT)}")
    ref = ROOT / "docs" / "ir-reference.md"
    ref.write_text(render(schema_dict()), encoding="utf-8")
    print(f"wrote {ref.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
