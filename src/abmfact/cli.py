"""Command line: ``abmfact run | check | demo | init | replay | engines | run-model | import | export |
converge``."""

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
    """Print the verdicts that need attention, the verdict counts, likely causes and claim warnings.

    ``ROBUST``, ``NOT_TESTABLE`` and ``SKIPPED`` rows are left out. ``stream`` defaults to standard output.
    """
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
    for c in result.data["claims"]:
        for w in c.get("warnings", []):
            stream.write(f"  warning ({c['id']}): {w}\n")


def cmd_run(args: argparse.Namespace, fail_on_flag: bool) -> int:
    """Run ``abmfact run``, or ``abmfact check`` with ``fail_on_flag``; exit 1 if ``check`` flags a claim, else 0."""
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
    """Run ``abmfact demo``; exit code 1 when the detection matrix is not as expected, else 0."""
    from .demo import run_demo

    def note(model: str) -> None:
        if not args.quiet:
            print(f"  auditing {model} ...", file=sys.stderr, flush=True)

    res = run_demo(seeds=args.seeds or 100, jobs=args.jobs or 1, progress=note, convergence=not args.no_convergence)
    print(res.table())
    problems = res.problems()
    if problems:
        print("\nNot diagonal:\n  " + "\n  ".join(problems))
        return 1
    if "convergence" in res.columns():
        print("\nEvery planted fault was caught by its own check and by no other, except the documented overlap:")
        print("dt_unscaled is also caught by convergence, because both checks refine the time step.")
    else:
        print("\nEvery planted fault was caught by its own check and by no other.")
    return 0


def cmd_init(args: argparse.Namespace) -> int:
    """Run ``abmfact init``; exit code 2 when the output file exists without ``--force``, else 0."""
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
    """Run ``abmfact replay`` and print the run's outputs as JSON; exit code 0."""
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
    """Run ``abmfact engines`` and list each engine's availability and capabilities; exit code 0."""
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


def cmd_recommend(args: argparse.Namespace) -> int:
    """Run ``abmfact recommend`` and rank the engines for one model; exit code 0."""
    from .engines.recommend import recommend

    recs = recommend(args.model)
    if args.json:
        json.dump([r.to_dict() for r in recs], sys.stdout, indent=2)
        sys.stdout.write("\n")
        return 0
    runnable = [r for r in recs if r.runs]
    width = max(len(r.engine) for r in recs) + 2
    print(f"engines for {args.model}, best first:" if runnable else f"no engine can run {args.model}:")
    for k, r in enumerate(runnable, 1):
        if r.route == "translated":
            counts = ", ".join(f"{s} {n}" for s, n in r.fidelity.items() if n)
            how = f"translated ({r.tier}): {counts}"
        else:
            how = f"{r.route} ({r.tier}), nothing translated"
        here = "installed here" if r.available else f"not installed here: {r.detail}"
        print(f"{k:>2}. {r.engine:<{width}}{r.description}")
        print(f"    {'':<{width}}{how}; {here}")
        print(f"    {'':<{width}}testable checks: {', '.join(r.checks) or 'none'}")
        for path, status, note in r.losses if args.details else ():
            print(f"    {'':<{width}}{status:<13}{path}: {note}")
    blocked = [r for r in recs if not r.runs]
    if blocked:
        print("cannot run it:")
        for r in blocked:
            print(f"    {r.engine:<{width}}{'; '.join(r.reasons)}")
    if runnable and not args.details and any(r.losses for r in runnable):
        print("add --details to list every approximated or dropped part")
    return 0


def _options(pairs: list[str] | None) -> dict[str, Any]:
    """``KEY=VALUE`` engine options; a value is read as JSON when it parses, else kept as text."""
    out: dict[str, Any] = {}
    for pair in pairs or []:
        key, sep, value = pair.partition("=")
        if not sep:
            raise ConfigError(f"engine option {pair!r} must look like KEY=VALUE")
        try:
            out[key] = json.loads(value)
        except json.JSONDecodeError:
            out[key] = value
    return out


def cmd_run_model(args: argparse.Namespace) -> int:
    """Run ``abmfact run-model``; exit code 1 when the run failed, else 0."""
    from .engines import get_engine
    from .records.store import RunStore, write_run

    engine = get_engine(args.engine, **_options(args.option))
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


