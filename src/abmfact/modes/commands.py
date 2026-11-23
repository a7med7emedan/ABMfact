"""Command line for the modes: ``abmfact ensemble``, ``sweep``, ``sensitivity``, ``calibrate``, ``translate`` and
``visualise``.

Only ``argparse`` is imported here at load time; each command imports what it needs when it runs.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

COMMANDS = ("ensemble", "sweep", "sensitivity", "calibrate", "translate", "visualise")


def add_parsers(sub: Any) -> None:
    """Add the mode subcommands to the main parser's subparsers."""
    en = sub.add_parser("ensemble", help="run many seeds of one setting in one engine and summarise with intervals")
    en.add_argument("model", help="ABM-IR file, or a native project folder for engines that take one")
    en.add_argument("--engine", default="reference", help="engine name (see: abmfact engines)")
    en.add_argument("--seeds", type=int, default=20, help="number of seeds (default 20)")
    en.add_argument("--seed-start", type=int, default=0, help="the first seed (default 0)")
    en.add_argument(
        "--param",
        action="append",
        metavar="KEY=VALUE",
        help="parameter for every run, e.g. 'experiment.max_time=600'; repeatable",
    )
    en.add_argument("--option", action="append", metavar="KEY=VALUE", help="engine option, e.g. threads=1")
    en.add_argument("--metric", action="append", help="metric to summarise, e.g. 'last(cells)'; repeatable")
    en.add_argument("--alpha", type=float, default=0.05, help="one-sided level; intervals cover 1-2*alpha")
    en.add_argument("--jobs", type=int, default=1, help="runs at once")
    en.add_argument("--store", help="run store: finished runs are kept and reused")
    en.add_argument("--workdir", help="folder for run folders (default: a temporary folder)")
    en.add_argument("--out", default="abmfact-ensemble", help="output folder (default: abmfact-ensemble)")
    en.add_argument("-q", "--quiet", action="store_true", help="print only errors")

    sw = sub.add_parser("sweep", help="run a model over a design of parameter settings (grid, lhs or sobol)")
    sw.add_argument("sweep", help="sweep YAML file")
    sw.add_argument("--jobs", type=int, help="runs at once (overrides the file)")
    sw.add_argument("--store", help="run store: finished runs are kept and reused, so a sweep can resume")
    sw.add_argument("--workdir", help="folder for run folders (default: a temporary folder)")
    sw.add_argument("--out", default="abmfact-sweep", help="output folder (default: abmfact-sweep)")
    sw.add_argument(
        "--backend",
        choices=["local", "slurm"],
        default="local",
        help="local runs here; slurm writes a job array for the runs not yet in the store",
    )
    sw.add_argument("--submit", action="store_true", help="with --backend slurm: also submit the array with sbatch")
    sw.add_argument("--collect", action="store_true", help="read the runs a Slurm array wrote under OUT/slurm")
    sw.add_argument("-q", "--quiet", action="store_true", help="print only errors")
    se = sub.add_parser("sensitivity", help="Sobol or Morris sensitivity indices with bootstrap intervals (SALib)")
    se.add_argument("sensitivity", help="sensitivity YAML file: a sweep file with a 'sensitivity' section")
    se.add_argument("--method", choices=["sobol", "morris"], help="override the file's method")
    se.add_argument("--n", type=int, help="override N (Sobol base sample) or the Morris trajectories")
    se.add_argument("--jobs", type=int, help="runs at once (overrides the file)")
    se.add_argument("--store", help="run store: finished runs are kept and reused")
    se.add_argument("--workdir", help="folder for run folders (default: a temporary folder)")
    se.add_argument("--out", default="abmfact-sensitivity", help="output folder (default: abmfact-sensitivity)")
    se.add_argument("-q", "--quiet", action="store_true", help="print only errors")
    ca = sub.add_parser("calibrate", help="fit parameters to target data or another engine's ensemble")
    ca.add_argument("calibration", nargs="?", help="calibration YAML file")
    ca.add_argument("--methods", action="store_true", help="list the calibration methods and plug-ins, and exit")
    ca.add_argument("--method", help="override the file's method: bayesopt, abc-smc or a plug-in")
    ca.add_argument("--jobs", type=int, help="runs at once (overrides the file)")
    ca.add_argument("--store", help="run store: finished runs are kept and reused")
    ca.add_argument("--workdir", help="folder for run folders (default: a temporary folder)")
    ca.add_argument("--out", default="abmfact-calibration", help="output folder (default: abmfact-calibration)")
    ca.add_argument("-q", "--quiet", action="store_true", help="print only errors")
    tr = sub.add_parser(
        "translate", help="T2 translation: calibrate a lattice engine's mapping gains to a source model"
    )
    tr.add_argument(
        "target", help="translation YAML file, or an ABM-IR model with --to, --calibrate-on, --hold-out, --free"
    )
    tr.add_argument("--to", choices=["compucell3d", "morpheus"], help="the target engine")
    tr.add_argument("--calibrate-on", help="comma-separated observables the gains are fitted to")
    tr.add_argument("--hold-out", help="comma-separated observables kept out of the fit and reported")
    tr.add_argument(
        "--free",
        action="append",
        metavar="GAIN=LOW:HIGH",
        help="a mapping gain to fit, e.g. pressure_gain=0.2:5; only pressure_gain and motility_gain",
    )
    tr.add_argument("--source-engine", default="reference", help="the source engine (default reference)")
    tr.add_argument("--source-seeds", type=int, default=20, help="seeds of the source ensemble (default 20)")
    tr.add_argument("--seeds", type=int, default=10, help="seeds per evaluation of the target (default 10)")
    tr.add_argument("--seed-start", type=int, default=0, help="the first seed (default 0)")
    tr.add_argument("--method", default="bayesopt", help="bayesopt (default) or abc-smc")
    tr.add_argument("--param", action="append", metavar="KEY=VALUE", help="fixed ABM-IR parameter for both engines")
    tr.add_argument("--option", action="append", metavar="KEY=VALUE", help="fixed target-engine option")
    tr.add_argument("--jobs", type=int, help="runs at once")
    tr.add_argument("--store", help="run store: finished runs are kept and reused")
    tr.add_argument("--workdir", help="folder for run folders (default: a temporary folder)")
    tr.add_argument("--out", default="abmfact-translation", help="output folder (default: abmfact-translation)")
    tr.add_argument("-q", "--quiet", action="store_true", help="print only errors")
    vi = sub.add_parser("visualise", help="plots, cell snapshots, fields, movies and VTK files from results or a run")
    vi.add_argument("target", help="a mode's result file or folder, a run folder, or a run id with --store")
    vi.add_argument("--store", help="run store to find a run id in")
    vi.add_argument("--out", default="abmfact-plots", help="output folder (default: abmfact-plots)")
    vi.add_argument("--times", default="last", help="for a run: last (default), all, or comma-separated times")
    vi.add_argument("--colour", choices=["type", "state"], default="type", help="colour cells by type or state")
    vi.add_argument("--field", action="append", help="substrate to draw (default: every one); repeatable")
    vi.add_argument("--movie", choices=["gif", "mp4"], help="also film every save time")
    vi.add_argument("--vtk", action="store_true", help="also write VTK files for ParaView")
    vi.add_argument("-q", "--quiet", action="store_true", help="print only errors")


