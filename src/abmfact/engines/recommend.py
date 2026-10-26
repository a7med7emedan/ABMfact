"""Recommend which engine suits a model, from what each engine declares about itself.

Nothing here is specific to one engine. For each engine, the recommendation asks the engine contract four
questions: can it run this model (:meth:`~abmfact.engines.base.Engine.refuse`), how faithfully
(:meth:`~abmfact.engines.base.Engine.translation`), which audit checks become testable
(:meth:`~abmfact.engines.base.Engine.support_for`), and is it installed here
(:meth:`~abmfact.engines.base.Engine.availability`). A new engine improves the recommendations without any
change to this module.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import asdict, dataclass, field
from typing import Any

from ..checks import CHECKS
from ..knobs import Knobs
from .base import Engine
from .registry import list_engines

#: The convergence check is not an audit check, but it is testable wherever the time step is.
CONVERGENCE = "convergence"


@dataclass
class Recommendation:
    """How well one engine suits one model.

    Attributes:
        engine: The engine's registered name.
        description: The engine's one-line description.
        runs: Whether the engine can run the model as it stands.
        reasons: Why it cannot, when ``runs`` is false.
        route: ``as given`` (a native project), ``reads ABM-IR`` (the engine's own format) or ``translated``.
        tier: The fidelity tier of the route.
        fidelity: The counts of exact, approximated, dropped and native parts, for a translated route.
        losses: The parts that are not exact, as ``(path, status, note)``, for a translated route.
        checks: The checks that become testable with this engine, in the order an audit runs them.
        available: Whether the engine runs on this machine now.
        detail: What is installed, or what is missing.
    """

    engine: str
    description: str
    runs: bool
    reasons: list[str] = field(default_factory=list)
    route: str = ""
    tier: str = ""
    fidelity: dict[str, int] = field(default_factory=dict)
    losses: list[tuple[str, str, str]] = field(default_factory=list)
    checks: list[str] = field(default_factory=list)
    available: bool = False
    detail: str = ""

    @property
    def loss(self) -> int:
        """A single number for ranking: each dropped or native part counts ten, each approximation one."""
        f = self.fidelity
        return 10 * (f.get("dropped", 0) + f.get("native", 0)) + f.get("approximated", 0)

    def rank_key(self) -> tuple[int, int, int, int, str]:
        """Sort key: engines that run first, then installed ones, then least loss, then most checks."""
        return (not self.runs, not self.available, self.loss, -len(self.checks), self.engine)

    def to_dict(self) -> dict[str, Any]:
        """The recommendation as a JSON-ready dictionary."""
        d = asdict(self)
        d["losses"] = [list(x) for x in self.losses]
        return d


def testable_checks(engine: Engine, model: Any) -> list[str]:
    """The audit checks, plus convergence, that ``engine`` can test for ``model``."""
    support = engine.support_for(model)
    names = [c.name for c in CHECKS if c.variants(support) is not None]
    if support.dt_scale:
        names.append(CONVERGENCE)
    return names


def assess(name: str, engine: Engine, model: Any) -> Recommendation:
    """How well ``engine`` suits ``model``; errors while loading become reasons, not exceptions."""
    rec = Recommendation(name, type(engine).description, runs=False)
    avail = engine.availability()
    rec.available, rec.detail = avail.ok, avail.detail
    try:
        loaded = engine.load(model)
        rec.reasons = engine.refuse(loaded, Knobs())
    except (OSError, ValueError, RuntimeError) as exc:
        rec.reasons = [str(exc)]
        return rec
    if rec.reasons:
        return rec
    rec.runs = True
    report = engine.translation(loaded)
    if report is None:
        from ..ir import IRModel

        rec.route = "reads ABM-IR" if isinstance(loaded, IRModel) else "as given"
        rec.tier = "T0"
    else:
        rec.route, rec.tier = "translated", report.tier
        rec.fidelity = report.counts()
        rec.losses = [(e.path, e.status, e.note) for e in report.entries if e.status != "exact"]
    rec.checks = testable_checks(engine, loaded)
    return rec


def recommend(model: Any, engines: Mapping[str, Engine] | None = None) -> list[Recommendation]:
    """Every engine's fit for ``model``, best first.

    Args:
        model: An ABM-IR model or file, or a native project folder.
        engines: Engines to consider, by name; by default one instance of every registered engine.

    Returns:
        One :class:`Recommendation` per engine: engines that can run the model first, then installed ones,
        then the least translation loss, then the most testable checks.
    """
    if engines is None:
        engines = {name: cls() for name, cls in list_engines().items()}
    return sorted((assess(name, engine, model) for name, engine in engines.items()), key=Recommendation.rank_key)
