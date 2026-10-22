"""Run an audit: execute every condition, then turn the runs into verdicts."""

from __future__ import annotations

import json
import os
import platform
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

import numpy as np

from . import __version__
from . import stats as st
from .adapters import Adapter, Outputs
from .checks import Check, Variant, select_checks
from .claims import Claim
from .config import Audit
from .diagnose import diagnose
from .engines.legacy import AdapterEngine, engine_for_adapter
from .knobs import Knobs, perm_seed
from .records.cache import bypass_cache
from .runtime.local import LocalBackend

SCHEMA_VERSION = "1"

VERDICTS = ("ROBUST", "SHIFTED", "FRAGILE", "INCONCLUSIVE", "NOT_TESTABLE", "SKIPPED", "BASELINE_FAILS")
FLAGGED = ("FRAGILE", "SHIFTED")

BASELINE = "baseline"
RERUN = "determinism:rerun"


def point_key(params: dict[str, Any]) -> str:
    return json.dumps(params, sort_keys=True, default=str)


@dataclass
class Condition:
    label: str
    check: str | None
    knobs: Knobs
    shuffle_ids: bool = False
    seeds: list[int] = field(default_factory=list)

    def knobs_for(self, seed: int) -> Knobs:
        if self.shuffle_ids:
            return self.knobs.with_(agent_perm_seed=perm_seed(seed))
        return self.knobs


# ---------------------------------------------------------------------------------------------------------
# Execution

Job = tuple[str, str, dict[str, Any], int, dict[str, Any]]


class _AuditTask:
    """One audit job through the engine contract: prepare, run on the local backend, collect."""

    def __init__(self, engine: AdapterEngine):
        self.engine = engine

    def __call__(self, job: Job) -> tuple[str, str, int, Outputs]:
        label, pkey, params, seed, knobs = job
        if label == RERUN:
            # A determinism rerun must execute again: no cache may answer it with the first run.
            with bypass_cache():
                record = self.engine.run_once(self.engine.adapter, params, seed, Knobs.from_dict(knobs))
        else:
            record = self.engine.run_once(self.engine.adapter, params, seed, Knobs.from_dict(knobs))
        assert record.outputs is not None
        return label, pkey, seed, record.outputs.scalars


def execute(
    adapter: Adapter,
    jobs: list[Job],
    workers: int,
    progress: Callable[[int, int], None] | None = None,
) -> dict[tuple[str, str, int], Outputs]:
    """Run every job through the adapter's engine on the local backend, in parallel when possible.

    Results are keyed, so order never matters.
    """
    task = _AuditTask(engine_for_adapter(adapter))
    results = LocalBackend(workers).map(task, jobs, progress)
    return {(label, pkey, seed): out for label, pkey, seed, out in results}


# ---------------------------------------------------------------------------------------------------------
# Evaluation helpers


def _status_at(
    claim: Claim, values: dict[str, np.ndarray], points: list[dict[str, Any]], alpha: float, negligible: float = 0.0
) -> tuple[str, dict[str, Any]]:
    """Claim status in one condition, plus the intervals it was based on."""
    if claim.is_monotone:
        assert claim.over is not None
        xs, ys = [], []
        for p in points:
            v = values[point_key(p)]
            xs.extend([float(p[claim.over])] * v.size)
            ys.extend(v.tolist())
        iv = st.slope_interval(np.asarray(xs), np.asarray(ys), alpha)
        status = st.trend_status(iv, claim.test == "monotone_increasing", negligible)
        return status, {"slope": iv.to_dict()}
    statuses = []
    detail: dict[str, Any] = {}
    for p in points:
        k = point_key(p)
        iv = st.mean_interval(values[k], alpha)
        statuses.append(st.side_status(iv, claim.test, claim.value))
        detail[k] = iv.to_dict()
    if all(s == "holds" for s in statuses):
        status = "holds"
    elif any(s == "fails" for s in statuses):
        status = "fails"
    else:
        status = "uncertain"
    return status, {"points": detail}


