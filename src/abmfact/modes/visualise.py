"""Visualise mode: plots, snapshots, fields and movies from the common output schema, for any engine.

* An ``ensemble.json`` gives one time-series plot per observable: the mean, its interval, and the 5% to 95% band.
* A ``sweep.json`` gives each metric's point means against each parameter, with intervals.
* An ``indices.json`` gives the sensitivity indices with their intervals.
* A ``calibration.json`` gives the distance of every evaluation, and the best fit against the target.
* A run (a folder written by ``run-model``, or a run id in a store) gives cell snapshots coloured by type or
  state, substrate fields, a movie (GIF, or MP4 through imageio-ffmpeg), and a VTK export for ParaView.

Plots need matplotlib and movies imageio, both in the optional ``[viz]`` extra. The VTK export needs neither.
"""

from __future__ import annotations

import json
import math
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import numpy as np

from ..records.schema import RunOutputs

STATE_NAMES = {0: "live", 1: "apoptotic", 2: "necrotic"}


def _pyplot() -> Any:
    try:
        import matplotlib
    except ImportError as exc:  # pragma: no cover - depends on the environment
        raise ImportError('plots need matplotlib: pip install "abmfact[viz]"') from exc
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    return plt


def _safe(name: str) -> str:
    return "".join(c if c.isalnum() or c in "-_" else "_" for c in name)


def _save(fig: Any, path: Path) -> Path:
    fig.tight_layout()
    fig.savefig(path, dpi=120, facecolor="white")
    _pyplot().close(fig)
    return path


# -- mode results ----------------------------------------------------------------------------------------------


def plot_ensemble(data: dict[str, Any], out: Path) -> list[Path]:
    """One plot per observable: the mean over seeds, its interval, and the 5% to 95% band."""
    plt = _pyplot()
    e = data["ensemble"]
    paths = []
    for name, rows in data["series"].items():
        t = np.array([r["time"] for r in rows])
        fig, ax = plt.subplots(figsize=(6, 4))
        ax.fill_between(
            t,
            [r["q05"] for r in rows],
            [r["q95"] for r in rows],
            color="#0f766e",
            alpha=0.15,
            label="5% to 95% of seeds",
        )
        ax.fill_between(
            t,
            [r["low"] for r in rows],
            [r["high"] for r in rows],
            color="#0f766e",
            alpha=0.35,
            label=e.get("interval", "interval for the mean"),
        )
        ax.plot(t, [r["mean"] for r in rows], color="#0f766e", label="mean")
        ax.set(xlabel="time (min)", ylabel=name, title=f"{name}: {e['engine']}, {e['seeds']} seeds")
        ax.legend(frameon=False, fontsize=8)
        paths.append(_save(fig, out / f"ensemble_{_safe(name)}.png"))
    return paths


def plot_sweep(data: dict[str, Any], out: Path) -> list[Path]:
    """Each metric's point means, with intervals, against each varied parameter."""
    plt = _pyplot()
    s = data["sweep"]
    paths = []
    for metric in s["metrics"]:
        for p in s["parameters"]:
            xs, ys, lo, hi = [], [], [], []
            for row in data["summary"]:
                m = row["metrics"].get(metric)
                if m:
                    xs.append(row["values"][p["name"]])
                    ys.append(m["mean"])
                    lo.append(m["mean"] - m["low"])
                    hi.append(m["high"] - m["mean"])
            fig, ax = plt.subplots(figsize=(6, 4))
            ax.errorbar(xs, ys, yerr=[lo, hi], fmt="o", color="#0f766e", ecolor="#5b6575", capsize=2)
            if p.get("scale") == "log":
                ax.set_xscale("log")
            ax.set(xlabel=p["name"], ylabel=metric, title=f"{metric}: {s['design']['method']} sweep")
            paths.append(_save(fig, out / f"sweep_{_safe(metric)}_{_safe(p['name'])}.png"))
    return paths


def plot_sensitivity(data: dict[str, Any], out: Path) -> list[Path]:
    """Sobol S1 and ST, or Morris mu* and sigma, per parameter with their intervals."""
    plt = _pyplot()
    method = data["sensitivity"]["method"]
    paths = []
    for metric, m in data["metrics"].items():
        if "indices" not in m:
            continue
        names = list(m["indices"])
        y = np.arange(len(names))
        fig, ax = plt.subplots(figsize=(6, 1.2 + 0.5 * len(names)))
        if method == "sobol":
            for k, (key, colour) in enumerate((("S1", "#0f766e"), ("ST", "#8a5a00"))):
                vals = [m["indices"][n][key] for n in names]
                errs = [m["indices"][n][f"{key}_conf"] for n in names]
                ax.barh(y + (k - 0.5) * 0.38, vals, height=0.36, xerr=errs, color=colour, label=key, capsize=2)
            ax.set_xlabel("index")
        else:
            vals = [m["indices"][n]["mu_star"] for n in names]
            errs = [m["indices"][n]["mu_star_conf"] for n in names]
            ax.barh(y, vals, xerr=errs, color="#0f766e", label="mu*", capsize=2)
            ax.set_xlabel("mu* (per unit of the scaled range)")
        ax.set_yticks(y, names, fontsize=8)
        ax.set_title(f"{metric}: {method}")
        ax.legend(frameon=False, fontsize=8)
        paths.append(_save(fig, out / f"sensitivity_{_safe(metric)}.png"))
    return paths


