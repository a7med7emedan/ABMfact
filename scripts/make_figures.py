"""Draw the README figures: the platform, the audit pipeline and the planted-fault matrix.

The matrix is drawn from a real run of ``abmfact.demo.run_demo``, never by hand. Run from the
repository root::

    python scripts/make_figures.py            # writes assets/platform*.svg, how-it-works*.svg and matrix*.svg
    python scripts/make_figures.py --no-matrix   # the two diagrams only, without the demo run
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass
from pathlib import Path
from xml.sax.saxutils import escape

from abmfact.demo import DemoResult, expected, run_demo

SANS = "-apple-system, BlinkMacSystemFont, 'Segoe UI', Helvetica, Arial, sans-serif"
MONO = "ui-monospace, SFMono-Regular, Menlo, Consolas, monospace"
WIDTH = 1200


@dataclass(frozen=True)
class Palette:
    ink: str
    muted: str
    teal: str
    teal_soft: str
    amber: str
    on_accent: str
    on_amber: str
    panel: str
    line: str
    cell: str


LIGHT = Palette(
    ink="#1d2430",
    muted="#5b6575",
    teal="#0f766e",
    teal_soft="#99c9c3",
    amber="#d97706",
    on_accent="#ffffff",
    on_amber="#1d2430",
    panel="#f6f8fa",
    line="#d5dbe2",
    cell="#eef1f4",
)
DARK = Palette(
    ink="#e6e9ee",
    muted="#9aa4b2",
    teal="#5eead4",
    teal_soft="#2f6f68",
    amber="#fbbf24",
    on_accent="#0d1117",
    on_amber="#0d1117",
    panel="#161b22",
    line="#30363d",
    cell="#1c232c",
)


def text(
    x: float, y: float, s: str, size: int, fill: str, weight: int = 400, anchor: str = "start", mono: bool = False
) -> str:
    family = MONO if mono else SANS
    return (
        f'<text x="{x:g}" y="{y:g}" font-family="{family}" font-size="{size}" font-weight="{weight}" '
        f'fill="{fill}" text-anchor="{anchor}">{escape(s)}</text>'
    )


def svg(height: int, title: str, body: list[str]) -> str:
    head = (
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {WIDTH} {height}" width="{WIDTH}" '
        f'height="{height}" role="img" aria-labelledby="t">\n  <title id="t">{escape(title)}</title>\n'
    )
    return head + "".join(f"  {line}\n" for line in body) + "</svg>\n"


# The pipeline -------------------------------------------------------------------------------------------------------

BOX_W, BOX_H, GAP, TOP = 168, 292, 32, 14


def _box(i: int, p: Palette, title: str, subtitle: str) -> tuple[float, list[str]]:
    x = 16 + i * (BOX_W + GAP)
    out = [
        f'<rect x="{x:g}" y="{TOP}" width="{BOX_W}" height="{BOX_H}" rx="12" fill="{p.panel}" stroke="{p.line}"/>',
        f'<circle cx="{x + 28:g}" cy="{TOP + 32}" r="13" fill="{p.teal}"/>',
        text(x + 28, TOP + 37.5, str(i + 1), 15, p.on_accent, 700, "middle"),
        text(x + 48, TOP + 38, title, 16, p.ink, 700),
        text(x + 16, TOP + 66, subtitle, 15, p.muted),
        f'<line x1="{x + 16:g}" y1="{TOP + 82}" x2="{x + BOX_W - 16:g}" y2="{TOP + 82}" stroke="{p.line}"/>',
    ]
    return x, out


def _arrow(i: int, p: Palette) -> str:
    x0 = 16 + i * (BOX_W + GAP) + BOX_W + 6
    x1 = x0 + GAP - 12
    y = TOP + BOX_H / 2
    return (
        f'<path d="M{x0:g} {y:g} H{x1 - 6:g}" stroke="{p.muted}" stroke-width="2"/>'
        f'<path d="M{x1 - 8:g} {y - 6:g} L{x1:g} {y:g} L{x1 - 8:g} {y + 6:g}" fill="none" stroke="{p.muted}" '
        f'stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/>'
    )


def _chip(x: float, y: float, w: float, label: str, fill: str, stroke: str, colour: str) -> list[str]:
    return [
        f'<rect x="{x:g}" y="{y:g}" width="{w:g}" height="30" rx="15" fill="{fill}" stroke="{stroke}" '
        f'stroke-width="2"/>',
        text(x + w / 2, y + 20, label, 14, colour, 700, "middle"),
    ]


def how_it_works(p: Palette) -> str:
    body: list[str] = []
    y0 = TOP + 112

    x, b = _box(0, p, "Model + claims", "audit.yaml")
    body += b
    for k, line in enumerate(["model: ...", "params: ...", "claims:", "  - C1 ...", "  - C2 ...", "budget: ..."]):
        body.append(text(x + 16, y0 + k * 26, line, 14, p.ink if k != 2 else p.teal, 400, mono=True))

    x, b = _box(1, p, "Conditions", "baseline + 6 changes")
    body += b
    body += _chip(x + 16, y0 - 20, 92, "baseline", p.teal, p.teal, p.on_accent)
    for k, knob in enumerate(["update order", "agent order", "rng streams", "grid", "time step", "population"]):
        yy = y0 + 32 + k * 25
        body.append(f'<circle cx="{x + 22:g}" cy="{yy - 5:g}" r="4" fill="none" stroke="{p.amber}" stroke-width="2"/>')
        body.append(text(x + 34, yy, knob, 15, p.ink))

    x, b = _box(2, p, "Seeds", "N per condition")
    body += b
    soft = {(0, 0), (3, 1), (1, 2), (4, 3), (2, 4), (5, 0), (0, 3), (5, 4)}
    for r in range(5):
        for c in range(6):
            fill = p.teal_soft if (c, r) in soft else p.teal
            body.append(f'<circle cx="{x + 30 + c * 23:g}" cy="{y0 - 10 + r * 23:g}" r="7" fill="{fill}"/>')
    body.append(text(x + 16, y0 + 128, "the same seeds", 15, p.muted))
    body.append(text(x + 16, y0 + 150, "in every condition", 15, p.muted))

    x, b = _box(3, p, "Statistics", "intervals and tests")
    body += b
    left, right = x + 22, x + BOX_W - 22
    body.append(
        f'<rect x="{left + 34:g}" y="{y0 - 22:g}" width="64" height="76" rx="4" fill="{p.teal_soft}" opacity="0.45"/>'
    )
    for k, (a, z, colour) in enumerate([(52, 82, p.teal), (58, 92, p.amber), (40, 106, p.muted)]):
        yy = y0 - 6 + k * 22
        body.append(
            f'<path d="M{left + a:g} {yy:g} H{left + z:g}" stroke="{colour}" stroke-width="3" stroke-linecap="round"/>'
        )
        body.append(f'<circle cx="{left + (a + z) / 2:g}" cy="{yy:g}" r="4.5" fill="{colour}"/>')
    body.append(f'<path d="M{left:g} {y0 + 62:g} H{right:g}" stroke="{p.line}"/>')
    for k, line in enumerate(["one-sided tests", "equivalence within", "a tolerance"]):
        body.append(text(x + 16, y0 + 94 + k * 22, line, 15, p.muted))

    x, b = _box(4, p, "Verdicts", "claim by check")
    body += b
    body += _chip(x + 16, y0 - 20, 140, "ROBUST", p.teal, p.teal, p.on_accent)
    body += _chip(x + 16, y0 + 24, 140, "SHIFTED", "none", p.amber, p.amber)
    body += _chip(x + 16, y0 + 68, 140, "FRAGILE", p.amber, p.amber, p.on_amber)
    body.append(text(x + 16, y0 + 136, "each with a reason", 15, p.muted))
    body.append(text(x + 16, y0 + 158, "and a reproducer", 15, p.muted))

    x, b = _box(5, p, "Outputs", "for people and CI")
    body += b
    for k, name in enumerate(["report.html", "results.json", "RO-Crate"]):
        yy = y0 - 20 + k * 52
        body.append(
            f'<path d="M{x + 18:g} {yy:g} h16 l8 8 v24 h-24 z" fill="none" stroke="{p.teal}" stroke-width="2" '
            f'stroke-linejoin="round"/>'
        )
        body.append(text(x + 52, yy + 22, name, 14, p.ink, 400, mono=True))

    body += [_arrow(i, p) for i in range(5)]
    return svg(TOP * 2 + BOX_H, "How ABMfact audits a model", body)


# The platform -------------------------------------------------------------------------------------------------------


def _lines(x: float, y0: float, items: list[tuple[str, str]], p: Palette, mono: bool = False) -> list[str]:
    colour = {"ink": p.ink, "muted": p.muted, "teal": p.teal}
    return [text(x, y0 + k * 26, s, 14 if mono else 15, colour[c], 400, mono=mono) for k, (s, c) in enumerate(items)]


def platform(p: Palette) -> str:
    body: list[str] = []
    y0 = TOP + 112

    x, b = _box(0, p, "Your model", "as it is, or ABM-IR")
    body += b
    body += _lines(
        x + 16,
        y0,
        [
            ("PhysiCell project", "ink"),
            ("ABM-IR file", "ink"),
            ("Python run()", "ink"),
            ("Mesa, NetLogo", "ink"),
            ("any program", "ink"),
            ("(JSON in, JSON out)", "muted"),
        ],
        p,
    )

    x, b = _box(1, p, "ABM-IR", "one shared format")
    body += b
    body += _chip(x + 16, y0 - 20, 136, "exact", p.teal, p.teal, p.on_accent)
    body += _chip(x + 16, y0 + 24, 136, "approximated", "none", p.amber, p.amber)
    body += _chip(x + 16, y0 + 68, 136, "dropped", p.amber, p.amber, p.on_amber)
    body += _lines(x + 16, y0 + 130, [("a fidelity report", "muted"), ("for every model", "muted")], p)

    x, b = _box(2, p, "Engines", "one contract")
    body += b
    body += _lines(
        x + 16,
        y0,
        [
            ("reference", "ink"),
            ("PhysiCell", "ink"),
            ("PhysiBoSS", "ink"),
            ("Mesa, NetLogo", "ink"),
            ("command", "ink"),
            ("next: CompuCell3D,", "muted"),
            ("Morpheus", "muted"),
        ],
        p,
    )

    x, b = _box(3, p, "Runtime", "where it runs")
    body += b
    for k, name in enumerate(["this machine", "container", "Slurm array"]):
        yy = y0 - 20 + k * 52
        body.append(f'<rect x="{x + 18:g}" y="{yy:g}" width="26" height="26" rx="5" fill="none" stroke="{p.teal}" '
                    f'stroke-width="2"/>')  # fmt: skip
        body.append(text(x + 54, yy + 18, name, 15, p.ink))
    body += _lines(x + 16, y0 + 138, [("pinned versions", "muted"), ("and image digests", "muted")], p)

    x, b = _box(4, p, "Run store", "one record per run")
    body += b
    for k, name in enumerate(["record.json", "cells", "fields"]):
        yy = y0 - 20 + k * 52
        body.append(
            f'<path d="M{x + 18:g} {yy:g} h16 l8 8 v24 h-24 z" fill="none" stroke="{p.teal}" stroke-width="2" '
            f'stroke-linejoin="round"/>'
        )
        body.append(text(x + 52, yy + 22, name, 14, p.ink, 400, mono=True))
    body += _lines(x + 16, y0 + 138, [("identical runs", "muted"), ("are reused", "muted")], p)

    x, b = _box(5, p, "Checks", "finding or artefact?")
    body += b
    body += _chip(x + 16, y0 - 20, 140, "7 audit checks", p.teal, p.teal, p.on_accent)
    body += _chip(x + 16, y0 + 24, 140, "convergence", "none", p.teal, p.teal)
    body += _lines(
        x + 16, y0 + 92, [("report.html", "ink"), ("results.json", "ink"), ("RO-Crate", "ink")], p, mono=True
    )

    body += [_arrow(i, p) for i in range(5)]
    return svg(TOP * 2 + BOX_H, "How the ABMfact platform fits together", body)


# The planted-fault matrix -------------------------------------------------------------------------------------------

HEADERS = {
    "determinism": ["determinism"],
    "update_order": ["update", "order"],
    "agent_order": ["agent", "order"],
    "rng_streams": ["rng", "streams"],
    "grid": ["grid"],
    "time_step": ["time", "step"],
    "population_scale": ["population", "scale"],
    "convergence": ["conver-", "gence"],
}


def matrix(demo: DemoResult, seeds: int, p: Palette) -> str:
    columns = demo.columns()
    label_w, top, cell_h, pad = 178, 74, 52, 4
    cell_w = (WIDTH - 32 - label_w) / len(columns)
    body: list[str] = []
    for j, check in enumerate(columns):
        cx = 16 + label_w + j * cell_w + cell_w / 2
        lines = HEADERS.get(check, [check.replace("_", " ")])
        for k, line in enumerate(lines):
            body.append(text(cx, top - 14 - (len(lines) - 1 - k) * 20, line, 15, p.muted, 600, "middle"))

    for i, (model, row) in enumerate(demo.flags.items()):
        y = top + i * cell_h
        is_clean = model in ("clean", "field_clean")
        colour = p.teal if is_clean else p.ink
        body.append(
            text(16 + label_w - 16, y + cell_h / 2 + 5, model, 15, colour, 600 if is_clean else 400, "end", True)
        )
        want = expected(model)
        for j, check in enumerate(columns):
            x = 16 + label_w + j * cell_w
            rx, ry, w, h = x + pad / 2, y + pad / 2, cell_w - pad, cell_h - pad
            cx, cy = x + cell_w / 2, y + cell_h / 2 + 5
            flagged = row.get(check, False)
            if flagged and check in want:
                body.append(f'<rect x="{rx:g}" y="{ry:g}" width="{w:g}" height="{h:g}" rx="6" fill="{p.amber}"/>')
                body.append(text(cx, cy, "caught", 15, p.on_amber, 700, "middle"))
            elif flagged:
                body.append(f'<rect x="{rx:g}" y="{ry:g}" width="{w:g}" height="{h:g}" rx="6" fill="{p.muted}"/>')
                body.append(text(cx, cy, "false alarm", 15, p.on_accent, 700, "middle"))
            elif check in want:
                body.append(
                    f'<rect x="{rx:g}" y="{ry:g}" width="{w:g}" height="{h:g}" rx="6" fill="{p.cell}" '
                    f'stroke="{p.amber}" stroke-width="2" stroke-dasharray="6 4"/>'
                )
                body.append(text(cx, cy, "missed", 15, p.amber, 700, "middle"))
            else:
                body.append(f'<rect x="{rx:g}" y="{ry:g}" width="{w:g}" height="{h:g}" rx="6" fill="{p.cell}"/>')

    foot = top + len(demo.flags) * cell_h + 34
    problems = demo.problems()
    note = (
        "Every planted fault was caught by its own check and by no other,"
        if not problems
        else "Deviations from the expected matrix: " + "; ".join(problems) + "."
    )
    body.append(text(16, foot, f"{seeds} seeds per condition. {note}", 15, p.muted))
    if not problems:
        overlap = "except dt_unscaled, also caught by convergence: both checks refine the time step."
        body.append(text(16, foot + 22, overlap, 15, p.muted))
    return svg(foot + 38, "Planted-fault detection matrix", body)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--seeds", type=int, default=100)
    parser.add_argument("--jobs", type=int, default=4)
    parser.add_argument("--out", type=Path, default=Path("assets"))
    parser.add_argument("--no-matrix", action="store_true", help="draw the diagrams only, without the demo run")
    args = parser.parse_args(argv)
    args.out.mkdir(parents=True, exist_ok=True)

    for suffix, palette in (("", LIGHT), ("-dark", DARK)):
        (args.out / f"platform{suffix}.svg").write_text(platform(palette), encoding="utf-8")
        (args.out / f"how-it-works{suffix}.svg").write_text(how_it_works(palette), encoding="utf-8")
    if args.no_matrix:
        return 0

    demo = run_demo(seeds=args.seeds, jobs=args.jobs, progress=lambda m: print(f"  auditing {m} ...", file=sys.stderr))
    for suffix, palette in (("", LIGHT), ("-dark", DARK)):
        (args.out / f"matrix{suffix}.svg").write_text(matrix(demo, args.seeds, palette), encoding="utf-8")
    print(demo.table())
    return 1 if demo.problems() else 0


if __name__ == "__main__":
    raise SystemExit(main())
