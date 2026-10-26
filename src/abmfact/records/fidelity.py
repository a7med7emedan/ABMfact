"""Fidelity reports: what a translation mapped exactly, approximated, dropped, or left as native code."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Literal

Status = Literal["exact", "approximated", "dropped", "native"]
STATUSES: tuple[Status, ...] = ("exact", "approximated", "dropped", "native")

#: Fidelity tiers from the platform plan.
TIERS = {
    "T0": "native: the model's own files run unchanged",
    "T1": "structural: same paradigm, concepts mapped one to one",
    "T2": "calibrated: different mechanics, parameters fitted to match observables",
    "T3": "certified: a T1 or T2 translation that passed docking within stated tolerances",
}


@dataclass(frozen=True)
class FidelityEntry:
    """How one part of a model was carried over by a translation.

    Attributes:
        path: Where the item sits in the model, such as ``cell_types[tumor].custom``.
        status: One of ``exact``, ``approximated``, ``dropped`` or ``native``.
        note: What was changed or lost, if anything.
    """

    path: str
    status: Status
    note: str = ""


@dataclass
class FidelityReport:
    """What a translation mapped exactly, approximated, dropped or left as native code.

    Attributes:
        source: What was translated, such as ``abm-ir`` or ``physicell:<project>``.
        target: What it was translated into, in the same form.
        tier: The fidelity tier, a key of ``TIERS``.
        entries: One entry per path, in the order they were added.
    """

    source: str
    target: str
    tier: str = "T1"
    entries: list[FidelityEntry] = field(default_factory=list)

    def add(self, path: str, status: Status, note: str = "") -> None:
        """Record how one path was translated."""
        self.entries.append(FidelityEntry(path, status, note))

    def counts(self) -> dict[str, int]:
        """The number of entries with each status, including statuses with none."""
        return {s: sum(1 for e in self.entries if e.status == s) for s in STATUSES}

    @property
    def lossless(self) -> bool:
        """True when nothing was approximated, dropped or left native."""
        return all(e.status == "exact" for e in self.entries)

    def to_dict(self) -> dict[str, Any]:
        """The report as a dictionary for JSON, with the tier's meaning and the counts."""
        return {
            "source": self.source,
            "target": self.target,
            "tier": self.tier,
            "tier_meaning": TIERS.get(self.tier, ""),
            "counts": self.counts(),
            "entries": [asdict(e) for e in self.entries],
        }

    def to_markdown(self) -> str:
        """The report as a Markdown page: the tier, the counts and a table of every entry."""
        c = self.counts()
        lines = [
            f"# Fidelity report: {self.source} to {self.target}",
            "",
            f"Tier {self.tier} ({TIERS.get(self.tier, 'unknown tier')}).",
            "",
            ", ".join(f"{k} {v}" for k, v in c.items()),
            "",
            "| Path | Status | Note |",
            "|---|---|---|",
        ]
        lines += [f"| `{e.path}` | {e.status} | {e.note} |" for e in self.entries]
        return "\n".join(lines) + "\n"
