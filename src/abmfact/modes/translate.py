"""T2 translation: calibrate a lattice engine's mapping gains so its observables match the source model's.

A structural (T1) translation maps every ABM-IR concept it can, but two mappings between centre-based and
cellular Potts models are coefficients with no exact counterpart: the contact fraction that stands in for pressure,
and the strength that drives motility. T2 translation fits those coefficients, and only those (the
``pressure_gain`` and ``motility_gain`` engine options), so that the translated model reproduces chosen observables
of the source model's ensemble. Biological parameters such as rates, half-max values or adhesion are never varied:
a T2 translation changes how the model is expressed in the target engine, not the model.

The observables used for calibration and those held out must not overlap, and at least one must be held out, so a
T2 translation cannot match by construction what is later used to judge it. The held-out observables are reported
beside the calibration, with the difference and its interval, but not judged here.

The output folder holds the exported project at the calibrated gains, ``translation.json`` (tier T2, the gains with
their intervals, the calibration and the held-out check), ``fidelity.md`` and the calibration's own files.
"""

from __future__ import annotations

import dataclasses
import json
import tempfile
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any

import numpy as np
import yaml

from .. import stats as st
from ..engines.base import MAPPING_GAINS
from .calibrate import CalibrationError, CalibrationResult, CalibrationSpec, Evaluation, run_calibration
from .design import DesignError, Parameter
from .report import dumps

TARGETS = ("compucell3d", "morpheus")
KEYS = {
    "title",
    "model",
    "to",
    "source",
    "calibrate_on",
    "hold_out",
    "free",
    "options",
    "params",
    "seeds_per_evaluation",
    "seed_start",
    "method",
    "bayesopt",
    "abc_smc",
    "jobs",
    "alpha",
}
SOURCE_KEYS = {"engine", "seeds", "seed_start", "options"}
#: The fidelity entries each gain calibrates.
AFFECTS = {"pressure_gain": ("rules.pressure",), "motility_gain": (".motility",)}