def _status_seeds_needed(claim: Claim, detail: dict[str, Any], n: int) -> int:
    """Seeds needed to settle an uncertain status."""
    best = 0
    if "slope" in detail:
        iv = detail["slope"]
        gap = abs(iv["estimate"])
        return st.seeds_needed(n, (iv["high"] - iv["low"]) / 2, gap)
    for iv in detail["points"].values():
        thresholds = list(claim.value) if isinstance(claim.value, tuple) else [claim.value]
        gap = min(abs(iv["estimate"] - float(t)) for t in thresholds if t is not None)
        best = max(best, st.seeds_needed(n, (iv["high"] - iv["low"]) / 2, gap))
    return best


def _is_degenerate(v: np.ndarray) -> bool:
    return v.size > 1 and float(np.ptp(v)) == 0.0


# ---------------------------------------------------------------------------------------------------------
# Main entry point


@dataclass
class AuditResult:
    data: dict[str, Any]

    @property
    def verdicts(self) -> list[dict[str, Any]]:
        return list(self.data["verdicts"])

    def matrix(self) -> dict[str, dict[str, str]]:
        """``{claim_id: {check: verdict}}``."""
        out: dict[str, dict[str, str]] = {}
        for v in self.data["verdicts"]:
            out.setdefault(v["claim"], {})[v["check"]] = v["verdict"]
        return out

    def flagged_checks(self) -> list[str]:
        return sorted({v["check"] for v in self.data["verdicts"] if v["verdict"] in FLAGGED})

    @property
    def any_flagged(self) -> bool:
        return any(v["verdict"] in FLAGGED for v in self.data["verdicts"])

    def to_json(self, include_timestamp: bool = True) -> str:
        d = dict(self.data)
        if not include_timestamp:
            d.pop("created", None)
        return json.dumps(d, indent=2, sort_keys=True, allow_nan=False, default=_json_default) + "\n"


def _json_default(o: Any) -> Any:
    if isinstance(o, np.floating | np.integer):
        return o.item()
    raise TypeError(f"not JSON serialisable: {type(o).__name__}")


def _clean(o: Any) -> Any:
    """Replace non-finite floats so results.json stays valid JSON."""
    if isinstance(o, float):
        return o if np.isfinite(o) else None
    if isinstance(o, dict):
        return {k: _clean(v) for k, v in o.items()}
    if isinstance(o, list | tuple):
        return [_clean(v) for v in o]
    return o


