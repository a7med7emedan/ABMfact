"""Signal and behaviour names for rules, as PhysiCell 1.14.2 defines them.

The lists follow ``setup_signal_behavior_dictionaries`` in ``core/PhysiCell_signal_behavior.cpp`` of
PhysiCell 1.14.2. Names that contain a substrate or cell type are matched as patterns, so a rule can
only name substrates and cell types that the model defines.
"""

from __future__ import annotations

from collections.abc import Iterable

FIXED_SIGNALS = frozenset(
    {
        "pressure",
        "volume",
        "contact with live cell",
        "contact with live cells",
        "contact with dead cell",
        "contact with dead cells",
        "contact with apoptotic cell",
        "contact with apoptotic cells",
        "contact with necrotic cell",
        "contact with necrotic cells",
        "contact with other dead cell",
        "contact with other dead cells",
        "contact with basement membrane",
        "contact with BM",
        "damage",
        "damage delivered",
        "total damage delivered",
        "attacking",
        "is attacking",
        "dead",
        "is dead",
        "total attack time",
        "time",
        "current time",
        "global time",
        "apoptotic",
        "is_apoptotic",
        "necrotic",
        "is_necrotic",
    }
)

SUBSTRATE_SIGNALS = ("{}", "intracellular {}", "internalized {}", "{} gradient", "grad({})", "gradient of {}")
TYPE_SIGNALS = ("contact with {}", "immunogenicity to {}")

FIXED_BEHAVIOURS = frozenset(
    {
        "cycle entry",
        "apoptosis",
        "necrosis",
        "migration speed",
        "migration bias",
        "migration persistence time",
        "cell-cell adhesion",
        "cell-cell adhesion elastic constant",
        "relative maximum adhesion distance",
        "cell-cell repulsion",
        "cell-BM adhesion",
        "cell-membrane adhesion",
        "cell-BM repulsion",
        "cell-membrane repulsion",
        "phagocytose apoptotic cell",
        "phagocytosis of apoptotic cell",
        "phagocytosis of apoptotic cells",
        "phagocytose necrotic cell",
        "phagocytosis of necrotic cell",
        "phagocytosis of necrotic cells",
        "phagocytose other dead cell",
        "phagocytosis of other dead cell",
        "phagocytosis of other dead cells",
        "is_movable",
        "movable",
        "is movable",
        "cell attachment rate",
        "cell detachment rate",
        "maximum number of cell attachments",
        "attack damage rate",
        "attack duration",
        "damage rate",
        "damage repair rate",
    }
)

SUBSTRATE_BEHAVIOURS = (
    "{} secretion",
    "{} secretion target",
    "{} secretion saturation density",
    "{} uptake",
    "{} export",
    "chemotactic response to {}",
    "chemotactic sensitivity to {}",
)
TYPE_BEHAVIOURS = (
    "adhesive affinity to {}",
    "phagocytose {}",
    "phagocytosis of {}",
    "attack {}",
    "fuse to {}",
    "transform to {}",
    "transition to {}",
    "asymmetric division to {}",
    "immunogenicity to {}",
)
CUSTOM_PREFIXES = ("custom:", "custom: ", "custom ")


def _matches(name: str, fixed: frozenset[str], by_sub: tuple[str, ...], by_type: tuple[str, ...], subs, types) -> bool:
    if name in fixed or name.startswith(CUSTOM_PREFIXES):
        return True
    if any(name == p.format(s) for p in by_sub for s in subs):
        return True
    if any(name == p.format(t) for p in by_type for t in types):
        return True
    return any(
        name.startswith(p.format("cell type ")) and name[len(p.format("cell type ")) :].isdigit() for p in by_type
    )


def is_signal(name: str, substrates: Iterable[str], cell_types: Iterable[str]) -> bool:
    """True when ``name`` is a PhysiCell signal for a model with these substrates and cell types."""
    return _matches(name, FIXED_SIGNALS, SUBSTRATE_SIGNALS, TYPE_SIGNALS, list(substrates), list(cell_types))


def is_behaviour(name: str, substrates: Iterable[str], cell_types: Iterable[str]) -> bool:
    """True when ``name`` is a PhysiCell behaviour for a model with these substrates and cell types."""
    if name.startswith("exit from cycle phase ") and name.rsplit(" ", 1)[-1].isdigit():
        return True
    return _matches(name, FIXED_BEHAVIOURS, SUBSTRATE_BEHAVIOURS, TYPE_BEHAVIOURS, list(substrates), list(cell_types))