@dataclasses.dataclass
class TranslationSpec:
    """A T2 translation.

    Attributes:
        title: A short title.
        model: The ABM-IR model.
        to: The target engine: ``compucell3d`` or ``morpheus``.
        source: The source ensemble: ``engine`` (default ``reference``), ``seeds``, ``seed_start`` and ``options``.
        calibrate_on: Observables the gains are fitted to.
        hold_out: Observables kept out of the fit and reported beside it.
        free: The mapping gains to fit, with their ranges.
        options: Fixed target-engine options, such as a gain fitted earlier.
        params: Fixed ABM-IR parameters for both engines, such as a shorter run.
        seeds_per_evaluation: Seeds behind each evaluation of the target engine.
        seed_start: The first seed of each evaluation.
        method: ``bayesopt`` or ``abc-smc``.
        settings: The method's settings.
        jobs: Runs at once.
        alpha: One-sided level for intervals.
    """

    title: str
    model: Path
    to: str
    calibrate_on: list[str]
    hold_out: list[str]
    free: list[Parameter]
    source: dict[str, Any] = dataclasses.field(default_factory=lambda: {"engine": "reference", "seeds": 20})
    options: dict[str, Any] = dataclasses.field(default_factory=dict)
    params: dict[str, Any] = dataclasses.field(default_factory=dict)
    seeds_per_evaluation: int = 10
    seed_start: int = 0
    method: str = "bayesopt"
    settings: dict[str, Any] = dataclasses.field(default_factory=dict)
    jobs: int = 1
    alpha: float = 0.05

    def check(self) -> None:
        """Refuse a translation that could match by construction or that varies anything but the mapping gains."""
        from ..ir import load_model

        if self.to not in TARGETS:
            raise CalibrationError(f"T2 translation targets {TARGETS}, not {self.to!r}")
        if not self.free:
            raise CalibrationError("a T2 translation needs at least one free mapping gain")
        for p in self.free:
            if p.kind != "option" or p.key not in MAPPING_GAINS:
                raise CalibrationError(
                    f"T2 translation may vary only the mapping gains {MAPPING_GAINS}, never {p.name!r}: biological "
                    "parameters such as rates, half-max values or adhesion belong to the model, not its translation"
                )
            if p.low <= 0:
                raise CalibrationError(f"{p.key}: a gain's range must be positive")
        clash = sorted(set(self.options) & {p.key for p in self.free})
        if clash:
            raise CalibrationError(f"{clash} are both fixed options and free gains")
        if not self.calibrate_on:
            raise CalibrationError("name at least one observable to calibrate on")
        if not self.hold_out:
            raise CalibrationError(
                "hold out at least one observable, so the translation is judged on data it was not fitted to"
            )
        overlap = sorted(set(self.calibrate_on) & set(self.hold_out))
        if overlap:
            raise CalibrationError(
                f"{overlap} are both calibrated on and held out; a held-out observable must not be fitted"
            )
        names = {o.name for o in load_model(self.model).observables}
        missing = sorted((set(self.calibrate_on) | set(self.hold_out)) - names)
        if missing:
            raise CalibrationError(f"the model has no observables {missing}; it has {sorted(names)}")

    def calibration(self) -> CalibrationSpec:
        """The calibration this translation runs: the gains of the target engine against the source ensemble."""
        src = self.source
        return CalibrationSpec(
            title=self.title,
            model=self.model,
            engine=self.to,
            parameters=self.free,
            target={
                "engine": src.get("engine", "reference"),
                "seeds": int(src.get("seeds", 20)),
                "seed_start": int(src.get("seed_start", self.seed_start)),
                "options": dict(src.get("options") or {}),
            },
            observables=list(self.calibrate_on),
            seeds_per_evaluation=self.seeds_per_evaluation,
            seed_start=self.seed_start,
            method=self.method,
            settings=self.settings,
            params=dict(self.params),
            options=dict(self.options),
            jobs=self.jobs,
            alpha=self.alpha,
            base=self.model.parent,
        )


def parse_free(items: Sequence[str]) -> list[Parameter]:
    """``NAME=LOW:HIGH`` gains from the command line, on a log scale."""
    out = []
    for item in items:
        name, sep, rng = item.partition("=")
        lo, colon, hi = rng.partition(":")
        if not sep or not colon:
            raise CalibrationError(f"--free {item!r} must look like NAME=LOW:HIGH, e.g. pressure_gain=0.2:5")
        try:
            out.append(Parameter("option:" + name.removeprefix("option:"), float(lo), float(hi), scale="log"))
        except (ValueError, DesignError) as exc:
            raise CalibrationError(f"--free {item!r}: {exc}") from exc
    return out


def load_translation(path: str | Path, overrides: Mapping[str, Any] | None = None) -> TranslationSpec:
    """Read a translation file; relative paths are resolved against its folder."""
    path = Path(path)
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    raw.update({k: v for k, v in (overrides or {}).items() if v is not None})
    unknown = sorted(set(raw) - KEYS)
    if unknown:
        raise CalibrationError(f"unknown translation keys: {unknown}")
    for key in ("model", "to", "calibrate_on", "hold_out", "free"):
        if key not in raw:
            raise CalibrationError(f"a translation needs {key!r}")
    source = dict(raw.get("source") or {"engine": "reference"})
    if set(source) - SOURCE_KEYS:
        raise CalibrationError(f"unknown source keys: {sorted(set(source) - SOURCE_KEYS)}")
    try:
        free = [
            Parameter.from_mapping({"scale": "log", **f, "name": "option:" + str(f["name"]).removeprefix("option:")})
            for f in raw["free"]
        ]
    except (KeyError, DesignError) as exc:
        raise CalibrationError(f"free gains: {exc}") from exc
    method = str(raw.get("method", "bayesopt"))
    spec = TranslationSpec(
        title=str(raw.get("title", path.stem)),
        model=(path.parent / raw["model"]).resolve(),
        to=str(raw["to"]),
        calibrate_on=[str(o) for o in raw["calibrate_on"]],
        hold_out=[str(o) for o in raw["hold_out"]],
        free=free,
        source=source,
        options=dict(raw.get("options") or {}),
        params=dict(raw.get("params") or {}),
        seeds_per_evaluation=int(raw.get("seeds_per_evaluation", 10)),
        seed_start=int(raw.get("seed_start", 0)),
        method=method,
        settings=dict(raw.get({"abc-smc": "abc_smc"}.get(method, "bayesopt")) or {}),
        jobs=int(raw.get("jobs", 1)),
        alpha=float(raw.get("alpha", 0.05)),
    )
    spec.check()
    return spec


