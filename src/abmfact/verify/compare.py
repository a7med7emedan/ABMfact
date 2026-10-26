"""Compare one model across engines (docking) against a declared disagreement budget.

The same ABM-IR model runs in each engine over the same seeds. For every metric the engines' means are
compared with the baseline engine's by a Welch interval for the difference, and the interval is read against
the metric's budget with the same three-outcome test as the audit:

* ``AGREE``: the whole interval lies within the budget;
* ``DISAGREE``: the whole interval lies outside it;
* ``INCONCLUSIVE``: neither; the result says how many seeds would settle it;
* ``NOT_RUN``: the engine refused the model, is not installed here, or every run failed.

Agreement within a budget is numerical equivalence. A comparison may also name threshold claims (from an audit
file); each engine's status for each claim (holds, fails, uncertain) shows whether the engines draw the same
conclusion, which is relational equivalence (Axtell et al., 2000). A translated model is a second
implementation, so each engine's fidelity report is kept beside its verdicts.

Budgets belong to the comparison file and are meant to be written before any run, as audit claims are.

Axtell, R., Axelrod, R., Epstein, J. M. and Cohen, M. D. (1996). Aligning simulation models: a case study and
results. *Computational and Mathematical Organization Theory*, 1(2), 123-141.
"""

from __future__ import annotations

import json
import platform
import tempfile
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import numpy as np
import yaml

from .. import __version__
from .. import stats as st
from ..audit import _clean, _json_default
from ..claims import Claim, ClaimError, Metric, parse_claims
from ..knobs import Knobs

VERDICTS = ("AGREE", "DISAGREE", "INCONCLUSIVE", "NOT_RUN")
THRESHOLD_TESTS = ("greater_than", "less_than", "between")


class CompareError(ValueError):
    """A comparison file or option is not valid."""


@dataclass(frozen=True)
class Budget:
    """How far an engine's mean may sit from the baseline's and still agree.

    Exactly one of ``absolute`` (in the metric's units) or ``relative`` (a fraction of the absolute baseline
    mean) is set.
    """

    absolute: float | None = None
    relative: float | None = None

    @classmethod
    def parse(cls, raw: Any) -> Budget:
        """A number is an absolute budget; ``{relative: f}`` or ``{absolute: x}`` say which."""
        if isinstance(raw, int | float) and not isinstance(raw, bool):
            return cls(absolute=float(raw))
        if isinstance(raw, Mapping) and len(raw) == 1 and set(raw) <= {"absolute", "relative"}:
            ((key, value),) = raw.items()
            if not isinstance(value, int | float) or value <= 0:
                raise CompareError(f"budget {key} must be a positive number, got {value!r}")
            return cls(**{key: float(value)})
        raise CompareError(f"a budget is a number or {{relative: f}} or {{absolute: x}}, got {raw!r}")

    def margin(self, baseline: np.ndarray) -> float:
        """The budget in the metric's units, given the baseline engine's values."""
        if self.absolute is not None:
            return self.absolute
        assert self.relative is not None
        return self.relative * abs(float(np.mean(baseline)))

    def describe(self) -> str:
        """The budget in words."""
        return f"{self.absolute:g}" if self.absolute is not None else f"{self.relative:.0%} of the baseline mean"


@dataclass(frozen=True)
class CompareMetric:
    """One compared metric, its budget and why the budget is that size."""

    metric: Metric
    budget: Budget
    note: str = ""


@dataclass
class Comparison:
    """A parsed comparison: one model, several engines, metrics with budgets, and optional claims.

    Attributes:
        title: A short title.
        model: The ABM-IR model file.
        engines: Engine names; the first is the baseline unless ``baseline`` names another.
        baseline: The engine every other is compared with.
        options: Engine options by engine name, as for ``run-model --option``.
        params: ABM-IR parameters applied in every engine.
        seeds: Seeds per engine (``0`` to ``seeds - 1``).
        jobs: Runs at once.
        alpha: One-sided significance level; intervals cover ``1 - 2*alpha``.
        metrics: The compared metrics.
        claims: Threshold claims whose status is reported per engine.
        seeds_by_engine: Fewer seeds for a slow engine, with the reason in ``notes``.
        notes: Free-text notes kept in the result.
    """

    title: str
    model: Path
    engines: list[str]
    baseline: str
    metrics: list[CompareMetric]
    options: dict[str, dict[str, Any]] = field(default_factory=dict)
    params: dict[str, Any] = field(default_factory=dict)
    seeds: int = 10
    jobs: int = 1
    alpha: float = 0.05
    claims: list[Claim] = field(default_factory=list)
    seeds_by_engine: dict[str, int] = field(default_factory=dict)
    notes: dict[str, str] = field(default_factory=dict)

    def seeds_for(self, engine: str) -> int:
        """The number of seeds ``engine`` runs."""
        return int(self.seeds_by_engine.get(engine, self.seeds))


