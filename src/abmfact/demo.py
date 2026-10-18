"""The planted-fault matrix: every zoo model audited by every check."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from .audit import FLAGGED, AuditResult, run_audit
from .checks import CHECK_NAMES
from .config import parse_audit
from .zoo import EXPECTED_CHECK, MODELS, audit_document


@dataclass
class DemoResult:
    """``flags[model][check]`` is True when any claim was flagged by that check."""

    flags: dict[str, dict[str, bool]]
    results: dict[str, AuditResult]

    def problems(self) -> list[str]:
        """Every deviation from the expected diagonal. Empty means the validation passed."""
        out = []
        for model, row in self.flags.items():
            expected = EXPECTED_CHECK.get(model)
            for check, flagged in row.items():
                if check == expected and not flagged:
                    out.append(f"{model}: missed by {check}")
                elif check != expected and flagged:
                    out.append(f"{model}: false alarm from {check}")
        return out

    def table(self) -> str:
        short = {c: c.replace("_", " ") for c in CHECK_NAMES}
        width = max(len(m) for m in self.flags) + 2
        cols = [max(len(short[c]), 6) for c in CHECK_NAMES]
        head = " " * width + "  ".join(short[c].ljust(w) for c, w in zip(CHECK_NAMES, cols, strict=True))
        lines = [head]
        for model, row in self.flags.items():
            expected = EXPECTED_CHECK.get(model)
            cells = []
            for c, w in zip(CHECK_NAMES, cols, strict=True):
                mark = ("CAUGHT" if c == expected else "FALSE") if row[c] else ("MISSED" if c == expected else ".")
                cells.append(mark.ljust(w))
            lines.append((model.ljust(width) + "  ".join(cells)).rstrip())
        return "\n".join(lines)


def run_demo(
    seeds: int = 100,
    jobs: int = 1,
    models: list[str] | None = None,
    progress: Callable[[str], None] | None = None,
) -> DemoResult:
    flags: dict[str, dict[str, bool]] = {}
    results: dict[str, AuditResult] = {}
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
        flags[model] = row
    return DemoResult(flags, results)