@dataclasses.dataclass
class TranslationResult:
    """A T2 translation: the calibrated gains, the held-out check and the calibration behind them.

    Attributes:
        data: What ``translation.json`` holds.
        calibration: The calibration result.
        spec: The translation.
    """

    data: dict[str, Any]
    calibration: CalibrationResult
    spec: TranslationSpec

    @property
    def gains(self) -> dict[str, float]:
        """The calibrated gains, by option name."""
        return dict(self.data["gains"])

    def write(self, out: str | Path) -> dict[str, Path]:
        """Write the project at the calibrated gains, ``translation.json``, ``fidelity.md`` and the calibration."""
        out = Path(out)
        out.mkdir(parents=True, exist_ok=True)
        paths = {"calibration": self.calibration.write(out / "calibration")["json"]}
        report = export(self.spec, {**self.spec.options, **self.gains}, out / "project")
        annotate(report, self.data)
        paths["fidelity"] = out / "fidelity.md"
        paths["fidelity"].write_text(report.to_markdown(), encoding="utf-8")
        self.data["fidelity"] = {"tier": report.tier, "counts": report.counts()}
        paths["json"] = out / "translation.json"
        paths["json"].write_text(dumps(self.data), encoding="utf-8")
        return paths


def export(spec: TranslationSpec, options: Mapping[str, Any], folder: Path) -> Any:
    """Export the model to the target engine with ``options`` (gains included); return the fidelity report."""
    from ..engines import get_engine
    from ..ir import apply_params, load_model

    engine = get_engine(spec.to, **dict(options))
    model = apply_params(load_model(spec.model), spec.params)
    scale, minutes = engine._scales(True)  # type: ignore[attr-defined]
    gains = {k: float(v) for k, v in options.items() if k in MAPPING_GAINS}
    if spec.to == "morpheus":
        from ..engines.morpheus.translate import export_model

        return export_model(model, folder, node_length=scale, time_minutes=minutes, **gains)
    from ..engines.compucell3d.translate import export_project

    return export_project(model, folder, site_length=scale, mcs_minutes=minutes, **gains)


def annotate(report: Any, data: Mapping[str, Any]) -> None:
    """Mark the report tier T2 and add each gain's calibration to the entries it governs."""
    report.tier = "T2"
    for gain, value in data["gains"].items():
        lo, hi = data["interval"][gain]
        note = (
            f"calibrated: {gain} {value:.4g} ({lo:.4g} to {hi:.4g}) on {', '.join(data['calibrate_on'])} "
            f"against {data['source']['engine']} ({data['source']['seeds']} seeds)"
        )
        for i, e in enumerate(report.entries):
            if any(e.path == a or e.path.endswith(a) for a in AFFECTS[gain]):
                report.entries[i] = dataclasses.replace(e, note=f"{e.note}; {note}")


