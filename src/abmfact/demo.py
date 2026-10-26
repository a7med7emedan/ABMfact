"""The planted-fault matrix: every zoo model audited by every check."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from .audit import FLAGGED, AuditResult, run_audit
from .checks import CHECK_NAMES
from .config import parse_audit
from .zoo import EXPECTED_ALSO, EXPECTED_CHECK, MODELS, audit_document

#: Verification columns beyond the seven audit checks.
EXTRA_COLUMNS = ("convergence",)


def expected(model: str) -> set[str]:
    """Every check that must flag ``model``: its own check plus any documented overlap."""
    own = EXPECTED_CHECK.get(model)
    return ({own} if own else set()) | set(EXPECTED_ALSO.get(model, ()))


@dataclass
class DemoResult:
    """``flags[model][check]`` is True when any claim was flagged by that check."""

    flags: dict[str, dict[str, bool]]
    results: dict[str, AuditResult]
    convergence: dict[str, Any] = field(default_factory=dict)

    def problems(self) -> list[str]:
        """Every deviation from the expected matrix. Empty means the validation passed."""
        out = []
        for model, row in self.flags.items():
            want = expected(model)
            for check, flagged in row.items():
                if check in want and not flagged:
                    out.append(f"{model}: missed by {check}")
                elif check not in want and flagged:
                    out.append(f"{model}: false alarm from {check}")
        return out

    def columns(self) -> list[str]:
        """Matrix columns: every audit check, then any verification column present, such as ``convergence``."""
        present = {c for row in self.flags.values() for c in row}
        return [*CHECK_NAMES, *(c for c in EXTRA_COLUMNS if c in present)]

    def table(self) -> str:
        """Render the detection matrix as plain text.

        A cell reads ``CAUGHT`` or ``MISSED`` for an expected flag, ``FALSE`` for a false alarm, and a dot
        when the check correctly stayed quiet.
        """
        names = self.columns()
        short = {c: c.replace("_", " ") for c in names}
        width = max(len(m) for m in self.flags) + 2
        cols = [max(len(short[c]), 6) for c in names]
        head = " " * width + "  ".join(short[c].ljust(w) for c, w in zip(names, cols, strict=True))
        lines = [head]
        for model, row in self.flags.items():
            want = expected(model)
            cells = []
            for c, w in zip(names, cols, strict=True):
                flagged = row.get(c, False)
                mark = ("CAUGHT" if c in want else "FALSE") if flagged else ("MISSED" if c in want else ".")
                cells.append(mark.ljust(w))
            lines.append((model.ljust(width) + "  ".join(cells)).rstrip())
        return "\n".join(lines)


def run_demo(
    seeds: int = 100,
    jobs: int = 1,
    models: list[str] | None = None,
    progress: Callable[[str], None] | None = None,
    convergence: bool = True,
) -> DemoResult:
    """Audit every zoo model with every check and, unless ``convergence`` is false, check convergence."""
    flags: dict[str, dict[str, bool]] = {}
    results: dict[str, AuditResult] = {}
    conv: dict[str, Any] = {}
    for model in models or list(MODELS):
        if progress:
            progress(model)
        audit = parse_audit(audit_document(model, seeds=seeds, jobs=jobs))
        res = run_audit(audit)
        results[model] = res
        row = {c: False for c in CHECK_NAMES}
        for v in res.verdicts:
            if v["verdict"] in FLAGGED:
                row[v["check"]] = True
        if convergence:
            from .verify.convergence import converge

            c = converge(audit)
            conv[model] = c
            row["convergence"] = c.any_not_converged
        flags[model] = row
    return DemoResult(flags, results, conv)