def _report(report: Any, path: Path, quiet: bool) -> None:
    md = path.with_suffix(".fidelity.md") if path.suffix else path / "fidelity.md"
    md.write_text(report.to_markdown(), encoding="utf-8")
    md.with_suffix(".json").write_text(json.dumps(report.to_dict(), indent=2) + "\n", encoding="utf-8")
    if not quiet:
        counts = ", ".join(f"{k} {v}" for k, v in report.counts().items())
        print(f"  fidelity {report.tier}: {counts}")
        for e in report.entries:
            if e.status != "exact":
                print(f"    {e.status:<12} {e.path}: {e.note}")
        print(f"  wrote {md}")


def cmd_import(args: argparse.Namespace) -> int:
    """Run ``abmfact import`` and write the ABM-IR model with its fidelity report; exit code 0."""
    from .engines.physicell.translate import import_project
    from .ir import save_model

    out = Path(args.output)
    model, report = import_project(args.project, out_dir=out.parent)
    save_model(model, out)
    if not args.quiet:
        print(f"  wrote {out}")
    _report(report, out, args.quiet)
    return 0


def cmd_export(args: argparse.Namespace) -> int:
    """Run ``abmfact export`` and write the project with its fidelity report; exit code 0."""
    from .engines.physicell.translate import export_project
    from .ir import load_model

    out = Path(args.output)
    report = export_project(load_model(args.model), out, base=args.base)
    if not args.quiet:
        print(f"  wrote {out}")
    _report(report, out, args.quiet)
    return 0


def cmd_converge(args: argparse.Namespace) -> int:
    """Run ``abmfact converge``; exit code 1 when any claim has not converged, else 0."""
    from .verify.convergence import ConvergenceError, ConvergenceSettings, converge

    audit = load_audit(args.audit, _overrides(args))
    try:
        settings = ConvergenceSettings.from_mapping(audit.convergence)
    except ConvergenceError as exc:
        raise ConfigError(str(exc)) from exc
    result = converge(audit, settings, progress=_progress(args.quiet))
    if not args.quiet:
        levels = ", ".join(f"dt x{lv['dt_scale']:g}" + (f" dx x{lv['dx_scale']:g}" if lv["dx_scale"] != 1 else "")
                           for lv in result.data["levels"])  # fmt: skip
        print(f"  levels: {levels}")
        for v in result.verdicts:
            print(f"  {v['claim']:<6} {v['metric']:<24} {v['verdict']:<14} {v['reason']}")
            for pk, pv in v.get("points", {}).items():
                point = {k: x for k, x in json.loads(pk).items() if audit.params.get(k) != x}
                where = ", ".join(f"{k}={x}" for k, x in point.items()) or "base parameters"
                means = " -> ".join(f"{lv['estimate']:.4g}" if lv else "non-finite" for lv in pv["levels"])
                print(f"         {where}: {means}  {pv['verdict']}")
    if args.out:
        out = Path(args.out)
        out.mkdir(parents=True, exist_ok=True)
        (out / "convergence.json").write_text(result.to_json(), encoding="utf-8")
        if not args.quiet:
            print(f"  wrote {out / 'convergence.json'}")
    if result.any_not_converged:
        print("  not converged: " + ", ".join(v["claim"] for v in result.verdicts if v["verdict"] == "NOT_CONVERGED"),
              file=sys.stderr)  # fmt: skip
        return 1
    return 0


def cmd_compare(args: argparse.Namespace) -> int:
    """Run ``abmfact compare``; exit code 1 when any engine disagrees with the baseline, else 0."""
    import yaml

    from .verify.compare import CompareError, compare, comparison_from_model, load_comparison

    try:
        raw = yaml.safe_load(Path(args.target).read_text(encoding="utf-8")) or {}
        if "schema_version" in raw:  # an ABM-IR model: a quick comparison from the options
            if not args.engines or not args.metric:
                raise CompareError("comparing a model directly needs --engines and at least one --metric")
            cmp = comparison_from_model(args.target, args.engines.split(","), args.metric, args.budget,
                                        args.seeds or 10, args.jobs or 1)  # fmt: skip
        else:
            cmp = load_comparison(args.target, {"seeds": args.seeds, "jobs": args.jobs})
    except CompareError as exc:
        raise ConfigError(str(exc)) from exc
    result = compare(cmp, workdir=args.workdir, store=args.store, progress=_progress(args.quiet))
    if not args.quiet:
        _print_comparison(result.data)
    if args.out:
        out = Path(args.out)
        out.mkdir(parents=True, exist_ok=True)
        (out / "compare.json").write_text(result.to_json(), encoding="utf-8")
        if not args.quiet:
            print(f"  wrote {out / 'compare.json'}")
    return 1 if result.any_disagree else 0