def run_audit(audit: Audit, progress: Callable[[int, int], None] | None = None) -> AuditResult:
    b = audit.budget
    alpha = b.alpha
    adapter = audit.adapter
    support = adapter.support
    seeds = list(range(b.seed_start, b.seed_start + b.seeds))
    checks = select_checks(list(b.checks) or None)

    # Parameter points: the union of every claim's grid.
    points_by_claim: dict[str, list[dict[str, Any]]] = {c.id: c.points(audit.params) for c in audit.claims}
    all_points: dict[str, dict[str, Any]] = {}
    for pts in points_by_claim.values():
        for p in pts:
            all_points.setdefault(point_key(p), p)

    # Conditions.
    conditions: dict[str, Condition] = {BASELINE: Condition(BASELINE, None, Knobs(), seeds=seeds)}
    variants_by_check: dict[str, list[Variant] | None] = {}
    for chk in checks:
        vs = chk.variants(support)
        variants_by_check[chk.name] = vs
        for v in vs or []:
            conditions[v.label] = Condition(v.label, chk.name, v.knobs, v.shuffle_ids, seeds=seeds)
    if any(c.name == "determinism" for c in checks):
        reruns = seeds[: max(1, min(b.determinism_reruns, len(seeds)))]
        conditions[RERUN] = Condition(RERUN, "determinism", Knobs(), seeds=reruns)

    jobs = []
    for cond in conditions.values():
        for pkey, params in all_points.items():
            for s in cond.seeds:
                jobs.append((cond.label, pkey, params, s, cond.knobs_for(s).to_dict()))
    outputs = execute(adapter, jobs, b.jobs, progress)

    # Metric values: values[label][claim_id][pkey] -> array over seeds.
    values: dict[str, dict[str, dict[str, np.ndarray]]] = {}
    for label, cond in conditions.items():
        values[label] = {}
        for claim in audit.claims:
            per_point = {}
            for p in points_by_claim[claim.id]:
                k = point_key(p)
                per_point[k] = np.asarray([claim.metric.evaluate(outputs[(label, k, s)]) for s in cond.seeds])
            values[label][claim.id] = per_point

    verdicts: list[dict[str, Any]] = []
    claims_out: list[dict[str, Any]] = []
    for claim in audit.claims:
        pts = points_by_claim[claim.id]
        base_vals = values[BASELINE][claim.id]
        base_status, base_detail = _status_at(claim, base_vals, pts, alpha)
        base_slope = abs(base_detail["slope"]["estimate"]) if "slope" in base_detail else 0.0
        tolerances = {
            k: (claim.tolerance if claim.tolerance is not None else st.default_tolerance(v))
            for k, v in base_vals.items()
        }
        warnings: list[str] = []
        if claim.tolerance is None:
            for k, vals in base_vals.items():
                w = st.tolerance_warning(vals, tolerances[k])
                if w and w not in warnings:
                    warnings.append(w)
        entry = {**claim.to_dict(), "baseline": {"status": base_status, **base_detail}, "tolerance": tolerances}
        if warnings:
            # Only present when there is something to say, so records without warnings keep their v0.1 shape.
            entry["warnings"] = warnings
        claims_out.append(entry)
        for chk in checks:
            verdicts.append(
                _verdict(
                    claim,
                    chk,
                    variants_by_check.get(chk.name),
                    conditions,
                    values,
                    pts,
                    base_status,
                    base_slope,
                    tolerances,
                    alpha,
                    len(seeds),
                )
            )
            if warnings and verdicts[-1]["verdict"] in ("ROBUST", "SHIFTED", "INCONCLUSIVE", "FRAGILE"):
                verdicts[-1]["warnings"] = warnings

    flagged = sorted({v["check"] for v in verdicts if v["verdict"] in FLAGGED})
    summary = {name: sum(1 for v in verdicts if v["verdict"] == name) for name in VERDICTS}
    cond_summary = {}
    for label, per_claim in values.items():
        cond_summary[label] = {
            "check": conditions[label].check,
            "knobs": conditions[label].knobs.changed(),
            "seeds": len(conditions[label].seeds),
            "claims": {
                cid: {k: st.mean_interval(v, alpha).to_dict() for k, v in per_point.items()}
                for cid, per_point in per_claim.items()
            },
        }

    data = {
        "schema_version": SCHEMA_VERSION,
        "tool": {"name": "abmfact", "version": __version__},
        "created": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "environment": {
            "python": platform.python_version(),
            "numpy": np.__version__,
            "platform": platform.system(),
        },
        "audit": {
            "title": audit.title,
            "source": os.fspath(audit.source) if audit.source else None,
            "sha256": audit.sha256,
            "model": {**adapter.describe(), "config": audit.model_cfg},
            "params": audit.params,
            "budget": b.to_dict(),
        },
        "claims": claims_out,
        "conditions": cond_summary,
        "verdicts": verdicts,
        "summary": summary,
        "diagnosis": diagnose(flagged),
    }
    return AuditResult(_clean(data))


