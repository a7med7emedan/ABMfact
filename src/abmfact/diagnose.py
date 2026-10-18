"""Map the pattern of flagged checks to the most likely cause and a fix to try first."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass


@dataclass(frozen=True)
class Rule:
    check: str
    unless: tuple[str, ...]
    cause: str
    fix: str


RULES: tuple[Rule, ...] = (
    Rule(
        "determinism",
        (),
        "Hidden nondeterminism: the same seed gives different results.",
        "Look for an unseeded generator (the global random or numpy.random module), iteration over "
        "sets or dicts keyed by objects, wall-clock time, threads, or parallel reductions.",
    ),
    Rule(
        "update_order",
        ("agent_order",),
        "Stale or partial state reads: agents see a state that depends on the scheduling path.",
        "Check that a synchronous update reads only last step's state and writes every agent's new "
        "state, and that buffers are not reused between steps without being refreshed.",
    ),
    Rule(
        "update_order",
        (),
        "Results depend on activation order, and also on agent labels.",
        "Look for ties broken by position in a list, or resources handed out in iteration order.",
    ),
    Rule(
        "agent_order",
        (),
        "Results depend on how agents are labelled or stored.",
        "Look for agents[:k], ties broken by id, or sampling that walks a list in creation order "
        "instead of drawing at random.",
    ),
    Rule(
        "rng_streams",
        (),
        "Random streams are not properly tied to the seed, or processes share or reuse streams.",
        "Derive one generator per process from the replicate seed (for example numpy SeedSequence.spawn) "
        "and check no generator is built from a constant.",
    ),
    Rule(
        "grid",
        (),
        "Results depend on where the world sits in the array: a boundary or indexing problem.",
        "Check wrap-around at every edge, neighbour offsets, and that no code uses absolute array "
        "positions where it means world positions.",
    ),
    Rule(
        "time_step",
        (),
        "Rates are not converted correctly when the time step changes.",
        "Per-step probabilities should be 1 - exp(-rate * dt) with the current dt; durations and "
        "recording intervals should be in time units, not steps.",
    ),
    Rule(
        "population_scale",
        (),
        "An intensive result changes with system size: density is not held fixed, or a count is hard-coded.",
        "Look for sizes, counts or normalisers fixed at the calibration size, and for density-dependent "
        "terms where frequency dependence was meant.",
    ),
)


def diagnose(flagged: Iterable[str]) -> list[dict[str, str]]:
    """Return one finding per flagged check, choosing the most specific rule that applies."""
    flagged_set = set(flagged)
    findings = []
    for check in sorted(flagged_set, key=lambda c: [r.check for r in RULES].index(c)):
        for rule in RULES:
            if rule.check != check:
                continue
            if any(u in flagged_set for u in rule.unless):
                continue
            findings.append({"check": check, "cause": rule.cause, "fix": rule.fix})
            break
    return findings
