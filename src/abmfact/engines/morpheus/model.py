"""Read and edit MorpheusML models with the standard library only.

A Morpheus model is one XML file (``<MorpheusModel version="...">``). The engine edits only what a run controls,
in a copy: the stop time when a parameter sets it, any element a parameter names, and two loggers ABMfact adds
to the ``Analysis`` section. One logs each cell's id, type, centre and volume; the other logs every field, one
file per save. Loggers are Morpheus's own output components and change no dynamics. The seed and the thread
count are set on the command line.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path

from ..base import EngineError

CELL_SYMBOLS = ("cell.id", "cell.type", "cell.center.x", "cell.center.y", "cell.center.z", "cell.volume")


@dataclass
class MorpheusModel:
    """One MorpheusML model, parsed for reading and editing.

    Attributes:
        path: The model file.
        root: The parsed ``MorpheusModel`` element, edited in place by the setters.
    """

    path: Path
    root: ET.Element

    @classmethod
    def read(cls, path: str | Path) -> MorpheusModel:
        """Read the model at ``path``; it must be a ``MorpheusModel`` document."""
        path = Path(path)
        if not path.is_file():
            raise EngineError(f"{path} is not a Morpheus model file")
        parser = ET.XMLParser(target=ET.TreeBuilder(insert_comments=True))  # noqa: S314
        try:
            root = ET.parse(path, parser=parser).getroot()  # noqa: S314 - the user's own model file
        except ET.ParseError as exc:
            raise EngineError(f"{path} is not valid XML: {exc}") from exc
        if root.tag != "MorpheusModel":
            raise EngineError(f"{path} is not a MorpheusML model (its root is <{root.tag}>)")
        return cls(path, root)

    # -- reading -------------------------------------------------------------------------------------------

    @property
    def version(self) -> str:
        """The MorpheusML version the file declares."""
        return self.root.get("version", "")

    def title(self) -> str:
        """The model's title, or the file name."""
        return (self.root.findtext("Description/Title") or self.path.stem).strip()

    def lattice(self) -> tuple[int, int, int]:
        """The lattice size in nodes, from ``Space/Lattice/Size``; a size can be an expression only in 2.4+."""
        size = self.root.find("Space/Lattice/Size")
        if size is None:
            raise EngineError("the model has no Space/Lattice/Size")
        parts = [p.strip() for p in size.get("value", "").split(",")]
        try:
            x, y, z = (int(float(p)) for p in [*parts, "0", "0"][:3])
        except ValueError as exc:
            raise EngineError(f"the lattice size {size.get('value')!r} is not three numbers") from exc
        return x, y, z

    def dimensions(self) -> int:
        """2 for a flat lattice (``z`` of 0 or 1), else 3."""
        return 2 if self.lattice()[2] <= 1 else 3

    def stop_time(self) -> float:
        """The model's stop time, in its own time units."""
        el = self.root.find("Time/StopTime")
        if el is None:
            raise EngineError("the model has no Time/StopTime")
        return float(el.get("value", "0"))

    def cell_types(self) -> list[str]:
        """Every cell type name in declaration order, including the medium; ``cell.type`` indexes this list."""
        return [ct.get("name", "") for ct in self.root.iterfind("CellTypes/CellType")]

    def biological_types(self) -> list[str]:
        """Cell types that are cells, not the medium."""
        return [ct.get("name", "") for ct in self.root.iterfind("CellTypes/CellType") if ct.get("class") != "medium"]

    def fields(self) -> list[str]:
        """Symbols of the global fields, the model's chemical fields."""
        return [f.get("symbol", "") for f in self.root.iterfind("Global/Field") if f.get("symbol")]

    # -- editing -------------------------------------------------------------------------------------------

    def set_stop_time(self, value: float) -> None:
        """Set ``Time/StopTime``."""
        el = self.root.find("Time/StopTime")
        if el is None:
            raise EngineError("the model has no Time/StopTime")
        el.set("value", repr(float(value)))

    def set(self, path: str, value: str | float | int) -> None:
        """Set the ``value`` attribute of the element at ``path`` (an ElementTree path from the root)."""
        el = self.root.find(path)
        if el is None:
            raise EngineError(f"no element {path!r} in {self.path.name}")
        el.set("value", repr(float(value)) if isinstance(value, float) else str(value))

    def add_output_loggers(self, every: float, cell_symbols: tuple[str, ...] = ()) -> tuple[int, int]:
        """Add ABMfact's cell and field loggers, saving every ``every`` time units.

        ``cell_symbols`` are logged for each cell after the standard ones, such as the ``state`` property of a
        translated model.

        Returns:
            The 1-based positions of the two loggers among the model's loggers, which name their files
            (``logger_<n>.csv`` and ``logger_<n>_<time>.csv``). The first is 0 when the model has no cell types,
            as Morpheus refuses cell symbols then; the second is 0 when the model has no fields.
        """
        analysis = self.root.find("Analysis")
        if analysis is None:
            analysis = ET.SubElement(self.root, "Analysis")
        position = len(analysis.findall("Logger"))
        cell_logger = 0
        if self.biological_types():
            cells = ET.SubElement(analysis, "Logger", {"time-step": repr(float(every)), "name": "abmfact cells"})
            inp = ET.SubElement(cells, "Input")
            symbols = CELL_SYMBOLS if self.dimensions() == 3 else tuple(s for s in CELL_SYMBOLS if s != "cell.center.z")
            for s in (*symbols, *cell_symbols):
                ET.SubElement(inp, "Symbol", {"symbol-ref": s})
            ET.SubElement(ET.SubElement(cells, "Output"), "TextOutput", {"file-format": "csv"})
            position += 1
            cell_logger = position
        if not self.fields():
            return cell_logger, 0
        fields = ET.SubElement(analysis, "Logger", {"time-step": repr(float(every)), "name": "abmfact fields"})
        inp = ET.SubElement(fields, "Input")
        for f in self.fields():
            ET.SubElement(inp, "Symbol", {"symbol-ref": f})
        ET.SubElement(ET.SubElement(fields, "Output"), "TextOutput",
                      {"file-format": "csv", "file-separation": "time"})  # fmt: skip
        return cell_logger, position + 1

    def write(self, path: str | Path | None = None) -> Path:
        """Write the model to ``path``, by default where it was read from."""
        target = Path(path) if path is not None else self.path
        ET.ElementTree(self.root).write(target, encoding="unicode")
        return target


def is_model(path: str | Path) -> bool:
    """True when ``path`` is an XML file whose root element is ``MorpheusModel``."""
    p = Path(path)
    if not (p.is_file() and p.suffix.lower() == ".xml"):
        return False
    with p.open(encoding="utf-8", errors="replace") as fh:
        head = fh.read(512)
    return "<MorpheusModel" in head
