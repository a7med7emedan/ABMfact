"""Load, save, hash and parameterise ABM-IR documents, and export the JSON Schema."""

from __future__ import annotations

import copy
import hashlib
import json
import re
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import yaml
from pydantic import ValidationError

from ..digest import content_digest
from .errors import IRError, from_validation_error
from .model import SCHEMA_VERSION, IRModel

SCHEMA_ID = f"https://github.com/a7med7emedan/ABMfact/blob/main/schemas/abm-ir-{SCHEMA_VERSION}.json"

#: Fields that describe a model without changing what it computes; they are left out of the hash.
UNHASHED = ("description", "provenance")


def model_from_dict(doc: Any, source: str | None = None, source_dir: Path | None = None) -> IRModel:
    """Validate a mapping as an ABM-IR model, raising :class:`IRError` with dotted paths."""
    if not isinstance(doc, Mapping):
        raise IRError([("", "an ABM-IR document must be a mapping")], source)
    version = doc.get("schema_version", SCHEMA_VERSION)
    if version != SCHEMA_VERSION:
        raise IRError([("schema_version", f"unsupported version {version!r}; this ABMfact reads {SCHEMA_VERSION}")])
    try:
        model = IRModel.model_validate(doc)
    except ValidationError as exc:
        raise from_validation_error(exc, doc, source) from None
    model._source_dir = source_dir
    return model


def load_model(path: str | Path) -> IRModel:
    """Read an ABM-IR model from a YAML or JSON file."""
    path = Path(path)
    text = path.read_text(encoding="utf-8")
    try:
        doc = json.loads(text) if path.suffix.lower() == ".json" else yaml.safe_load(text)
    except (json.JSONDecodeError, yaml.YAMLError) as exc:
        raise IRError([("", f"cannot parse: {exc}")], str(path)) from None
    return model_from_dict(doc, source=str(path), source_dir=path.parent.resolve())


def model_to_dict(model: IRModel, full: bool = False) -> dict[str, Any]:
    """A plain mapping. Defaults are left out unless ``full``; ``schema_version`` always comes first."""
    body = model.model_dump(mode="json", exclude_defaults=not full)
    body.pop("schema_version", None)
    return {"schema_version": model.schema_version, **body}


def save_model(model: IRModel, path: str | Path) -> Path:
    """Write a model as YAML, or JSON when the suffix is ``.json``."""
    path = Path(path)
    doc = model_to_dict(model)
    if path.suffix.lower() == ".json":
        path.write_text(json.dumps(doc, indent=2) + "\n", encoding="utf-8")
    else:
        path.write_text(yaml.safe_dump(doc, sort_keys=False, allow_unicode=True), encoding="utf-8")
    return path


def _file_digest(model: IRModel, rel: str) -> str | None:
    p = model.resolve(rel)
    return content_digest(p) if p.is_file() else None


def model_hash(model: IRModel) -> str:
    """SHA-256 of what the model computes: every field except description and provenance, plus the
    contents of the files it references (initial positions and intracellular models). Text files are hashed
    with LF line endings, so a Windows checkout gives the same hash."""
    doc = model.model_dump(mode="json", exclude=set(UNHASHED))
    files: dict[str, str | None] = {}
    for p in model.initial_conditions.placements:
        if p.path:
            files[p.path] = _file_digest(model, p.path)
    for ic in model.intracellular:
        for f in ic.files.values():
            files[f] = _file_digest(model, f)
    doc["_files"] = files
    canonical = json.dumps(doc, sort_keys=True, separators=(",", ":"), allow_nan=False)
    return hashlib.sha256(canonical.encode()).hexdigest()


def read_cells_csv(path: str | Path, type_names: list[str]) -> tuple[list[tuple[float, float, float]], list[str]]:
    """Initial cells from a PhysiCell-style ``cells.csv``: ``x,y,z,type`` with or without a header.

    ``type`` is a cell type name or an index into ``type_names``; extra columns are ignored.
    """
    import csv

    rows = [
        r for r in csv.reader(Path(path).read_text(encoding="utf-8").splitlines()) if r and not r[0].startswith("//")
    ]
    if rows and not _is_number(rows[0][0]):
        rows = rows[1:]
    pts = [(float(r[0]), float(r[1]), float(r[2]) if len(r) > 2 and r[2].strip() else 0.0) for r in rows]
    names = [type_names[int(float(r[3]))] if _is_number(r[3]) else r[3].strip() for r in rows]
    return pts, names