def run(args: argparse.Namespace) -> int:
    """Run the chosen mode command and return its exit code."""
    if args.command == "visualise":
        return cmd_visualise(args)
    if args.command == "translate":
        return cmd_translate(args)
    if args.command == "calibrate":
        return cmd_calibrate(args)
    if args.command == "sweep":
        return cmd_sweep(args)
    if args.command == "sensitivity":
        return cmd_sensitivity(args)
    return cmd_ensemble(args)


def pairs(items: list[str] | None, what: str) -> dict[str, Any]:
    """``KEY=VALUE`` pairs; a value is read as JSON when it parses, else kept as text."""
    from ..config import ConfigError

    out: dict[str, Any] = {}
    for item in items or []:
        key, sep, value = item.partition("=")
        if not sep or not key:
            raise ConfigError(f"{what} {item!r} must look like KEY=VALUE")
        try:
            out[key] = json.loads(value)
        except json.JSONDecodeError:
            out[key] = value
    return out


def cmd_ensemble(args: argparse.Namespace) -> int:
    """Run ``abmfact ensemble``; exit code 1 when any run failed, else 0."""
    from ..cli import _progress
    from ..config import ConfigError
    from .ensemble import run_ensemble

    try:
        result = run_ensemble(
            args.model,
            args.engine,
            args.seeds,
            seed_start=args.seed_start,
            params=pairs(args.param, "parameter"),
            options=pairs(args.option, "engine option"),
            metrics=args.metric or (),
            alpha=args.alpha,
            jobs=args.jobs,
            workdir=args.workdir,
            store=args.store,
            progress=_progress(args.quiet),
        )
    except ValueError as exc:
        if type(exc).__name__ == "IRError":
            raise
        raise ConfigError(str(exc)) from exc
    paths = result.write(args.out)
    d = result.data
    if not args.quiet:
        runs = d["runs"]
        tier = d["engine"].get("tier", "")
        print(f"  {d['ensemble']['engine']} {tier}: {runs['ok']} new, {runs['cached']} reused, {runs['failed']} failed")
        for err in runs["errors"]:
            print(f"    error: {err}")
        for expr, m in d["metrics"].items():
            if "mean" in m:
                print(
                    f"  {expr:<24} {m['mean']:>10.4g}  ({m['low']:.4g} to {m['high']:.4g}), sd {m['sd']:.3g}, "
                    f"n {m['n']}"
                )
        for name, rows in d["series"].items():
            last = rows[-1]
            iv = f"({last['low']:.4g} to {last['high']:.4g})"
            print(f"  {name:<24} {last['mean']:>10.4g}  {iv} at t={last['time']:g}")
        print(f"  wrote {Path(paths['json']).parent}")
    return 1 if d["runs"]["failed"] else 0


