"""Read and edit a PhysiCell settings XML file (``config/PhysiCell_settings.xml``).

Element paths follow the PhysiCell 1.14.2 settings template. The file is parsed with the standard library
and written back with its comments kept; only the elements that are set change. This module reads and
writes the XML itself and does not use any third-party settings library.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any

SEED_PATHS = ("options/random_seed", "user_parameters/random_seed")
DT_PATHS = ("overall/dt_diffusion", "overall/dt_mechanics", "overall/dt_phenotype")


class SettingsError(ValueError):
    """The settings file is missing an element or holds a value that cannot be read."""


def _fmt(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, float):
        return repr(value)
    return str(value)


class SettingsXML:
    """A PhysiCell settings document. Paths are ElementTree paths relative to the root element."""

    def __init__(self, root: ET.Element, source: Path | None = None):
        self.root = root
        self.source = source

    @classmethod
    def read(cls, path: str | Path) -> SettingsXML:
        path = Path(path)
        parser = ET.XMLParser(target=ET.TreeBuilder(insert_comments=True))  # noqa: S314
        try:
            root = ET.parse(path, parser=parser).getroot()  # noqa: S314 - the user's own project file
        except ET.ParseError as exc:
            raise SettingsError(f"{path}: not valid XML: {exc}") from exc
        if root.tag != "PhysiCell_settings":
            raise SettingsError(f"{path}: root element is <{root.tag}>, expected <PhysiCell_settings>")
        return cls(root, path)

    def write(self, path: str | Path) -> Path:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        ET.ElementTree(self.root).write(path, encoding="unicode")
        return path

    # -- generic access ----------------------------------------------------------------------------------

    def find(self, path: str) -> ET.Element | None:
        return self.root.find(path)

    def get(self, path: str, default: str | None = None) -> str | None:
        el = self.root.find(path)
        return default if el is None or el.text is None else el.text.strip()

    def get_float(self, path: str) -> float:
        text = self.get(path)
        if text is None:
            raise SettingsError(f"missing <{path}>")
        try:
            return float(text)
        except ValueError as exc:
            raise SettingsError(f"<{path}> is {text!r}, not a number") from exc

    def get_bool(self, path: str, default: bool = False) -> bool:
        text = self.get(path)
        return default if text is None else text.strip().lower() in ("true", "1", "yes")

    def set(self, path: str, value: Any, create: bool = False) -> None:
        el = self.root.find(path)
        if el is None:
            if not create:
                raise SettingsError(f"missing <{path}>")
            el = self._create(path)
        el.text = _fmt(value)

    def _create(self, path: str) -> ET.Element:
        node = self.root
        for part in path.split("/"):
            child = node.find(part)
            if child is None:
                if "[" in part:
                    raise SettingsError(f"cannot create <{path}>: {part} has a predicate")
                child = ET.SubElement(node, part)
            node = child
        return node

    # -- parameters --------------------------------------------------------------------------------------

    def user_parameters(self) -> dict[str, tuple[str, str]]:
        """``{name: (type, text)}`` for every user parameter."""
        up = self.root.find("user_parameters")
        if up is None:
            return {}
        return {el.tag: (el.get("type", "double"), (el.text or "").strip()) for el in up if isinstance(el.tag, str)}

    def set_param(self, key: str, value: Any) -> None:
        """Set ``user_parameters.<name>`` or any element by path, for example ``overall/max_time`` or
        ``cell_definitions/cell_definition[@name='tumour']/phenotype/motility/speed``."""
        if key.startswith("user_parameters."):
            name = key.split(".", 1)[1]
            if name not in self.user_parameters():
                raise SettingsError(f"no user parameter {name!r}; known: {sorted(self.user_parameters())}")
            self.set(f"user_parameters/{name}", value)
        else:
            self.set(key, value)

    # -- run settings ------------------------------------------------------------------------------------

    def seed_paths(self) -> list[str]:
        return [p for p in SEED_PATHS if self.root.find(p) is not None]

    def set_seed(self, seed: int) -> list[str]:
        """Set every seed location present, adding ``options/random_seed`` (which the core reads) if absent.

        Some sample projects seed from ``user_parameters/random_seed`` in their custom code instead, so
        both are set when both exist. Returns the paths set.
        """
        paths = self.seed_paths()
        if "options/random_seed" not in paths:
            self.set("options/random_seed", int(seed), create=True)
            paths.insert(0, "options/random_seed")
        for p in paths:
            self.set(p, int(seed))
        return paths

    @property
    def is_2d(self) -> bool:
        return self.get_bool("domain/use_2D", default=False)

    def time_steps(self) -> dict[str, float]:
        return {p.split("/")[-1]: self.get_float(p) for p in DT_PATHS}

    def scale_time_steps(self, factor: float) -> None:
        for p in DT_PATHS:
            self.set(p, self.get_float(p) * factor)

    def voxel(self) -> dict[str, float]:
        return {k: self.get_float(f"domain/{k}") for k in ("dx", "dy", "dz")}

    def scale_voxels(self, factor: float) -> None:
        """Scale the voxel size; in 2D only ``dx`` and ``dy``, since ``dz`` is the slab thickness."""
        for k in ("dx", "dy") if self.is_2d else ("dx", "dy", "dz"):
            self.set(f"domain/{k}", self.get_float(f"domain/{k}") * factor)

    def set_threads(self, n: int) -> None:
        self.set("parallel/omp_num_threads", int(n), create=True)

    def set_output(self, folder: str = "output", interval: float | None = None, svg: bool = False) -> None:
        """Where full data goes and how often; SVG snapshots off by default (they do not affect dynamics)."""
        self.set("save/folder", folder, create=True)
        if interval is not None:
            self.set("save/full_data/interval", interval, create=True)
        self.set("save/full_data/enable", True, create=True)
        self.set("save/SVG/enable", svg, create=True)

    # -- model content -----------------------------------------------------------------------------------

    def substrates(self) -> list[ET.Element]:
        return self.root.findall("microenvironment_setup/variable")

    def cell_definitions(self) -> list[ET.Element]:
        return self.root.findall("cell_definitions/cell_definition")

    def ruleset(self) -> tuple[bool, Path | None]:
        """Whether a rules CSV is enabled, and its path relative to the run folder."""
        rs = self.root.find("cell_rules/rulesets/ruleset")
        if rs is None:
            return False, None
        enabled = rs.get("enabled", "false").lower() == "true"
        folder = (rs.findtext("folder") or ".").strip()
        name = (rs.findtext("filename") or "").strip()
        return enabled, (Path(folder) / name) if name else None

    def cell_positions(self) -> tuple[bool, Path | None]:
        cp = self.root.find("initial_conditions/cell_positions")
        if cp is None:
            return False, None
        enabled = cp.get("enabled", "false").lower() == "true"
        folder = (cp.findtext("folder") or ".").strip()
        name = (cp.findtext("filename") or "").strip()
        return enabled, (Path(folder) / name) if name else None