def _is_number(text: str) -> bool:
    try:
        float(text)
    except ValueError:
        return False
    return True


_TOKEN = re.compile(r"([A-Za-z_][A-Za-z0-9_]*)|\[([^\]]+)\]")


def _parse_path(path: str) -> list[str | int]:
    parts: list[str | int] = []
    for segment in path.split("."):
        if not segment:
            raise IRError([(path, "empty path segment")])
        i = 0
        while i < len(segment):
            m = _TOKEN.match(segment, i)
            if not m:
                raise IRError([(path, f"cannot parse path near {segment[i:]!r}")])
            key, idx = m.group(1), m.group(2)
            if key is not None:
                parts.append(key)
            else:
                parts.append(int(idx) if idx.lstrip("-").isdigit() else f"[{idx}]")
            i = m.end()
    return parts


def _step(node: Any, part: str | int, path: str) -> tuple[Any, str | int]:
    """Resolve one part against ``node``; list items can be addressed by index or by ``[name]``."""
    if isinstance(part, str) and part.startswith("[") and isinstance(node, list):
        name = part[1:-1]
        for i, item in enumerate(node):
            if isinstance(item, Mapping) and item.get("name", item.get("cell_type")) == name:
                return node, i
        raise IRError([(path, f"no item named {name!r}")])
    if isinstance(part, int):
        if not isinstance(node, list) or not -len(node) <= part < len(node):
            raise IRError([(path, f"index {part} out of range")])
        return node, part
    if isinstance(part, str) and part.startswith("["):
        return node, part[1:-1]
    if not isinstance(node, Mapping):
        raise IRError([(path, f"cannot index into {type(node).__name__} with {part!r}")])
    return node, part


def apply_params(model: IRModel, params: Mapping[str, Any]) -> IRModel:
    """A copy of ``model`` with values replaced at dotted paths.

    ``cell_types[tumour].cycle.rates[0]`` addresses a list item by name and then by index. A path to a
    mapping key that does not exist yet adds it (useful for ``custom`` values and affinities). Every
    change is validated, so a bad value fails with the same messages as a bad file.
    """
    if not params:
        return model
    doc = model.model_dump(mode="json")
    for path, value in params.items():
        parts = _parse_path(path)
        node: Any = doc
        for part in parts[:-1]:
            holder, key = _step(node, part, path)
            if isinstance(holder, Mapping) and key not in holder:
                raise IRError([(path, f"unknown field {key!r}")])
            node = holder[key]
        holder, key = _step(node, parts[-1], path)
        if isinstance(holder, Mapping) and key not in holder and not _is_open_mapping(parts):
            raise IRError([(path, f"unknown field {key!r}")])
        holder[key] = copy.deepcopy(value)
    out = model_from_dict(doc, source="params")
    out._source_dir = model.source_dir
    return out


def _is_open_mapping(parts: list[str | int]) -> bool:
    open_maps = {"custom", "adhesion_affinities", "attack_rates", "phagocytosis_rates", "fusion_rates"}
    open_maps |= {"transformations", "sides", "inputs", "outputs", "settings", "files"}
    return len(parts) >= 2 and parts[-2] in open_maps


def schema_dict() -> dict[str, Any]:
    """The JSON Schema of ABM-IR, as exported to ``schemas/abm-ir-<version>.json``."""
    schema = IRModel.model_json_schema()
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$id": SCHEMA_ID,
        **schema,
        "title": f"ABM-IR {SCHEMA_VERSION}",
    }


def export_schema(path: str | Path) -> Path:
    """Write the ABM-IR JSON Schema to ``path`` and return the path."""
    path = Path(path)
    path.write_text(json.dumps(schema_dict(), indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return path