def _verdict(
    claim: Claim,
    chk: Check,
    variants: list[Variant] | None,
    conditions: dict[str, Condition],
    values: dict[str, dict[str, dict[str, np.ndarray]]],
    pts: list[dict[str, Any]],
    base_status: str,
    base_slope: float,
    tolerances: dict[str, float],
    alpha: float,
    n: int,
) -> dict[str, Any]:
    row: dict[str, Any] = {"claim": claim.id, "check": chk.name, "variants": []}

    def done(verdict: str, reason: str, **extra: Any) -> dict[str, Any]:
        row.update(verdict=verdict, reason=reason, **extra)
        return row

    if chk.name in claim.skip_checks:
        return done("SKIPPED", claim.skip_checks[chk.name])
    if variants is None:
        return done("NOT_TESTABLE", "the model does not declare the knob this check needs")
    if base_status == "fails":
        return done("BASELINE_FAILS", "the claim does not hold even at baseline")
    base_vals = values[BASELINE][claim.id]

    if chk.name == "determinism":
        rerun = values[RERUN][claim.id]
        seeds = conditions[RERUN].seeds
        for k, v in rerun.items():
            first = base_vals[k][: v.size]
            bad = [s for s, x, y in zip(seeds, first, v, strict=True) if not (x == y or (np.isnan(x) and np.isnan(y)))]
            if bad:
                return done(
                    "FRAGILE",
                    f"the same seed gave a different result on rerun (seeds {bad[:5]})",
                    reproduce=[{"label": BASELINE, "seed": bad[0], "params": json.loads(k), "knobs": {}}],
                )
        return done("ROBUST", f"{len(seeds)} seeds reran identically")

    degenerate = [k for k, v in base_vals.items() if _is_degenerate(v)]
    if chk.name == "rng_streams" and degenerate:
        return done(
            "FRAGILE",
            "every seed gave the same result, so the seed has no effect "
            "(skip this check only if the model is meant to be deterministic)",
        )
    if degenerate:
        return done("INCONCLUSIVE", "the baseline has no between-seed variance, so no comparison is possible")
    if base_status == "uncertain":
        _, detail = _status_at(claim, base_vals, pts, alpha)
        return done(
            "INCONCLUSIVE",
            "the claim is not established at baseline",
            seeds_needed=_status_seeds_needed(claim, detail, n),
        )

    any_fail = any_shift = any_uncertain = False
    needed = 0
    reasons: list[str] = []
    reproduce: list[dict[str, Any]] = []
    for var in variants:
        vv = values[var.label][claim.id]
        status, detail = _status_at(claim, vv, pts, alpha, negligible=0.1 * base_slope)
        vrow: dict[str, Any] = {"label": var.label, "status": status, "comparisons": {}}
        if status == "fails":
            any_fail = True
            reasons.append(f"claim fails under {var.label}")
        elif status == "uncertain":
            any_uncertain = True
            needed = max(needed, _status_seeds_needed(claim, detail, n))
        for k, b in base_vals.items():
            v = vv[k]
            tol = tolerances[k]
            iv = st.diff_interval(b, v, alpha)
            eq = st.equivalence(iv, tol)
            sp = st.spread(b, v)
            spread_flag = chk.compare_spread and sp.shifted(alpha)
            vrow["comparisons"][k] = {
                "difference": iv.to_dict(),
                "tolerance": tol,
                "equivalence": eq,
                "spread_sd_ratio": sp.sd_ratio,
                "spread_iqr_ratio": sp.iqr_ratio,
                "spread_p": sp.p,
                "spread_shifted": spread_flag,
            }
            if eq == "shifted" or spread_flag:
                any_shift = True
                what = "mean" if eq == "shifted" else "spread"
                reasons.append(
                    f"{what} shifted under {var.label} ({iv.estimate:+.4g}, tolerance {tol:.3g})"
                    if what == "mean"
                    else f"spread changed by x{sp.sd_ratio:.2f} under {var.label}"
                )
            elif eq == "undetermined":
                any_uncertain = True
                gap = abs(tol - abs(iv.estimate))
                needed = max(needed, st.seeds_needed(n, iv.half_width, gap))
            if eq == "shifted" or status == "fails":
                seed_idx = int(np.argmax(np.abs(v - b.mean())))
                cond = conditions[var.label]
                s = cond.seeds[seed_idx]
                reproduce.append(
                    {"label": var.label, "seed": s, "params": json.loads(k), "knobs": cond.knobs_for(s).changed()}
                )
        row["variants"].append(vrow)

    if any_fail:
        return done("FRAGILE", "; ".join(dict.fromkeys(reasons)), reproduce=reproduce[:3])
    if any_shift:
        return done("SHIFTED", "; ".join(dict.fromkeys(reasons)), reproduce=reproduce[:3])
    if any_uncertain:
        return done("INCONCLUSIVE", "intervals too wide to decide", seeds_needed=needed)
    return done("ROBUST", f"claim holds and results are equivalent under {len(variants)} variant(s)")
