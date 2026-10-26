"""Read and edit CompuCell3D projects with the standard library only.

A project is a folder with one ``.cc3d`` file. That file names the CC3DML model (``XMLScript``), the Python
main script (``PythonScript``) and any other resources, by paths relative to the project folder::

    <Simulation version="3.5.1">
       <XMLScript Type="XMLScript">Simulation/cellsorting.xml</XMLScript>
       <PythonScript Type="PythonScript">Simulation/cellsorting.py</PythonScript>
       <Resource Type="Python">Simulation/cellsortingSteppables.py</Resource>
    </Simulation>

The engine edits only what a run controls, in a copy of the project: the random seed in ``Potts/RandomSeed``,
the number of Monte Carlo steps in ``Potts/Steps``, and the ``CenterOfMass`` plugin, which ABMfact's output
steppable reads and which does not change the dynamics.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path

from ..base import EngineError


@dataclass
class CC3DProject:
    """One CompuCell3D project: where its files are, and its CC3DML model parsed for editing.

    Attributes:
        folder: The project folder.
        cc3d_file: The ``.cc3d`` file in it.
        xml_path: The CC3DML model, or ``None`` for a project specified in Python only.
        python_script: The Python main script, or ``None``.
        resources: Other files the ``.cc3d`` file names, such as steppable modules.
        root: The parsed CC3DML model, edited in place by the setters.
    """

    folder: Path
    cc3d_file: Path
    xml_path: Path | None
    python_script: Path | None
    resources: list[Path] = field(default_factory=list)
    root: ET.Element | None = None

    @classmethod
    def read(cls, folder: str | Path) -> CC3DProject:
        """Read the project in ``folder``; it must hold exactly one ``.cc3d`` file."""
        folder = Path(folder)
        if not folder.is_dir():
            raise EngineError(f"{folder} is not a CompuCell3D project folder")
        files = sorted(folder.glob("*.cc3d"))
        if len(files) != 1:
            raise EngineError(f"{folder} needs exactly one .cc3d file, found {len(files)}")
        sim = ET.parse(files[0]).getroot()  # noqa: S314 - the user's own project file

        def path_of(tag: str) -> Path | None:
            el = sim.find(tag)
            return folder / el.text.strip() if el is not None and el.text and el.text.strip() else None

        xml_path, script = path_of("XMLScript"), path_of("PythonScript")
        resources = [folder / r.text.strip() for r in sim.iterfind("Resource") if r.text and r.text.strip()]
        for p in (xml_path, script, *resources):
            if p is not None and not p.is_file():
                raise EngineError(f"{files[0].name} names {p.relative_to(folder)}, which does not exist")
        if script is None and xml_path is None:
            raise EngineError(f"{files[0].name} names neither a CC3DML model nor a Python main script")
        root = _parse(xml_path) if xml_path is not None else None
        return cls(folder, files[0], xml_path, script, resources, root)

    # -- reading -------------------------------------------------------------------------------------------

    def _potts(self) -> ET.Element:
        if self.root is None:
            raise EngineError("this project has no CC3DML model to edit; ABMfact runs XML-based projects")
        potts = self.root.find("Potts")
        if potts is None:
            raise EngineError(f"{self.xml_path} has no Potts section")
        return potts

    def dimensions(self) -> tuple[int, int, int]:
        """The lattice size in sites, from ``Potts/Dimensions``."""
        d = self._potts().find("Dimensions")
        if d is None:
            raise EngineError("Potts has no Dimensions")
        return int(d.get("x", 1)), int(d.get("y", 1)), int(d.get("z", 1))

    def steps(self) -> int:
        """The number of Monte Carlo steps the model runs."""
        return int(float(self._potts().findtext("Steps", "0")))

    def seed(self) -> int | None:
        """The seed in ``Potts/RandomSeed``, or ``None`` when the model draws its own."""
        text = self._potts().findtext("RandomSeed")
        return int(text) if text and text.strip() else None

    def cell_types(self) -> list[str]:
        """Cell type names in type-id order, without ``Medium``."""
        plugin = self.root.find("Plugin[@Name='CellType']") if self.root is not None else None
        if plugin is None:
            return []
        types = sorted(plugin.iterfind("CellType"), key=lambda t: int(t.get("TypeId", "0")))
        return [t.get("TypeName", "") for t in types if t.get("TypeName") != "Medium"]

    def fields(self) -> list[str]:
        """Chemical field names declared to any diffusion solver, in order and without repeats.

        Newer models name a field as ``<DiffusionField Name="...">``; older ones in
        ``<DiffusionData><FieldName>``.
        """
        if self.root is None:
            return []
        names: list[str] = []
        for f in self.root.iterfind("Steppable/DiffusionField"):
            name = f.get("Name") or (f.findtext("DiffusionData/FieldName") or "").strip()
            if name and name not in names:
                names.append(name)
        return names

    def python_files(self) -> list[Path]:
        """The main script and every Python resource: custom code that ABMfact runs but never translates."""
        files = [self.python_script] if self.python_script is not None else []
        return files + [r for r in self.resources if r.suffix == ".py"]

    # -- editing -------------------------------------------------------------------------------------------

    def set_seed(self, seed: int) -> None:
        """Set ``Potts/RandomSeed``, adding it if absent."""
        potts = self._potts()
        el = potts.find("RandomSeed")
        if el is None:
            el = ET.SubElement(potts, "RandomSeed")
        el.text = str(int(seed))

    def set_steps(self, steps: int) -> None:
        """Set ``Potts/Steps``, the number of Monte Carlo steps."""
        el = self._potts().find("Steps")
        if el is None:
            el = ET.SubElement(self._potts(), "Steps")
        el.text = str(int(steps))

    def ensure_center_of_mass(self) -> None:
        """Add the ``CenterOfMass`` plugin if the model lacks it; it tracks centroids and changes no dynamics."""
        if self.root is not None and self.root.find("Plugin[@Name='CenterOfMass']") is None:
            self.root.insert(1, ET.Element("Plugin", Name="CenterOfMass"))

    def set(self, path: str, value: str | float | int) -> None:
        """Set the text of the element at ``path`` (an ElementTree path from the CC3DML root)."""
        if self.root is None:
            raise EngineError("this project has no CC3DML model to edit")
        el = self.root.find(path)
        if el is None:
            raise EngineError(f"no element {path!r} in {self.xml_path.name if self.xml_path else 'the model'}")
        el.text = repr(float(value)) if isinstance(value, float) else str(value)

    def write_xml(self) -> None:
        """Write the edited CC3DML model back to ``xml_path``."""
        if self.root is not None and self.xml_path is not None:
            ET.ElementTree(self.root).write(self.xml_path, encoding="unicode")


def _parse(path: Path) -> ET.Element:
    """Parse a CC3DML file keeping its comments, so a written copy still reads like the original."""
    parser = ET.XMLParser(target=ET.TreeBuilder(insert_comments=True))  # noqa: S314
    return ET.parse(path, parser=parser).getroot()  # noqa: S314 - the user's own project file


def is_project(path: str | Path) -> bool:
    """True when ``path`` is a folder holding exactly one ``.cc3d`` file."""
    p = Path(path)
    return p.is_dir() and len(list(p.glob("*.cc3d"))) == 1