def plot_calibration(data: dict[str, Any], out: Path) -> list[Path]:
    """The distance of each evaluation with the best so far, and the best fit against the target."""
    plt = _pyplot()
    paths = []
    d = [e["distance"] for e in data["evaluations"]]
    finite = [x if math.isfinite(x) else np.nan for x in d]
    fig, ax = plt.subplots(figsize=(6, 4))
    ax.semilogy(range(1, len(d) + 1), finite, "o", color="#5b6575", ms=3, label="evaluation")
    ax.semilogy(
        range(1, len(d) + 1),
        np.fmin.accumulate(np.nan_to_num(finite, nan=np.inf)),
        color="#0f766e",
        label="best so far",
    )
    ax.set(
        xlabel="evaluation",
        ylabel="distance",
        title=f"{data['calibration']['method']}: {data['calibration']['engine']}",
    )
    ax.legend(frameon=False, fontsize=8)
    paths.append(_save(fig, out / "calibration_distance.png"))
    fit = data["fit"].get("best_fit")
    if fit:
        for name in data["fit"]["observables"]:
            rows = [r for r in fit if r["observable"] == name]
            t = [r["time"] for r in rows]
            fig, ax = plt.subplots(figsize=(6, 4))
            ax.errorbar(
                t,
                [r["target"] for r in rows],
                yerr=[r["scale"] for r in rows],
                fmt="o",
                color="#5b6575",
                capsize=2,
                label="target (± scale)",
            )
            ax.plot(t, [r["simulated"] for r in rows], color="#0f766e", label="best fit")
            ax.set(xlabel="time (min)", ylabel=name, title=f"{name}: best fit against the target")
            ax.legend(frameon=False, fontsize=8)
            paths.append(_save(fig, out / f"calibration_fit_{_safe(name)}.png"))
    return paths


# -- single runs -----------------------------------------------------------------------------------------------


def _bounds(outputs: RunOutputs) -> tuple[float, float, float, float]:
    x, y = outputs.cells["x"], outputs.cells["y"]
    r = float(np.max(np.cbrt(3 * outputs.cells["volume"] / (4 * math.pi)))) if x.size else 1.0
    if outputs.fields:
        f = outputs.fields[0]
        ny, nx = f.values.shape[-2:]
        return (
            f.origin[0] - f.spacing[0] / 2,
            f.origin[0] + (nx - 0.5) * f.spacing[0],
            f.origin[1] - f.spacing[1] / 2,
            f.origin[1] + (ny - 0.5) * f.spacing[1],
        )
    if not x.size:
        return -1.0, 1.0, -1.0, 1.0
    return float(x.min() - r), float(x.max() + r), float(y.min() - r), float(y.max() + r)


def draw_cells(
    ax: Any, outputs: RunOutputs, time: float, colour: str = "type", bounds: Sequence[float] | None = None
) -> None:
    """Draw the cells at ``time`` as discs of their equivalent radius, coloured by ``type`` or ``state``."""
    from matplotlib.collections import PatchCollection
    from matplotlib.patches import Circle

    cells = outputs.cells_at(time)
    key = cells["type"] if colour == "type" else cells["state"]
    names = dict(enumerate(outputs.type_names)) if colour == "type" else STATE_NAMES
    palette = ["#0f766e", "#8a5a00", "#3d5a80", "#a12a2a", "#6b7280", "#5b8c2a"]
    radius = np.cbrt(3 * cells["volume"] / (4 * math.pi))
    for k in sorted(set(int(v) for v in key)):
        sel = key == k
        patches = [Circle((x, y), r) for x, y, r in zip(cells["x"][sel], cells["y"][sel], radius[sel], strict=True)]
        ax.add_collection(
            PatchCollection(
                patches,
                facecolor=palette[k % len(palette)],
                edgecolor="white",
                linewidth=0.3,
                alpha=0.9,
                label=names.get(k, str(k)),
            )
        )
    x0, x1, y0, y1 = bounds or _bounds(outputs)
    ax.set_xlim(x0, x1)
    ax.set_ylim(y0, y1)
    ax.set_aspect("equal")
    ax.set(xlabel="x (micron)", ylabel="y (micron)", title=f"t = {time:g} min, {int(cells['x'].size)} cells")


def snapshot(outputs: RunOutputs, time: float, path: Path, colour: str = "type") -> Path:
    """A picture of the cells at one save time."""
    plt = _pyplot()
    fig, ax = plt.subplots(figsize=(5.5, 5))
    draw_cells(ax, outputs, time, colour)
    from matplotlib.patches import Patch

    handles = [Patch(color=c.get_facecolor()[0], label=c.get_label()) for c in ax.collections]
    ax.legend(handles=handles, frameon=False, fontsize=8, loc="upper right")
    return _save(fig, path)


