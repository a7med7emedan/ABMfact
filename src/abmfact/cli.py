"""Command line: ``abmfact run | check | demo | init | replay | engines | run-model``."""

from __future__ import annotations

import argparse
import json
import sys
import time
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from . import __version__
from .adapters import AdapterError
from .audit import FLAGGED, AuditResult, run_audit
from .claims import ClaimError
from .config import ConfigError, load_audit, template
from .engines.base import EngineError
from .knobs import Knobs


def _progress(quiet: bool) -> Any:
    if quiet or not sys.stderr.isatty():
        return None
    start = time.monotonic()

    def show(i: int, total: int) -> None:
        if i == total or i % max(1, total // 100) == 0:
            sys.stderr.write(f"\r  {i}/{total} runs  {time.monotonic() - start:5.0f}s")
            if i == total:
                sys.stderr.write("\n")
            sys.stderr.flush()

    return show


def _overrides(args: argparse.Namespace) -> dict[str, Any]:
    return {"seeds": args.seeds, "jobs": args.jobs}


def print_summary(result: AuditResult, stream: Any = None) -> None:
    stream = stream or sys.stdout
    rows = result.verdicts
    width = max([len(r["check"]) for r in rows] + [5])
    for r in rows:
        if r["verdict"] in ("ROBUST", "NOT_TESTABLE", "SKIPPED"):
            continue
        stream.write(f"  {r['claim']:<6} {r['check']:<{width}}  {r['verdict']:<14} {r['reason']}\n")
    counts = ", ".join(f"{k.lower()} {v}" for k, v in result.data["summary"].items() if v)
    stream.write(f"  {counts}\n")
    for f in result.data["diagnosis"]:
        stream.write(f"  likely cause ({f['check']}): {f['cause']}\n")


def cmd_run(args: argparse.Namespace, fail_on_flag: bool) -> int:
    from .report import write_outputs

    audit = load_audit(args.audit, _overrides(args))
    result = run_audit(audit, progress=_progress(args.quiet))
    if not args.quiet:
        print_summary(result)
    if getattr(args, "out", None):
        written = write_outputs(result, args.out, audit_file=args.audit)
        if not args.quiet:
            print(f"  wrote {', '.join(str(p) for p in written.values())}")
    if fail_on_flag and result.any_flagged:
        flagged = sorted({v["claim"] for v in result.verdicts if v["verdict"] in FLAGGED})
        print(f"  flagged claims: {', '.join(flagged)}", file=sys.stderr)
        return 1
    return 0


def cmd_demo(args: argparse.Namespace) -> int:
    from .demo import run_demo

    def note(model: str) -> None:
        if not args.quiet:
            print(f"  auditing {model} ...", file=sys.stderr, flush=True)

    res = run_demo(seeds=args.seeds or 100, jobs=args.jobs or 1, progress=note)
    print(res.table())
    problems = res.problems()
    if problems:
        print("\nNot diagonal:\n  " + "\n  ".join(problems))
        return 1
    print("\nEvery planted fault was caught by its own check and by no other.")
    return 0


def cmd_init(args: argparse.Namespace) -> int:
    text = template(args.adapter)
    if args.output == "-":
        sys.stdout.write(text)
        return 0
    path = Path(args.output)
    if path.exists() and not args.force:
        print(f"{path} exists; use --force to overwrite", file=sys.stderr)
        return 2
    path.write_text(text, encoding="utf-8")
    print(f"wrote {path}")
    return 0


def cmd_replay(args: argparse.Namespace) -> int:
    audit = load_audit(args.audit)
    params = dict(audit.params)
    if args.params:
        params.update(json.loads(args.params))
    knobs = Knobs.from_dict(json.loads(args.knobs)) if args.knobs else Knobs()
    out = audit.adapter.run(params, args.seed, knobs)
    json.dump({"params": params, "seed": args.seed, "knobs": knobs.changed(), "outputs": out}, sys.stdout, indent=2)
    sys.stdout.write("\n")
    return 0


def cmd_engines(args: argparse.Namespace) -> int:
    from .engines import list_engines

    rows: list[dict[str, Any]] = []
    for name, cls in list_engines().items():
        engine = cls()
        caps, avail = engine.capabilities(), engine.availability()
        rows.append({"name": name, "description": cls.description, "available": avail.ok, "detail": avail.detail,
                     "version": avail.version, "capabilities": caps.to_dict()})  # fmt: skip
    if args.json:
        json.dump(rows, sys.stdout, indent=2)
        sys.stdout.write("\n")
        return 0
    width = max(len(r["name"]) for r in rows) + 2
    for r in rows:
        c = r["capabilities"]
        mark = "available" if r["available"] else "not available"
        version = f" {r['version']}" if r["version"] else ""
        print(f"{r['name']:<{width}}{mark}{version}: {r['detail']}")
        print(f"{'':<{width}}{r['description']}; mechanics: {c['mechanics']}")
        dims = "/".join(f"{d}D" for d in c["dimensions"]) or "any"
        diffusion = "yes" if c["diffusion"] else "no"
        intra = ", ".join(c["intracellular"]) or "none"
        print(f"{'':<{width}}dimensions {dims}; diffusion {diffusion}; intracellular {intra}")
        print(f"{'':<{width}}knobs: {', '.join(c['knobs']) or 'none'}")
        if c["notes"]:
            print(f"{'':<{width}}{c['notes']}")
    return 0


def cmd_run_model(args: argparse.Namespace) -> int:
    from .engines import get_engine
    from .records.store import RunStore, write_run

    engine = get_engine(args.engine)
    params = json.loads(args.params) if args.params else {}
    knobs = Knobs.from_dict(json.loads(args.knobs)) if args.knobs else Knobs()
    out = Path(args.out)
    store = RunStore(args.store) if args.store else None
    record = engine.run_once(args.model, params, args.seed, knobs, workdir=out / "work", store=store)
    outputs = record.outputs
    record = write_run(record, out)
    if not args.quiet:
        wall = f"{record.wall_time:.1f}s" if record.wall_time is not None else "cached"
        print(f"  {engine.name}: {record.status} in {wall}, seed {args.seed}, run {record.run_id[:12]}")
        for w in record.warnings:
            print(f"  warning: {w}")
        if outputs is not None:
            times = outputs.times()
            for name, series in outputs.scalars.items():
                if name == "time" or not isinstance(series, list) or not series:
                    continue
                print(f"  {name:<20} {series[0]:>12.4g} at t={times[0]:g}  ->  {series[-1]:>12.4g} at t={times[-1]:g}")
        print(f"  wrote {out}")
    if record.status != "ok" and record.status != "cached":
        print(f"  run failed: {record.error}", file=sys.stderr)
        return 1
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="abmfact",
        description="Is it a finding or an artefact? Audit an agent-based model's conclusions.",
    )
    p.add_argument("--version", action="version", version=f"abmfact {__version__}")
    sub = p.add_subparsers(dest="command", required=True)

    def common(sp: argparse.ArgumentParser) -> None:
        sp.add_argument("audit", help="audit YAML file")
        sp.add_argument("--seeds", type=int, help="override budget.seeds")
        sp.add_argument("--jobs", type=int, help="override budget.jobs")
        sp.add_argument("-q", "--quiet", action="store_true")

    r = sub.add_parser("run", help="run an audit and write results")
    common(r)
    r.add_argument("--out", default="abmfact-results", help="output directory (default: abmfact-results)")
    c = sub.add_parser("check", help="run an audit; exit 1 if any claim is FRAGILE or SHIFTED")
    common(c)
    c.add_argument("--out", default=None, help="also write outputs to this directory")
    d = sub.add_parser("demo", help="audit the planted-fault zoo and print the detection matrix")
    d.add_argument("--seeds", type=int, default=100)
    d.add_argument("--jobs", type=int, default=1)
    d.add_argument("-q", "--quiet", action="store_true")
    i = sub.add_parser("init", help="write a commented audit file template")
    i.add_argument("--adapter", default="python", choices=["python", "mesa", "netlogo", "command"])
    i.add_argument("-o", "--output", default="audit.yaml", help="file to write, or - for stdout")
    i.add_argument("--force", action="store_true")
    rp = sub.add_parser("replay", help="run one model run and print its outputs")
    rp.add_argument("audit")
    rp.add_argument("--seed", type=int, required=True)
    rp.add_argument("--params", help="JSON object merged into the audit's params")
    rp.add_argument("--knobs", help='JSON object of knobs, e.g. {"update_order": "id"}')
    rm = sub.add_parser("run-model", help="run one ABM-IR model (or native project) in an engine")
    rm.add_argument("model", help="ABM-IR file, or a native project folder for engines that take one")
    rm.add_argument("--engine", default="reference", help="engine name (see: abmfact engines)")
    rm.add_argument("--out", default="abmfact-run", help="output directory (default: abmfact-run)")
    rm.add_argument("--seed", type=int, default=0)
    rm.add_argument("--params", help='JSON object of parameter overrides, e.g. {"experiment.max_time": 600}')
    rm.add_argument("--knobs", help='JSON object of knobs, e.g. {"dt_scale": 0.5}')
    rm.add_argument("--store", help="run store directory; an identical earlier run is reused")
    rm.add_argument("-q", "--quiet", action="store_true")
    en = sub.add_parser("engines", help="list engines, their capabilities and whether they run here")
    en.add_argument("--json", action="store_true", help="print JSON instead of a table")
    return p


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.command == "run":
            return cmd_run(args, fail_on_flag=False)
        if args.command == "check":
            return cmd_run(args, fail_on_flag=True)
        if args.command == "demo":
            return cmd_demo(args)
        if args.command == "init":
            return cmd_init(args)
        if args.command == "engines":
            return cmd_engines(args)
        if args.command == "run-model":
            return cmd_run_model(args)
        return cmd_replay(args)
    except (ConfigError, ClaimError, AdapterError, EngineError, json.JSONDecodeError, FileNotFoundError) as exc:
        print(f"abmfact: error: {exc}", file=sys.stderr)
        return 2
    except ValueError as exc:
        # ABM-IR errors; the ir package (and pydantic) is only imported when a command needs it.
        ir_errors = sys.modules.get("abmfact.ir.errors")
        if ir_errors is not None and isinstance(exc, ir_errors.IRError):
            print(f"abmfact: error: {exc}", file=sys.stderr)
            return 2
        raise


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