def _print_comparison(d: dict[str, Any]) -> None:
    base = d["comparison"]["baseline"]
    print(f"  {d['comparison']['title']}: compared with {base}")
    for name, e in d["engines"].items():
        if not e["runs"]:
            print(f"  {name:<12} not run: {e['reason']}")
            continue
        fid = ", ".join(f"{k} {v}" for k, v in e.get("fidelity", {}).items() if v)
        runs = f"{e['ok'] + e['cached']} runs" + (f", {e['failed']} failed" if e["failed"] else "")
        print(f"  {name:<12} {e.get('tier', '')} {runs}" + (f"; translated: {fid}" if fid else ""))
    for v in d["verdicts"]:
        print(f"  {v['metric']:<22} {v['engine']:<12} {v['verdict']:<13} {v['reason']}")
    for c in d["claims"]:
        statuses = ", ".join(f"{n} {s}" for n, s in c["status"].items())
        same = "same conclusion" if c["same_conclusion"] else "DIFFERENT conclusions"
        print(f"  claim {c['claim']:<6} {same}: {statuses}")


def build_parser() -> argparse.ArgumentParser:
    """Build the argument parser with every subcommand and its options."""
    p = argparse.ArgumentParser(
        prog="abmfact",
        description="Run agent-based models across simulators and check whether their conclusions are findings "
        "or artefacts.",
    )
    p.add_argument("--version", action="version", version=f"abmfact {__version__}")
    sub = p.add_subparsers(dest="command", required=True)

    def common(sp: argparse.ArgumentParser) -> None:
        sp.add_argument("audit", help="audit YAML file")
        sp.add_argument("--seeds", type=int, help="override budget.seeds")
        sp.add_argument("--jobs", type=int, help="override budget.jobs")
        sp.add_argument("-q", "--quiet", action="store_true", help="print only errors")

    r = sub.add_parser("run", help="run an audit and write results")
    common(r)
    r.add_argument("--out", default="abmfact-results", help="output directory (default: abmfact-results)")
    c = sub.add_parser("check", help="run an audit; exit 1 if any claim is FRAGILE or SHIFTED")
    common(c)
    c.add_argument("--out", default=None, help="also write outputs to this directory")
    d = sub.add_parser("demo", help="audit the planted-fault zoo and print the detection matrix")
    d.add_argument("--seeds", type=int, default=100, help="seeds per condition")
    d.add_argument("--jobs", type=int, default=1, help="worker processes")
    d.add_argument("-q", "--quiet", action="store_true", help="no progress messages")
    d.add_argument("--no-convergence", action="store_true", help="only the seven audit checks (faster)")
    i = sub.add_parser("init", help="write a commented audit file template")
    i.add_argument(
        "--adapter",
        default="python",
        choices=["python", "mesa", "netlogo", "command", "engine"],
        help="the kind of model the template is for",
    )
    i.add_argument("-o", "--output", default="audit.yaml", help="file to write, or - for stdout")
    i.add_argument("--force", action="store_true", help="overwrite an existing file")
    rp = sub.add_parser("replay", help="run one model run and print its outputs")
    rp.add_argument("audit", help="audit YAML file")
    rp.add_argument("--seed", type=int, required=True, help="the seed to run")
    rp.add_argument("--params", help="JSON object merged into the audit's params")
    rp.add_argument("--knobs", help='JSON object of knobs, e.g. {"update_order": "id"}')
    rm = sub.add_parser("run-model", help="run one ABM-IR model (or native project) in an engine")
    rm.add_argument("model", help="ABM-IR file, or a native project folder for engines that take one")
    rm.add_argument("--engine", default="reference", help="engine name (see: abmfact engines)")
    rm.add_argument("--out", default="abmfact-run", help="output directory (default: abmfact-run)")
    rm.add_argument("--seed", type=int, default=0, help="the seed to run")
    rm.add_argument("--params", help='JSON object of parameter overrides, e.g. {"experiment.max_time": 600}')
    rm.add_argument("--knobs", help='JSON object of knobs, e.g. {"dt_scale": 0.5}')
    rm.add_argument("--store", help="run store directory; an identical earlier run is reused")
    rm.add_argument("--option", action="append", metavar="KEY=VALUE", help="engine option, e.g. threads=1")
    rm.add_argument("-q", "--quiet", action="store_true", help="print only errors")
    cv = sub.add_parser("converge", help="check that claim metrics converge as dt (and voxel size) are refined")
    common(cv)
    cv.add_argument("--out", default=None, help="write convergence.json to this directory")
    im = sub.add_parser("import", help="translate a simulator's project into an ABM-IR model")
    im.add_argument("source", choices=["physicell"], help="the simulator the project is for")
    im.add_argument("project", help="project folder")
    im.add_argument("-o", "--output", default="model.yaml", help="ABM-IR file to write (default: model.yaml)")
    im.add_argument("-q", "--quiet", action="store_true", help="print nothing; the fidelity report is still written")
    ex = sub.add_parser("export", help="translate an ABM-IR model into a simulator's project")
    ex.add_argument("model", help="ABM-IR file")
    ex.add_argument("--to", required=True, choices=["physicell"], help="the simulator to write for")
    ex.add_argument("-o", "--output", required=True, help="project folder to write")
    ex.add_argument("--base", help="project whose C++ to use (default: PhysiCell's template project)")
    ex.add_argument("-q", "--quiet", action="store_true", help="print nothing; the fidelity report is still written")
    cp = sub.add_parser("compare", help="run one model in several engines and compare against a disagreement budget")
    cp.add_argument("target", help="comparison YAML file, or an ABM-IR model with --engines and --metric")
    cp.add_argument("--engines", help="comma-separated engines for a model; the first is the baseline")
    cp.add_argument("--metric", action="append", help="metric to compare, e.g. 'last(cells)'; repeatable")
    cp.add_argument("--budget", type=float, default=0.10, help="relative budget for --metric (default 0.10)")
    cp.add_argument("--seeds", type=int, help="seeds per engine")
    cp.add_argument("--jobs", type=int, help="runs at once")
    cp.add_argument("--out", help="folder for compare.json")
    cp.add_argument("--store", help="run store: finished runs are kept and reused")
    cp.add_argument("--workdir", help="folder for run folders (default: a temporary folder)")
    cp.add_argument("-q", "--quiet", action="store_true", help="print only errors")
    en = sub.add_parser("engines", help="list engines, their capabilities and whether they run here")
    en.add_argument("--json", action="store_true", help="print JSON instead of a table")
    rc = sub.add_parser(
        "recommend", help="rank the engines for one model: can they run it, how faithfully, what checks"
    )
    rc.add_argument("model", help="ABM-IR file, or a native project folder")
    rc.add_argument("--details", action="store_true", help="list every approximated or dropped part")
    rc.add_argument("--json", action="store_true", help="print JSON instead of a table")
    return p