KEYS = {"title", "model", "engines", "baseline", "metrics", "options", "params", "seeds", "jobs", "alpha",
        "claims_from", "seeds_by_engine", "notes"}  # fmt: skip


def load_comparison(path: str | Path, overrides: Mapping[str, Any] | None = None) -> Comparison:
    """Read a comparison file; relative paths are resolved against its folder.

    ``overrides`` replaces top-level keys, such as ``seeds`` or ``jobs`` from the command line.
    """
    path = Path(path)
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    raw.update({k: v for k, v in (overrides or {}).items() if v is not None})
    unknown = sorted(set(raw) - KEYS)
    if unknown:
        raise CompareError(f"unknown comparison keys: {unknown}")
    for key in ("model", "engines", "metrics"):
        if key not in raw:
            raise CompareError(f"a comparison needs {key!r}")
    engines = [str(e) for e in raw["engines"]]
    if len(engines) < 2 or len(set(engines)) != len(engines):
        raise CompareError("a comparison needs at least two different engines")
    baseline = str(raw.get("baseline", engines[0]))
    if baseline not in engines:
        raise CompareError(f"the baseline {baseline!r} is not among the engines {engines}")
    metrics = []
    for m in raw["metrics"]:
        try:
            metrics.append(CompareMetric(Metric.parse(m["metric"]), Budget.parse(m["budget"]), str(m.get("note", ""))))
        except (KeyError, TypeError, ClaimError) as exc:
            raise CompareError(f"metric entry {m!r}: {exc}") from exc
    claims: list[Claim] = []
    if raw.get("claims_from"):
        audit = yaml.safe_load((path.parent / raw["claims_from"]).read_text(encoding="utf-8")) or {}
        for c in parse_claims(audit.get("claims", [])):
            if c.test in THRESHOLD_TESTS and not c.where:
                claims.append(c)
    seeds = int(raw.get("seeds", 10))
    if seeds < 3:
        raise CompareError("a comparison needs at least 3 seeds per engine")
    alpha = float(raw.get("alpha", 0.05))
    if not 0 < alpha < 0.5:
        raise CompareError("alpha must lie between 0 and 0.5")
    return Comparison(
        title=str(raw.get("title", path.stem)),
        model=(path.parent / raw["model"]).resolve(),
        engines=engines,
        baseline=baseline,
        metrics=metrics,
        options={str(k): dict(v or {}) for k, v in (raw.get("options") or {}).items()},
        params=dict(raw.get("params") or {}),
        seeds=seeds,
        jobs=int(raw.get("jobs", 1)),
        alpha=alpha,
        claims=claims,
        seeds_by_engine={str(k): int(v) for k, v in (raw.get("seeds_by_engine") or {}).items()},
        notes={str(k): str(v) for k, v in (raw.get("notes") or {}).items()},
    )


# -- running ---------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class _Run:
    engine: str
    seed: int


class _CompareTask:
    """One engine run in a worker process. Workers read the store but never write it; the parent does."""

    def __init__(self, cmp: Comparison, workdir: Path, store_root: Path | None):
        self.model = str(cmp.model)
        self.params = dict(cmp.params)
        self.options = cmp.options
        self.workdir = workdir
        self.store_root = store_root

    def __call__(self, job: _Run) -> tuple[_Run, Any]:
        from ..engines import get_engine
        from ..records.store import RunStore

        store = RunStore(self.store_root) if self.store_root else None
        work = self.workdir / job.engine / f"seed-{job.seed}"
        try:
            engine = get_engine(job.engine, **self.options.get(job.engine, {}))
            prepared = engine.prepare(self.model, dict(self.params), Knobs(), job.seed, work)
            if store is not None and (hit := store.get(prepared.request)) is not None:
                return job, hit
            return job, engine.collect(engine.run(prepared))
        except Exception as exc:  # noqa: BLE001 - one failed run is reported, not fatal
            return job, f"{type(exc).__name__}: {exc}"


def _engine_status(name: str, cmp: Comparison) -> tuple[str | None, dict[str, Any]]:
    """``(reason not to run, description)`` for one engine, with its fidelity report when it translates."""
    from ..engines import get_engine

    try:
        engine = get_engine(name, **cmp.options.get(name, {}))
    except Exception as exc:  # noqa: BLE001 - an unknown or broken engine is reported
        return f"{type(exc).__name__}: {exc}", {}
    info: dict[str, Any] = {"description": getattr(engine, "description", "")}
    reasons = engine.refuse(str(cmp.model), Knobs())
    if reasons:
        return "; ".join(reasons), info
    avail = engine.availability()
    info["version"] = avail.version
    if not avail.ok:
        return f"not installed here: {avail.detail}", info
    translate = getattr(engine, "translation", None)
    report = translate(str(cmp.model)) if translate else None
    if report is not None:
        info["tier"] = report.tier
        info["fidelity"] = report.counts()
        info["losses"] = [[e.path, e.status, e.note] for e in report.entries if e.status != "exact"]
    else:
        info["tier"] = "T0"
    return None, info


