"""PhysiCell rules CSV files (cell behaviour hypothesis grammar, version 3).

The format, as PhysiCell 1.14.2 reads it in ``core/PhysiCell_rules.cpp`` (``parse_csv_rule_v3``): no
header, eight columns ``cell_type, signal, direction, behavior, saturation value, half-max, Hill power,
apply to dead``. Lines with another number of fields or an empty field are skipped, which is how lines
starting with ``//`` act as comments. Versions below 3 are rejected by PhysiCell.
"""

from __future__ import annotations

import csv
import io
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class RuleRow:
    """One rule from a PhysiCell rules CSV: a signal that raises or lowers a behaviour of one cell type.

    Attributes:
        cell_type: The cell definition the rule applies to.
        signal: The signal name, as PhysiCell spells it.
        direction: ``"increases"`` or ``"decreases"``.
        behaviour: The behaviour name, as PhysiCell spells it (the column is ``behavior`` there).
        saturation_value: The behaviour value approached as the signal grows.
        half_max: The signal value at which the response is half its maximum.
        hill_power: The Hill power of the response.
        apply_to_dead: Whether the rule also applies to dead cells.
    """

    cell_type: str
    signal: str
    direction: str
    behaviour: str
    saturation_value: float
    half_max: float
    hill_power: float
    apply_to_dead: bool

    def to_fields(self) -> list[str]:
        """The eight CSV fields in PhysiCell's column order.

        Whole numbers are written without a decimal point, and ``apply_to_dead`` as ``1`` or ``0``.
        """
        return [self.cell_type, self.signal, self.direction, self.behaviour, _num(self.saturation_value),
                _num(self.half_max), _num(self.hill_power), "1" if self.apply_to_dead else "0"]  # fmt: skip


def _num(x: float) -> str:
    return str(int(x)) if float(x).is_integer() else repr(float(x))


@dataclass(frozen=True)
class Skipped:
    """A line of a rules CSV that PhysiCell would skip.

    Attributes:
        line: The line number, counting from 1.
        text: The line with surrounding whitespace removed.
        reason: Why PhysiCell skips it.
    """

    line: int
    text: str
    reason: str


def parse_rules(text: str) -> tuple[list[RuleRow], list[Skipped]]:
    """Rules and the lines PhysiCell would skip (comments, blanks and malformed lines), with reasons."""
    rows: list[RuleRow] = []
    skipped: list[Skipped] = []
    for n, line in enumerate(text.splitlines(), 1):
        stripped = line.strip()
        if not stripped:
            continue
        fields = [f.strip() for f in next(csv.reader(io.StringIO(stripped)))]
        if stripped.startswith("//"):
            skipped.append(Skipped(n, stripped, "comment"))
            continue
        if len(fields) != 8 or any(f == "" for f in fields):
            skipped.append(Skipped(n, stripped, f"{len(fields)} fields or an empty field; PhysiCell skips it"))
            continue
        cell_type, signal, direction, behaviour, sat, half, hill, dead = fields
        if direction not in ("increases", "decreases"):
            skipped.append(Skipped(n, stripped, f"direction {direction!r} is not increases or decreases"))
            continue
        try:
            rows.append(RuleRow(cell_type, signal, direction, behaviour, float(sat), float(half), float(hill),
                                bool(float(dead))))  # fmt: skip
        except ValueError:
            skipped.append(Skipped(n, stripped, "a numeric field is not a number"))
    return rows, skipped


def read_rules_csv(path: str | Path) -> tuple[list[RuleRow], list[Skipped]]:
    """The rules in a CSV file and the lines PhysiCell would skip, as :func:`parse_rules` returns them."""
    return parse_rules(Path(path).read_text(encoding="utf-8"))


def write_rules_csv(rows: list[RuleRow], path: str | Path, comment: str | None = None) -> Path:
    """Write ``rows`` as a rules CSV at ``path``, creating parent folders, and return the path.

    Each line of ``comment`` is written first as a ``//`` line, which PhysiCell skips.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    buf = io.StringIO()
    if comment:
        for line in comment.splitlines():
            buf.write(f"// {line}\n")
    w = csv.writer(buf, lineterminator="\n")
    for r in rows:
        w.writerow(r.to_fields())
    path.write_text(buf.getvalue(), encoding="utf-8")
    return path
