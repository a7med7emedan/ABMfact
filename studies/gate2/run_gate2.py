"""Run the Gate 2 study as fixed in PROTOCOL.md.

    python studies/gate2/run_gate2.py timing      # one seed at the coarsest and finest levels, per project
    python studies/gate2/run_gate2.py audit       # the audit checks and the determinism diagnostic
    python studies/gate2/run_gate2.py convergence # three levels of time step and voxel size
    python studies/gate2/run_gate2.py summary     # print the verdict tables recorded in results/

Set ABMFACT_GATE2_PHYSICELL to the PhysiCell 1.14.2 source tree and ABMFACT_GATE2_CACHE to a scratch
folder; build the image first with: docker build -t abmfact/physicell:1.14.2 containers/physicell
"""

from __future__ import annotations

import json
import os
import shutil
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from abmfact.audit import run_audit  # noqa: E402
from abmfact.config import load_audit  # noqa: E402
from abmfact.knobs import Knobs  # noqa: E402
from abmfact.report import write_outputs  # noqa: E402
from abmfact.verify.convergence import converge  # noqa: E402

PROJECTS = ("heterogeneity", "biorobots")
BUDGET_HOURS = 8.0
MIN_FREE_GB = 20.0
RESULTS = HERE / "results"


def free_gb() -> float:
    return shutil.disk_usage(os.environ.get("ABMFACT_GATE2_CACHE", HERE)).free / 1e9


def check_disk() -> None:
    gb = free_gb()
    if gb < MIN_FREE_GB:
        raise SystemExit(f"stopping: {gb:.1f} GB free, below the {MIN_FREE_GB:g} GB floor in PROTOCOL.md")


