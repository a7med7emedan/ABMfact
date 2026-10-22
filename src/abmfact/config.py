"""Audit files: one YAML document with the model, its parameters, the claims and the budget."""

from __future__ import annotations

import hashlib
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from .adapters import Adapter, load_adapter
from .checks import CHECK_NAMES
from .claims import Claim, ClaimError, parse_claims


class ConfigError(ValueError):
    """The audit file is not valid."""


@dataclass(frozen=True)
class Budget:
    seeds: int = 100
    seed_start: int = 0
    jobs: int = 1
    alpha: float = 0.05
    determinism_reruns: int = 8
    checks: tuple[str, ...] = ()

    @classmethod
    def from_mapping(cls, m: Mapping[str, Any] | None) -> Budget:
        m = dict(m or {})
        unknown = sorted(set(m) - set(cls.__dataclass_fields__))
        if unknown:
            raise ConfigError(f"unknown budget keys: {unknown}")
        b = cls(
            seeds=int(m.get("seeds", 100)),
            seed_start=int(m.get("seed_start", 0)),
            jobs=int(m.get("jobs", 1)),
            alpha=float(m.get("alpha", 0.05)),
            determinism_reruns=int(m.get("determinism_reruns", 8)),
            checks=tuple(m.get("checks") or ()),
        )
        if b.seeds < 3:
            raise ConfigError("budget.seeds must be at least 3")
        if not 0 < b.alpha < 0.5:
            raise ConfigError("budget.alpha must be between 0 and 0.5")
        if b.jobs < 1:
            raise ConfigError("budget.jobs must be at least 1")
        bad = sorted(set(b.checks) - set(CHECK_NAMES))
        if bad:
            raise ConfigError(f"unknown checks in budget.checks: {bad}")
        return b

    def to_dict(self) -> dict[str, Any]:
        return {
            "seeds": self.seeds,
            "seed_start": self.seed_start,
            "jobs": self.jobs,
            "alpha": self.alpha,
            "determinism_reruns": self.determinism_reruns,
            "checks": list(self.checks),
        }


@dataclass
class Audit:
    adapter: Adapter
    params: dict[str, Any]
    claims: list[Claim]
    budget: Budget = field(default_factory=Budget)
    model_cfg: dict[str, Any] = field(default_factory=dict)
    source: Path | None = None
    sha256: str | None = None
    title: str = "ABMfact audit"
    convergence: dict[str, Any] = field(default_factory=dict)


def parse_audit(doc: Mapping[str, Any], base_dir: Path | None = None) -> Audit:
    if not isinstance(doc, Mapping):
        raise ConfigError("the audit file must be a YAML mapping")
    unknown = sorted(set(doc) - {"title", "model", "params", "claims", "budget", "convergence"})
    if unknown:
        raise ConfigError(f"unknown top-level keys: {unknown}")
    if "model" not in doc:
        raise ConfigError("the audit file needs a 'model' section")
    model_cfg = dict(doc["model"])
    adapter = load_adapter(model_cfg, base_dir=base_dir)
    try:
        claims = parse_claims(list(doc.get("claims") or []), known_checks=(*CHECK_NAMES, "convergence"))
    except ClaimError as exc:
        raise ConfigError(str(exc)) from exc
    return Audit(
        adapter=adapter,
        params=dict(doc.get("params") or {}),
        claims=claims,
        budget=Budget.from_mapping(doc.get("budget")),
        model_cfg=model_cfg,
        title=str(doc.get("title", "ABMfact audit")),
        convergence=dict(doc.get("convergence") or {}),
    )


def load_audit(path: str | Path, overrides: Mapping[str, Any] | None = None) -> Audit:
    path = Path(path)
    raw = path.read_bytes()
    try:
        doc = yaml.safe_load(raw)
    except yaml.YAMLError as exc:
        raise ConfigError(f"{path}: not valid YAML: {exc}") from exc
    if overrides:
        doc = dict(doc)
        budget = dict(doc.get("budget") or {})
        budget.update({k: v for k, v in overrides.items() if v is not None})
        doc["budget"] = budget
    audit = parse_audit(doc, base_dir=path.parent.resolve())
    audit.source = path
    audit.sha256 = hashlib.sha256(raw).hexdigest()
    return audit


TEMPLATE = """\
# ABMfact audit file. Run with: abmfact run audit.yaml --out results/
title: My model audit

model:
  adapter: {adapter}
{model_block}
# Base parameters passed to every run.
params:
  beta: 0.4

# Conclusions stated as checkable claims. Write these from the paper before running anything.
claims:
  - id: C1
    statement: "most of the population is infected"
    metric: attack_rate          # name, name[i] or f(name) with f in mean, sum, min, max, argmax, ...
    test: greater_than           # greater_than | less_than | between | monotone_increasing | monotone_decreasing
    value: 0.5
  - id: C2
    statement: "attack rate rises with beta"
    metric: attack_rate
    test: monotone_increasing
    over: beta
    where: {{beta: [0.25, 0.4]}}
  # - id: C3
  #   statement: "the epidemic peaks within 25 time units"
  #   metric: peak_time
  #   test: less_than
  #   value: 25
  #   tolerance: 2.0             # equivalence margin; default is 10% of the baseline mean
  #   skip_checks:
  #     population_scale: "peak time legitimately grows with system size"

budget:
  seeds: 100                     # seeds per condition and parameter point
  jobs: 1                        # worker processes
  alpha: 0.05                    # one-sided; intervals are 90%
"""

MODEL_BLOCKS = {
    "python": "  target: mypackage.model:run     # run(params, seed, knobs) -> dict\n"
    "  supports:                       # the knobs your model honours\n"
    "    update_order: {values: [random, id], default: random}\n"
    "    agent_order: true\n"
    "    rng_streams: false             # if false, ABMfact remaps seeds itself\n"
    "    grid: false\n"
    "    dt_scale: false\n"
    "    pop_scale: false\n",
    "mesa": "  target: mypackage.model:MyModel\n"
    "  steps: 200\n"
    "  outputs:\n"
    "    attack_rate: mypackage.model:attack_rate   # callable(model) -> float\n"
    "    prevalence: datacollector:Infected\n"
    "  knob_kwargs:                    # knob -> constructor keyword\n"
    "    update_order: activation\n"
    "  update_orders: [random, id]\n",
    "netlogo": "  path: model.nlogo\n"
    "  ticks: 200\n"
    "  reporters:\n"
    '    attack_rate: "count turtles with [ever-infected?] / count turtles"\n'
    '    prevalence[]: "count turtles with [infected?] / count turtles"\n'
    "  knob_globals:\n"
    "    update_order: update-order\n"
    "  update_orders: [random, id]\n",
    "command": '  argv: ["{python}", "model.py"]  # reads JSON on stdin, prints JSON\n'
    "  supports:\n"
    "    agent_order: false\n",
    "engine": "  engine: reference               # see: abmfact engines\n"
    "  model: model.yaml                # an ABM-IR file; outputs are its observables\n"
    "  # store: .abmfact-store          # reuse identical runs across audits\n",
}


def template(adapter: str = "python") -> str:
    if adapter not in MODEL_BLOCKS:
        raise ConfigError(f"unknown adapter {adapter!r}; expected one of {sorted(MODEL_BLOCKS)}")
    return TEMPLATE.format(adapter=adapter, model_block=MODEL_BLOCKS[adapter])