def cmd_sweep(args: argparse.Namespace) -> int:
    """Run ``abmfact sweep``; exit code 1 when any run failed or was not collected, else 0."""
    import numpy as np

    from ..cli import _progress
    from ..config import ConfigError
    from .sweep import load_sweep, run_sweep, write_slurm

    out = Path(args.out)
    try:
        spec = load_sweep(args.sweep, {"jobs": args.jobs})
        if args.backend == "slurm":
            script, written, cached = write_slurm(spec, out / "slurm", args.store, submit=args.submit)
            if not args.quiet:
                print(f"  {written} runs to do, {cached} already in the store")
                if script is not None:
                    print(f"  {'submitted' if args.submit else 'wrote'} {script}")
                    print(f"  when the array has finished: abmfact sweep {args.sweep} --collect --out {out}")
            return 0
        result = run_sweep(
            spec,
            workdir=args.workdir,
            store=args.store,
            progress=_progress(args.quiet),
            collect_from=out / "slurm" if args.collect else None,
        )
    except ValueError as exc:
        if type(exc).__name__ == "IRError":
            raise
        raise ConfigError(str(exc)) from exc
    paths = result.write(out)
    d = result.data
    if not args.quiet:
        c = d["counts"]
        s = d["sweep"]
        size = f"{s['design']['points']} points x {s['seeds_per_point']} seeds in {s['engine']}"
        print(f"  {s['title']}: {size}: {c['ok']} new, {c['cached']} reused, {c['failed']} failed")
        for err in c["errors"]:
            print(f"    error: {err}")
        for m in d["sweep"]["metrics"]:
            means = result.values(m)
            if np.isfinite(means).any():
                print(f"  {m:<24} point means from {np.nanmin(means):.4g} to {np.nanmax(means):.4g}")
        print(f"  wrote {paths['json'].parent}")
    return 1 if d["counts"]["failed"] else 0


