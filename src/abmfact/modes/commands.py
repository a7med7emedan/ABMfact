"""Command line for the modes: ``abmfact ensemble`` and ``abmfact sweep``.

Only ``argparse`` is imported here at load time; each command imports what it needs when it runs.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

COMMANDS = ("ensemble", "sweep")


def add_parsers(sub: Any) -> None:
    """Add the mode subcommands to the main parser's subparsers."""
    en = sub.add_parser("ensemble", help="run many seeds of one setting in one engine and summarise with intervals")
    en.add_argument("model", help="ABM-IR file, or a native project folder for engines that take one")
    en.add_argument("--engine", default="reference", help="engine name (see: abmfact engines)")
    en.add_argument("--seeds", type=int, default=20, help="number of seeds (default 20)")
    en.add_argument("--seed-start", type=int, default=0, help="the first seed (default 0)")
    en.add_argument("--param", action="append", metavar="KEY=VALUE",
                    help="parameter for every run, e.g. 'experiment.max_time=600'; repeatable")  # fmt: skip
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
    sw.add_argument("--backend", choices=["local", "slurm"], default="local",
                    help="local runs here; slurm writes a job array for the runs not yet in the store")  # fmt: skip
    sw.add_argument("--submit", action="store_true", help="with --backend slurm: also submit the array with sbatch")
    sw.add_argument("--collect", action="store_true", help="read the runs a Slurm array wrote under OUT/slurm")
    sw.add_argument("-q", "--quiet", action="store_true", help="print only errors")


def run(args: argparse.Namespace) -> int:
    """Run the chosen mode command and return its exit code."""
    if args.command == "sweep":
        return cmd_sweep(args)
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
        result = run_ensemble(args.model, args.engine, args.seeds, seed_start=args.seed_start,
                              params=pairs(args.param, "parameter"), options=pairs(args.option, "engine option"),
                              metrics=args.metric or (), alpha=args.alpha, jobs=args.jobs, workdir=args.workdir,
                              store=args.store, progress=_progress(args.quiet))  # fmt: skip
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
                print(f"  {expr:<24} {m['mean']:>10.4g}  ({m['low']:.4g} to {m['high']:.4g}), sd {m['sd']:.3g}, "
                      f"n {m['n']}")  # fmt: skip
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
        result = run_sweep(spec, workdir=args.workdir, store=args.store, progress=_progress(args.quiet),
                           collect_from=out / "slurm" if args.collect else None)  # fmt: skip
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