def field_plot(outputs: RunOutputs, substrate: str, time: float, path: Path) -> Path:
    """A heat map of one substrate at one save time (the middle slice in 3D)."""
    plt = _pyplot()
    frame = outputs.field_at(substrate, time)
    if frame is None:
        raise ValueError(f"no {substrate} field at t = {time:g}")
    values = frame.values if frame.values.ndim == 2 else frame.values[frame.values.shape[0] // 2]
    ny, nx = values.shape
    ext = (
        frame.origin[0] - frame.spacing[0] / 2,
        frame.origin[0] + (nx - 0.5) * frame.spacing[0],
        frame.origin[1] - frame.spacing[1] / 2,
        frame.origin[1] + (ny - 0.5) * frame.spacing[1],
    )
    fig, ax = plt.subplots(figsize=(5.5, 4.6))
    im = ax.imshow(values, origin="lower", extent=ext, cmap="viridis")
    fig.colorbar(im, ax=ax, label=substrate)
    ax.set(xlabel="x (micron)", ylabel="y (micron)", title=f"{substrate} at t = {time:g} min")
    return _save(fig, path)


def movie(outputs: RunOutputs, path: Path, colour: str = "type", fps: int = 4) -> Path:
    """Every save time as one frame: a GIF, or an MP4 through imageio-ffmpeg when ``path`` ends in ``.mp4``."""
    plt = _pyplot()
    try:
        import imageio.v3 as iio
    except ImportError as exc:  # pragma: no cover - depends on the environment
        raise ImportError('movies need imageio: pip install "abmfact[viz]"') from exc
    bounds = _bounds(outputs)
    frames = []
    for t in outputs.times():
        fig, ax = plt.subplots(figsize=(5, 5), dpi=100)
        draw_cells(ax, outputs, t, colour, bounds)
        fig.tight_layout()
        fig.canvas.draw()
        frames.append(np.asarray(fig.canvas.buffer_rgba())[..., :3].copy())
        plt.close(fig)
    if not frames:
        raise ValueError("the run has no saved times to film")
    if path.suffix.lower() == ".mp4":
        try:
            import imageio_ffmpeg  # noqa: F401  (the plug-in imageio uses for MP4)
        except ImportError as exc:  # pragma: no cover - depends on the environment
            raise ImportError('MP4 needs imageio-ffmpeg: pip install "abmfact[viz]", or write a .gif') from exc
        import imageio.v2 as iio2

        h, w = (s - s % 16 for s in frames[0].shape[:2])  # H.264 wants sizes divisible by 16
        iio2.mimwrite(path, [f[:h, :w] for f in frames], format="FFMPEG", fps=fps, codec="libx264", macro_block_size=16)
    else:
        iio.imwrite(path, frames, duration=1000 / fps, loop=0)
    return path


def visualise_run(
    outputs: RunOutputs,
    out: Path,
    *,
    times: str = "last",
    colour: str = "type",
    fields: Sequence[str] | None = None,
    film: str | None = None,
    vtk: bool = False,
) -> list[Path]:
    """Snapshots, field maps, a movie and a VTK export of one run, as asked."""
    from .vtk import export_vtk

    out.mkdir(parents=True, exist_ok=True)
    saved = outputs.times()
    if not saved:
        raise ValueError("the run has no saved times")
    if times == "all":
        chosen = saved
    elif times == "last":
        chosen = [saved[-1]]
    else:
        chosen = [min(saved, key=lambda s: abs(s - float(t))) for t in times.split(",")]
    paths = []
    for t in chosen:
        paths.append(snapshot(outputs, t, out / f"cells_{colour}_t{t:g}.png", colour))
        for sub in fields if fields is not None else sorted({f.substrate for f in outputs.fields}):
            if outputs.field_at(sub, t) is not None:
                paths.append(field_plot(outputs, sub, t, out / f"field_{_safe(sub)}_t{t:g}.png"))
    if film:
        paths.append(movie(outputs, out / f"cells_{colour}.{film}", colour))
    if vtk:
        paths += list(export_vtk(outputs, out / "vtk").values())
    return paths


def visualise_result(path: Path, out: Path) -> list[Path]:
    """Plots for a mode's result file: ensemble, sweep, sensitivity or calibration."""
    data = json.loads(path.read_text(encoding="utf-8"))
    out.mkdir(parents=True, exist_ok=True)
    for key, plot in (
        ("ensemble", plot_ensemble),
        ("sweep", plot_sweep),
        ("sensitivity", plot_sensitivity),
        ("calibration", plot_calibration),
    ):
        if key in data:
            return plot(data, out)
    raise ValueError(f"{path.name} is not an ensemble, sweep, sensitivity or calibration result")