def cmd_sensitivity(args: argparse.Namespace) -> int:
    """Run ``abmfact sensitivity``; exit code 1 when any run failed or a metric got no indices, else 0."""
    from ..cli import _progress
    from ..config import ConfigError
    from .sensitivity import load_sensitivity, run_sensitivity

    try:
        spec, settings = load_sensitivity(args.sensitivity, {"jobs": args.jobs, "method": args.method, "n": args.n})
        result = run_sensitivity(spec, settings, workdir=args.workdir, store=args.store, progress=_progress(args.quiet))
    except ValueError as exc:
        if type(exc).__name__ == "IRError":
            raise
        raise ConfigError(str(exc)) from exc
    paths = result.write(args.out)
    d = result.data
    if not args.quiet:
        s, c = d["sensitivity"], d["counts"]
        print(
            f"  {s['method']} on {s['points']} points x {s['seeds_per_point']} seeds in {s['engine']}: "
            f"{c['ok']} new, {c['cached']} reused, {c['failed']} failed"
        )
        for w in d["warnings"]:
            print(f"  warning: {w}")
        for metric, m in d["metrics"].items():
            if "error" in m:
                print(f"  {metric}: {m['error']}")
                continue
            for name, v in m["indices"].items():
                if s["method"] == "sobol":
                    print(
                        f"  {metric:<16} {name:<40} S1 {v['S1']:6.3f} ±{v['S1_conf']:.3f}"
                        f"  ST {v['ST']:6.3f} ±{v['ST_conf']:.3f}"
                    )
                else:
                    print(
                        f"  {metric:<16} {name:<40} mu* {v['mu_star']:.4g} ±{v['mu_star_conf']:.3g}"
                        f"  sigma {v['sigma']:.4g}"
                    )
            if m.get("noise_share") is not None:
                print(f"  {metric:<16} seed noise share {m['noise_share']:.1%}")
            for w in m.get("warnings", []):
                print(f"  warning ({metric}): {w}")
        print(f"  wrote {paths['json'].parent}")
    return 1 if d["counts"]["failed"] or any("error" in m for m in d["metrics"].values()) else 0


def cmd_calibrate(args: argparse.Namespace) -> int:
    """Run ``abmfact calibrate``; exit code 0 when a fit was found, 2 for an invalid file or no usable evaluation."""
    from ..config import ConfigError
    from .calibrate import load_calibration, run_calibration

    if args.methods:
        from .calibrate import calibrators
        from .plugins import status

        slots = {s["method"]: s for s in status()}
        for name in sorted(calibrators()):
            s = slots.get(name)
            where = "" if s is None else f"  ({s['package']} {s['version'] or 'not installed'}: {s['does']})"
            print(f"  {name}{where}")
        return 0
    if not args.calibration:
        raise ConfigError("give a calibration file, or --methods")

    def show(e: Any) -> None:
        values = ", ".join(f"{k} {v:.4g}" for k, v in e.values.items())
        print(f"  evaluation {e.index + 1:>3}: {values}  distance {e.distance:.4g}", flush=True)

    try:
        spec = load_calibration(args.calibration, {"jobs": args.jobs, "method": args.method})
        result = run_calibration(
            spec, workdir=args.workdir, store=args.store, on_evaluation=None if args.quiet else show
        )
    except ValueError as exc:
        if type(exc).__name__ == "IRError":
            raise
        raise ConfigError(str(exc)) from exc
    paths = result.write(args.out)
    if not args.quiet:
        r = result.data["result"]
        for name, value in r["best"].items():
            lo, hi = r["interval"][name]
            print(f"  {name}: {value:.4g} ({lo:.4g} to {hi:.4g}), distance {r['best_distance']:.4g}")
        print(f"  interval: {r['interval_meaning']}")
        for w in r.get("warnings", []):
            print(f"  warning: {w}")
        print(f"  wrote {paths['json'].parent}")
    return 0