def timing() -> None:
    """Seed 1000 at level 0 and level 2 for each project; projects the protocol's compute and sets horizons."""
    import gate2_models as gm

    out: dict[str, dict[str, float]] = {}
    for name in PROJECTS:
        model = getattr(gm, name)
        horizon = gm.SHIPPED_HORIZON[name]
        row: dict[str, float] = {"shipped_horizon_min": horizon}
        for level, knobs in ((0, Knobs()), (2, Knobs(dt_scale=0.25, dx_scale=0.25))):
            check_disk()
            # Time a short slice and scale: the cost per simulated minute is close to constant.
            probe = min(horizon, 720.0)
            start = time.perf_counter()
            model.run({"overall/max_time": probe}, 1000, knobs)
            row[f"level{level}_seconds_per_sim_min"] = (time.perf_counter() - start) / probe
        # Runs per protocol: baseline 20, determinism 8, rng_streams 20, time_step 20 (cost x2),
        # convergence level 1 20 (cost about x8) and level 2 20 (measured); two runs at a time.
        l0, l2 = row["level0_seconds_per_sim_min"], row["level2_seconds_per_sim_min"]
        per_min = (20 + 8 + 20 + 20 * 2 + 20 * min(8.0, l2 / l0 / 2)) * l0 + 20 * l2
        row["projected_hours_at_shipped_horizon"] = per_min * horizon / 2 / 3600
        allowed = BUDGET_HOURS / 2 * 3600 * 2 / per_min
        row["horizon_min"] = horizon if allowed >= horizon else float(max(1440.0, 1440.0 * (allowed // 1440)))
        out[name] = row
        print(name, json.dumps(row, indent=2), flush=True)
    RESULTS.mkdir(exist_ok=True)
    (RESULTS / "timing.json").write_text(json.dumps(out, indent=2) + "\n", encoding="utf-8")


def _progress(label: str):
    def show(i: int, n: int) -> None:
        print(f"\r  {label} {i}/{n}", end="", flush=True)

    return show


def _horizons() -> None:
    timing_data = json.loads((RESULTS / "timing.json").read_text())
    for name in PROJECTS:
        os.environ[f"ABMFACT_GATE2_HORIZON_{name.upper()}"] = str(timing_data[name]["horizon_min"])


def audit() -> None:
    """The audit checks and, where determinism fails, the 1-thread diagnostic."""
    _horizons()
    for name in PROJECTS:
        check_disk()
        out = RESULTS / name
        out.mkdir(parents=True, exist_ok=True)
        audit_file = HERE / f"{name}.yaml"
        t0 = time.perf_counter()
        result = run_audit(load_audit(audit_file), progress=_progress(f"{name} audit"))
        print()
        write_outputs(result, out, audit_file=audit_file)
        diag = {}
        if any(v["check"] == "determinism" and v["verdict"] == "FRAGILE" for v in result.verdicts):
            diag = determinism_diagnostic(name, audit_file)
        (out / "determinism_diagnostic.json").write_text(json.dumps(diag, indent=2) + "\n", encoding="utf-8")
        (out / "audit_wall.json").write_text(json.dumps({"seconds": time.perf_counter() - t0}) + "\n", encoding="utf-8")
        print(f"  {name} audit done in {(time.perf_counter() - t0) / 3600:.2f} h", flush=True)


def convergence() -> None:
    """Convergence over three levels of time step and voxel size."""
    _horizons()
    for name in PROJECTS:
        check_disk()
        out = RESULTS / name
        out.mkdir(parents=True, exist_ok=True)
        t0 = time.perf_counter()
        conv = converge(load_audit(HERE / f"{name}.yaml"), progress=_progress(f"{name} convergence"))
        print()
        (out / "convergence.json").write_text(conv.to_json(), encoding="utf-8")
        (out / "convergence_wall.json").write_text(json.dumps({"seconds": time.perf_counter() - t0}) + "\n")
        print(f"  {name} convergence done in {(time.perf_counter() - t0) / 3600:.2f} h", flush=True)


def determinism_diagnostic(name: str, audit_file: Path) -> dict:
    """The same 8 seeds, twice each, at 1 thread: are the reruns identical?"""
    import gate2_models as gm

    a = load_audit(audit_file)
    model = getattr(gm, name)
    os.environ["ABMFACT_GATE2_THREADS"] = "1"
    rows = []
    try:
        for seed in range(8):
            first = model.run(a.params, seed, Knobs())
            # A second, uncached run of the same request: clear its cached metrics first.
            import hashlib

            horizon = os.environ[f"ABMFACT_GATE2_HORIZON_{name.upper()}"]
            key = hashlib.sha256(
                json.dumps(
                    [name, dict(a.params), seed, Knobs().to_dict(), float(horizon), "1"], sort_keys=True
                ).encode()
            ).hexdigest()[:24]
            cached = Path(os.environ["ABMFACT_GATE2_CACHE"]) / "metrics" / f"{key}.json"
            cached.unlink(missing_ok=True)
            second = model.run(a.params, seed, Knobs())
            same = {k: first[k] == second[k] for k in first if k != "wall_time"}
            rows.append({"seed": seed, "identical": all(same.values()), "first": first, "second": second})
    finally:
        os.environ.pop("ABMFACT_GATE2_THREADS", None)
    return {"threads": 1, "seeds": 8, "all_identical": all(r["identical"] for r in rows), "runs": rows}


def summary() -> None:
    for name in PROJECTS:
        out = RESULTS / name
        if not (out / "results.json").exists():
            print(f"{name}: not run")
            continue
        res = json.loads((out / "results.json").read_text())
        print(f"== {name}")
        for v in res["verdicts"]:
            extra = f" (seeds needed {v['seeds_needed']})" if "seeds_needed" in v else ""
            print(f"  {v['claim']} {v['check']:<14} {v['verdict']:<14} {v['reason']}{extra}")
        conv_file = out / "convergence.json"
        conv = json.loads(conv_file.read_text()) if conv_file.exists() else {"verdicts": []}
        for v in conv["verdicts"]:
            extra = f" (seeds needed {v['seeds_needed']})" if "seeds_needed" in v else ""
            print(f"  {v['claim']} convergence    {v['verdict']:<14} {v['reason']}{extra}")
        diag = json.loads((out / "determinism_diagnostic.json").read_text())
        if diag:
            print(f"  determinism at 1 thread: all identical = {diag['all_identical']}")


if __name__ == "__main__":
    for var in ("ABMFACT_GATE2_PHYSICELL", "ABMFACT_GATE2_CACHE"):
        if var not in os.environ and sys.argv[1:2] != ["summary"]:
            raise SystemExit(f"set {var} (see the module docstring)")
    {"timing": timing, "audit": audit, "convergence": convergence, "summary": summary}[sys.argv[1]]()