def _warm_up(name: str, cmp: Comparison, workdir: Path) -> None:
    """Prepare one run in this process, so builds and translation caches exist before workers start."""
    import shutil

    from ..engines import get_engine

    engine = get_engine(name, **cmp.options.get(name, {}))
    with tempfile.TemporaryDirectory(dir=workdir) as tmp:
        engine.prepare(str(cmp.model), dict(cmp.params), Knobs(), 0, Path(tmp))
        shutil.rmtree(tmp, ignore_errors=True)


@dataclass
class CompareResult:
    """The outcome of a comparison.

    Attributes:
        data: The full result: tool and environment, the comparison, each engine's runs, a verdict per metric
            and engine, each engine's claim statuses, and a summary.
    """

    data: dict[str, Any]

    @property
    def verdicts(self) -> list[dict[str, Any]]:
        """One verdict per metric and compared engine."""
        return self.data["verdicts"]

    @property
    def any_disagree(self) -> bool:
        """True when any engine disagrees with the baseline on any metric."""
        return any(v["verdict"] == "DISAGREE" for v in self.verdicts)

    def to_json(self, include_timestamp: bool = True) -> str:
        """The result as JSON; without the timestamp it is the same for the same runs."""
        d = dict(self.data)
        if not include_timestamp:
            d.pop("created", None)
        return json.dumps(_clean(d), indent=2, default=_json_default) + "\n"


def compare(
    cmp: Comparison,
    workdir: str | Path | None = None,
    store: str | Path | None = None,
    progress: Callable[[int, int], None] | None = None,
) -> CompareResult:
    """Run the model in every engine over its seeds and compare each metric with the baseline engine.

    Args:
        cmp: The comparison.
        workdir: Where run folders go; a temporary folder by default.
        store: A run store; finished runs are kept there and reused when the comparison is run again.
        progress: Called with ``(done, total)`` after each run.
    """
    from ..records.store import RunStore
    from ..runtime.local import LocalBackend

    owned = tempfile.TemporaryDirectory() if workdir is None else None
    root = Path(owned.name if owned else workdir)  # type: ignore[arg-type]
    root.mkdir(parents=True, exist_ok=True)
    run_store = RunStore(store) if store else None
    engines: dict[str, dict[str, Any]] = {}
    jobs: list[_Run] = []
    for name in cmp.engines:
        reason, info = _engine_status(name, cmp)
        if reason is None:
            try:
                _warm_up(name, cmp, root)
            except Exception as exc:  # noqa: BLE001 - an engine that cannot prepare is reported, not fatal
                reason = f"could not prepare a run: {type(exc).__name__}: {exc}"
        engines[name] = {**info, "runs": reason is None, "reason": reason, "seeds": cmp.seeds_for(name)}
        if reason is None:
            jobs += [_Run(name, s) for s in range(cmp.seeds_for(name))]
    results = LocalBackend(workers=cmp.jobs).map(_CompareTask(cmp, root, Path(store) if store else None), jobs,
                                                  progress)  # fmt: skip
    values: dict[str, dict[str, list[float]]] = {n: {m.metric.expr: [] for m in cmp.metrics} for n in cmp.engines}
    claim_values: dict[str, dict[str, list[float]]] = {n: {c.id: [] for c in cmp.claims} for n in cmp.engines}
    for name in engines:
        engines[name].update({"ok": 0, "cached": 0, "failed": 0, "errors": [], "wall_time": 0.0})
    for job, rec in results:
        e = engines[job.engine]
        if isinstance(rec, str) or rec.status not in ("ok", "cached") or rec.outputs is None:
            e["failed"] += 1
            e["errors"].append(rec if isinstance(rec, str) else (rec.error or "no outputs"))
            continue
        e["cached" if rec.status == "cached" else "ok"] += 1
        e["wall_time"] += rec.wall_time or 0.0
        if run_store is not None and rec.status == "ok":
            outputs = rec.outputs
            rec = run_store.put(rec)
            rec.outputs = outputs
        scalars = rec.outputs.scalars
        try:
            for m in cmp.metrics:
                values[job.engine][m.metric.expr].append(m.metric.evaluate(scalars))
            for c in cmp.claims:
                claim_values[job.engine][c.id].append(c.metric.evaluate(scalars))
        except ClaimError as exc:
            e["failed"] += 1
            e["errors"].append(str(exc))
    for e in engines.values():
        e["errors"] = sorted(set(e["errors"]))[:5]
        if e["runs"] and e["ok"] + e["cached"] == 0:
            e["runs"], e["reason"] = False, "every run failed: " + "; ".join(e["errors"][:2])
    verdicts = _verdicts(cmp, engines, values)
    claims = _claims(cmp, engines, claim_values)
    counts = {v: sum(1 for x in verdicts if x["verdict"] == v) for v in VERDICTS}
    data = {
        "tool": "abmfact",
        "version": __version__,
        "created": datetime.now(UTC).isoformat(timespec="seconds"),
        "environment": {"python": platform.python_version(), "numpy": np.__version__, "platform": platform.system()},
        "comparison": {
            "title": cmp.title,
            "model": str(cmp.model),
            "engines": cmp.engines,
            "baseline": cmp.baseline,
            "params": cmp.params,
            "seeds": cmp.seeds,
            "seeds_by_engine": cmp.seeds_by_engine,
            "alpha": cmp.alpha,
            "metrics": [{"metric": m.metric.expr, "budget": m.budget.describe(), "note": m.note} for m in cmp.metrics],
            "notes": cmp.notes,
        },
        "engines": engines,
        "means": {
            n: {k: st.mean_interval(np.array(v), cmp.alpha).to_dict() for k, v in vals.items() if v}
            for n, vals in values.items()
        },
        "verdicts": verdicts,
        "claims": claims,
        "summary": counts,
    }
    if owned is not None:
        owned.cleanup()
    return CompareResult(data)