def cmd_translate(args: argparse.Namespace) -> int:
    """Run ``abmfact translate``; exit code 0 when the gains were calibrated, 2 for an invalid translation."""
    import yaml

    from ..config import ConfigError
    from .translate import TranslationSpec, load_translation, parse_free, translate

    def show(e: Any) -> None:
        values = ", ".join(f"{k.removeprefix('option:')} {v:.4g}" for k, v in e.values.items())
        print(f"  evaluation {e.index + 1:>3}: {values}  distance {e.distance:.4g}", flush=True)

    try:
        raw = yaml.safe_load(Path(args.target).read_text(encoding="utf-8")) or {}
        if isinstance(raw, dict) and "schema_version" in raw:
            if not (args.to and args.calibrate_on and args.hold_out and args.free):
                raise ConfigError("translating a model directly needs --to, --calibrate-on, --hold-out and --free")
            spec = TranslationSpec(
                title=Path(args.target).stem,
                model=Path(args.target).resolve(),
                to=args.to,
                calibrate_on=[s for s in args.calibrate_on.split(",") if s],
                hold_out=[s for s in args.hold_out.split(",") if s],
                free=parse_free([f for item in args.free for f in item.split(",") if f]),
                source={"engine": args.source_engine, "seeds": args.source_seeds, "seed_start": args.seed_start},
                options=pairs(args.option, "engine option"),
                params=pairs(args.param, "parameter"),
                seeds_per_evaluation=args.seeds,
                seed_start=args.seed_start,
                method=args.method,
                jobs=args.jobs or 1,
            )
            spec.check()
        else:
            spec = load_translation(args.target, {"jobs": args.jobs})
        result = translate(spec, workdir=args.workdir, store=args.store, on_evaluation=None if args.quiet else show)
    except ValueError as exc:
        if type(exc).__name__ == "IRError":
            raise
        raise ConfigError(str(exc)) from exc
    paths = result.write(args.out)
    if not args.quiet:
        d = result.data
        for gain, value in d["gains"].items():
            lo, hi = d["interval"][gain]
            print(
                f"  {gain}: {value:.4g} ({lo:.4g} to {hi:.4g}) on {', '.join(d['calibrate_on'])}, distance "
                f"{d['distance']:.4g}"
            )
        for row in d["held_out"]:
            diff = row.get("difference")
            iv = f"{diff['estimate']:+.4g} ({diff['low']:+.4g} to {diff['high']:+.4g})" if diff else "n/a"
            print(
                f"  held out {row['metric']}: source {row['source_mean']:.4g}, {d['engine']} {row['target_mean']:.4g}, "
                f"difference {iv}"
            )
        for w in d["warnings"]:
            print(f"  warning: {w}")
        print(f"  wrote {paths['json'].parent} (tier T2)")
    return 0


RESULT_FILES = ("ensemble.json", "sweep.json", "indices.json", "calibration.json", "calibration/calibration.json")


def cmd_visualise(args: argparse.Namespace) -> int:
    """Run ``abmfact visualise``; exit code 0 when something was drawn, 2 when the target is not recognised."""
    from ..config import ConfigError
    from ..records.store import RunStore, read_run
    from .visualise import visualise_result, visualise_run

    target, out = Path(args.target), Path(args.out)
    try:
        if target.is_file() and target.suffix == ".json":
            paths = visualise_result(target, out)
        elif target.is_dir() and (target / "record.json").exists():
            paths = _run_plots(read_run(target), out, args, visualise_run)
        elif target.is_dir() and any((target / f).exists() for f in RESULT_FILES):
            paths = visualise_result(next(target / f for f in RESULT_FILES if (target / f).exists()), out)
        elif args.store:
            store = RunStore(args.store)
            matches = [d for d in (store.root / "runs").glob(f"*/{args.target}*") if (d / "record.json").exists()]
            if len(matches) != 1:
                raise ConfigError(f"{len(matches)} runs in the store start with {args.target!r}; give more of the id")
            paths = _run_plots(read_run(matches[0]), out, args, visualise_run)
        else:
            raise ConfigError(
                f"{args.target} is not a result file, a result folder, a run folder, or a run id with --store"
            )
    except ValueError as exc:
        raise ConfigError(str(exc)) from exc
    if not args.quiet:
        for p in paths:
            print(f"  wrote {p}")
    return 0


def _run_plots(record: Any, out: Path, args: argparse.Namespace, draw: Any) -> list[Path]:
    from ..config import ConfigError

    if record.outputs is None:
        raise ConfigError("this run has no outputs to draw")
    return draw(
        record.outputs, out, times=args.times, colour=args.colour, fields=args.field, film=args.movie, vtk=args.vtk
    )