def main(argv: Sequence[str] | None = None) -> int:
    """Parse ``argv`` and run the chosen subcommand.

    Returns the exit code: 0 on success, 1 when a command reports a problem, and 2 for an input or configuration error.
    """
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
        if args.command == "recommend":
            return cmd_recommend(args)
        if args.command == "run-model":
            return cmd_run_model(args)
        if args.command == "import":
            return cmd_import(args)
        if args.command == "converge":
            return cmd_converge(args)
        if args.command == "export":
            return cmd_export(args)
        if args.command == "compare":
            return cmd_compare(args)
        return cmd_replay(args)
    except (ConfigError, ClaimError, AdapterError, EngineError, json.JSONDecodeError, FileNotFoundError) as exc:
        print(f"abmfact: error: {exc}", file=sys.stderr)
        return 2
    except ValueError as exc:
        # ABM-IR and PhysiCell settings errors; those modules are only imported when a command needs them.
        known = [getattr(sys.modules.get(m), n, None) for m, n in (("abmfact.ir.errors", "IRError"),
                 ("abmfact.engines.physicell.settings", "SettingsError"))]  # fmt: skip
        if any(k is not None and isinstance(exc, k) for k in known):
            print(f"abmfact: error: {exc}", file=sys.stderr)
            return 2
        raise


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