def _verdicts(
    cmp: Comparison, engines: Mapping[str, Mapping[str, Any]], values: Mapping[str, Mapping[str, list[float]]]
) -> list[dict[str, Any]]:
    out = []
    base = cmp.baseline
    for m in cmp.metrics:
        key = m.metric.expr
        for name in cmp.engines:
            if name == base:
                continue
            v: dict[str, Any] = {"metric": key, "engine": name, "baseline": base, "budget": m.budget.describe()}
            a, b = np.array(values[base][key]), np.array(values[name][key])
            if not engines[base]["runs"] or a.size < 2:
                out.append({**v, "verdict": "NOT_RUN", "reason": f"the baseline {base} has no results"})
                continue
            if not engines[name]["runs"] or b.size < 2:
                why = engines[name].get("reason") or "fewer than two runs gave this metric"
                out.append({**v, "verdict": "NOT_RUN", "reason": why})
                continue
            tol = m.budget.margin(a)
            iv = st.diff_interval(a, b, cmp.alpha)
            eq = st.equivalence(iv, tol)
            v.update({"margin": tol, "difference": iv.to_dict()})
            if eq == "equivalent":
                v.update(verdict="AGREE", reason=f"the difference lies within ±{tol:.4g}")
            elif eq == "shifted":
                v.update(verdict="DISAGREE", reason=f"the difference {iv.estimate:+.4g} lies outside ±{tol:.4g}")
            else:
                gap = max(tol - abs(iv.estimate), abs(iv.estimate) - tol, 1e-12)
                need = st.seeds_needed(min(a.size, b.size), iv.half_width, gap)
                v.update(verdict="INCONCLUSIVE", seeds_needed=need,
                         reason=f"the interval for the difference {iv.estimate:+.4g} straddles ±{tol:.4g}")  # fmt: skip
            out.append(v)
    return out


def _claims(
    cmp: Comparison, engines: Mapping[str, Mapping[str, Any]], values: Mapping[str, Mapping[str, list[float]]]
) -> list[dict[str, Any]]:
    out = []
    for c in cmp.claims:
        value = list(c.value) if isinstance(c.value, tuple) else c.value
        row: dict[str, Any] = {"claim": c.id, "statement": c.statement, "metric": c.metric.expr, "test": c.test,
                               "value": value, "status": {}}  # fmt: skip
        for name in cmp.engines:
            x = np.array(values[name][c.id])
            if not engines[name]["runs"] or x.size < 2:
                row["status"][name] = "not run"
                continue
            threshold = c.value if isinstance(c.value, tuple) else float(c.value)  # type: ignore[arg-type]
            row["status"][name] = st.side_status(st.mean_interval(x, cmp.alpha), c.test, threshold)
        decided = {s for s in row["status"].values() if s in ("holds", "fails")}
        row["same_conclusion"] = len(decided) <= 1
        out.append(row)
    return out


def comparison_from_model(
    model: str | Path, engines: Sequence[str], metrics: Sequence[str], budget: float, seeds: int, jobs: int
) -> Comparison:
    """A quick comparison from the command line: every metric gets the same relative budget."""
    return Comparison(
        title=Path(model).stem,
        model=Path(model).resolve(),
        engines=list(engines),
        baseline=engines[0],
        metrics=[CompareMetric(Metric.parse(m), Budget(relative=budget)) for m in metrics],
        seeds=seeds,
        jobs=jobs,
    )