def held_out_check(
    spec: TranslationSpec, gains: Mapping[str, float], *, workdir: str | Path | None, store: str | Path | None
) -> list[dict[str, Any]]:
    """Each held-out observable at the end of the run: source and target means and their difference's interval."""
    from .ensemble import run_ensemble

    metrics = [f"last({o})" for o in spec.hold_out]
    src = spec.source
    a = run_ensemble(
        spec.model,
        str(src.get("engine", "reference")),
        int(src.get("seeds", 20)),
        seed_start=int(src.get("seed_start", spec.seed_start)),
        params=spec.params,
        options=dict(src.get("options") or {}),
        metrics=metrics,
        alpha=spec.alpha,
        jobs=spec.jobs,
        workdir=workdir,
        store=store,
    )
    b = run_ensemble(
        spec.model,
        spec.to,
        spec.seeds_per_evaluation,
        seed_start=spec.seed_start,
        params=spec.params,
        options={**spec.options, **gains},
        metrics=metrics,
        alpha=spec.alpha,
        jobs=spec.jobs,
        workdir=workdir,
        store=store,
    )
    out = []
    for m in metrics:
        x, y = np.array(a.data["metrics"][m]["values"]), np.array(b.data["metrics"][m]["values"])
        row: dict[str, Any] = {
            "metric": m,
            "source_mean": float(x.mean()) if x.size else None,
            "target_mean": float(y.mean()) if y.size else None,
        }
        if x.size >= 2 and y.size >= 2:
            row["difference"] = st.diff_interval(x, y, spec.alpha).to_dict()
            row["relative"] = float((y.mean() - x.mean()) / abs(x.mean())) if x.mean() else None
        out.append(row)
    return out


def translate(
    spec: TranslationSpec,
    *,
    workdir: str | Path | None = None,
    store: str | Path | None = None,
    on_evaluation: Callable[[Evaluation], None] | None = None,
) -> TranslationResult:
    """Calibrate the gains, then check the held-out observables at the calibrated gains."""
    from .. import __version__

    spec.check()
    owned = tempfile.TemporaryDirectory() if store is None else None
    store = store if store is not None else Path(owned.name) / "store"  # type: ignore[union-attr]
    try:
        cal = run_calibration(spec.calibration(), workdir=workdir, store=store, on_evaluation=on_evaluation)
        best = cal.best
        gains = {p.key: float(best[p.name]) for p in spec.free}
        interval = {p.key: [float(v) for v in cal.data["result"]["interval"][p.name]] for p in spec.free}
        check = held_out_check(spec, gains, workdir=workdir, store=store)
    finally:
        if owned is not None:
            owned.cleanup()
    src = spec.calibration().target
    data = {
        "tool": "abmfact",
        "version": __version__,
        "created": cal.data["created"],
        "tier": "T2",
        "model": str(spec.model),
        "engine": spec.to,
        "gains": gains,
        "interval": interval,
        "interval_meaning": cal.data["result"]["interval_meaning"],
        "fixed_options": spec.options,
        "params": spec.params,
        "source": {
            "engine": src["engine"],
            "seeds": src["seeds"],
            "seed_start": src["seed_start"],
            "options": src["options"],
        },
        "calibrate_on": spec.calibrate_on,
        "hold_out": spec.hold_out,
        "method": spec.method,
        "distance": cal.data["result"]["best_distance"],
        "seeds_per_evaluation": spec.seeds_per_evaluation,
        "seed_start": spec.seed_start,
        "evaluations": len(cal.data["evaluations"]),
        "warnings": cal.data["result"].get("warnings", []),
        "held_out": check,
        "held_out_meaning": "reported, not judged: the held-out observables' end values in the source ensemble and "
        "in the target engine at the calibrated gains",
    }
    return TranslationResult(data, cal, spec)


def options_from(path: str | Path, engine: str) -> dict[str, float]:
    """The calibrated gains of a ``translation.json`` for ``engine``, checking that it is a T2 translation for it."""
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if data.get("tier") != "T2" or data.get("engine") != engine:
        raise CalibrationError(
            f"{path} is not a T2 translation for {engine} (tier {data.get('tier')}, engine {data.get('engine')})"
        )
    return {**dict(data.get("fixed_options") or {}), **{k: float(v) for k, v in data["gains"].items()}}
