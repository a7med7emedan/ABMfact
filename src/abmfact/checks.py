"""The checks: changes that a correct implementation must not care about."""

from __future__ import annotations

from dataclasses import dataclass

from .knobs import Knobs, Support


@dataclass(frozen=True)
class Variant:
    """One perturbed condition. ``shuffle_ids`` asks for a per-seed relabelling permutation."""

    check: str
    label: str
    knobs: Knobs
    shuffle_ids: bool = False


@dataclass(frozen=True)
class Check:
    """One audit check: a change that a correct implementation must not care about.

    Attributes:
        name: The check's name, as used in audit files and results.
        question: The question the check answers, in plain words.
        compare_spread: Whether the spread over seeds is compared as well as the mean.
    """

    name: str
    question: str
    compare_spread: bool = True

    def variants(self, support: Support) -> list[Variant] | None:
        """Variants to run, or ``None`` when the model does not declare the needed knob."""
        n = self.name
        if n == "determinism":
            return []
        if n == "update_order":
            if len(support.update_order) < 2:
                return None
            default = support.update_order_default or support.update_order[0]
            return [
                Variant(n, f"update_order={v}", Knobs(update_order=v)) for v in support.update_order if v != default
            ]
        if n == "agent_order":
            if not support.agent_order:
                return None
            return [Variant(n, "agent_order=shuffled", Knobs(agent_order="shuffled"), shuffle_ids=True)]
        if n == "rng_streams":
            return [Variant(n, "rng_streams=independent", Knobs(rng_streams="independent"))]
        if n == "grid":
            if not support.grid:
                return None
            return [
                Variant(n, "grid_offset=(0.5,0.5)", Knobs(grid_offset=(0.5, 0.5))),
                Variant(n, "grid_offset=(0.25,0)", Knobs(grid_offset=(0.25, 0.0))),
                Variant(n, "grid_rotation=90", Knobs(grid_rotation=90)),
            ]
        if n == "time_step":
            if not support.dt_scale:
                return None
            return [Variant(n, "dt_scale=0.5", Knobs(dt_scale=0.5))]
        if n == "population_scale":
            if not support.pop_scale:
                return None
            return [Variant(n, "pop_scale=2", Knobs(pop_scale=2.0))]
        raise ValueError(f"unknown check {n!r}")


CHECKS: tuple[Check, ...] = (
    Check("determinism", "Does the same seed always give the same result?"),
    Check("update_order", "Does the conclusion survive a different activation order within a step?"),
    Check("agent_order", "Does the conclusion survive relabelling and reordering the agents?"),
    Check("rng_streams", "Does the conclusion survive fresh, independent random streams?"),
    Check("grid", "Does the conclusion survive moving or rotating the world on the torus?"),
    Check("time_step", "Does the conclusion survive halving the time step?"),
    # Spread legitimately shrinks with system size, so only means are compared here.
    Check(
        "population_scale",
        "Does the conclusion survive doubling the population at fixed density?",
        compare_spread=False,
    ),
)

CHECK_NAMES: tuple[str, ...] = tuple(c.name for c in CHECKS)
CHECKS_BY_NAME = {c.name: c for c in CHECKS}


def select_checks(names: list[str] | None) -> list[Check]:
    """Return the named checks in their standard order, or every check when ``names`` is empty or ``None``.

    Raises ``ValueError`` for an unknown check name.
    """
    if not names:
        return list(CHECKS)
    unknown = sorted(set(names) - set(CHECK_NAMES))
    if unknown:
        raise ValueError(f"unknown checks {unknown}; available: {', '.join(CHECK_NAMES)}")
    return [c for c in CHECKS if c.name in names]
