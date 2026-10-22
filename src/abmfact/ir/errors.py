"""Validation errors that name the exact place in the document."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any


class IRError(ValueError):
    """An ABM-IR document is not valid. ``errors`` lists (dotted path, message) pairs."""

    def __init__(self, errors: Sequence[tuple[str, str]], source: str | None = None):
        self.errors = list(errors)
        self.source = source
        head = f"{source}: " if source else ""
        lines = [f"  {path or '(document)'}: {msg}" for path, msg in self.errors]
        super().__init__(f"{head}invalid ABM-IR model\n" + "\n".join(lines))


def dotted_path(loc: Sequence[Any], doc: Any) -> str:
    """Turn a pydantic error location into ``cell_types[tumour].cycle.rates[1]``.

    List items that have a ``name`` are addressed by name, so the path stays readable and matches the
    syntax of :func:`abmfact.ir.apply_params`.
    """
    out = ""
    node = doc
    for part in loc:
        if isinstance(part, int):
            item = node[part] if isinstance(node, list) and part < len(node) else None
            label = item.get("name") if isinstance(item, Mapping) else None
            out += f"[{label}]" if isinstance(label, str) else f"[{part}]"
            node = item
        else:
            out += f".{part}" if out else str(part)
            node = node.get(part) if isinstance(node, Mapping) else None
    return out


def from_validation_error(exc: Any, doc: Any, source: str | None = None) -> IRError:
    """Convert a pydantic ``ValidationError``. Cross-reference problems arrive as ``path: message`` lines."""
    errors: list[tuple[str, str]] = []
    for e in exc.errors():
        loc = [p for p in e["loc"] if not (isinstance(p, str) and ("[" in p or p.startswith("function-")))]
        path = dotted_path(loc, doc)
        msg = str(e["msg"]).removeprefix("Value error, ")
        if e["type"] == "value_error" and not path and ": " in msg:
            for line in msg.splitlines():
                sub, _, text = line.partition(": ")
                errors.append(((f"{path}.{sub}" if path else sub), text))
        else:
            errors.append((path, msg))
    return IRError(errors, source)
