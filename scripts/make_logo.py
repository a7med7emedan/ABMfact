"""Draw the ABMfact logo, mark and social preview, never by hand.

The mark shows three kinds of agent-based model side by side: agents on a lattice, off-lattice cells and
cellular Potts cells, with one lens over them. Run from the repository root::

    python scripts/make_logo.py        # writes assets/logo*.svg, assets/mark*.svg, assets/social-preview.svg

The social preview PNG for GitHub's settings is converted from the SVG with ``rsvg-convert``.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from pathlib import Path

SANS = "Inter, 'Segoe UI', Helvetica, Arial, sans-serif"


@dataclass(frozen=True)
class Palette:
    cell: str
    soft: str
    lens: str
    ink: str
    sub: str
    tile: str
    page: str


LIGHT = Palette(cell="#0f766e", soft="#99c9c3", lens="#d97706", ink="#1d2430", sub="#5b6575", tile="#f4f8f7",
                page="#ffffff")  # fmt: skip
DARK = Palette(cell="#5eead4", soft="#2f6f68", lens="#fbbf24", ink="#e6e9ee", sub="#9aa4b2", tile="#161b22",
               page="#0d1117")  # fmt: skip

# Agents on a lattice: two columns of six sites.
LATTICE = [(7 + 9 * i, 9 + 9 * j, (i + j) % 3 == 2) for i in range(2) for j in range(6)]
# Off-lattice cells of different sizes: (x, y, radius, light).
FREE = [(27, 9, 3.6, False), (37, 12, 3.0, True), (25, 19, 2.8, True), (24, 41, 3.8, False), (35, 44, 2.8, True),
        (27, 52, 3.0, False), (38, 55, 3.6, False)]  # fmt: skip
# Cellular Potts cells: an irregular tessellation in two staggered columns, each tile shrunk towards its centre
# to leave a gap.
POTTS = [
    [(44, 3), (52, 3), (54, 9), (51, 16), (44, 15)],
    [(52, 3), (62, 3), (62, 20), (56, 22), (51, 16), (54, 9)],
    [(44, 15), (51, 16), (56, 22), (55, 30), (48, 33), (44, 30)],
    [(56, 22), (62, 20), (62, 38), (57, 40), (55, 30)],
    [(44, 30), (48, 33), (55, 30), (57, 40), (52, 46), (44, 45)],
    [(57, 40), (62, 38), (62, 56), (56, 57), (52, 46)],
    [(44, 45), (52, 46), (56, 57), (54, 61), (44, 61)],
    [(56, 57), (62, 56), (62, 61), (54, 61)],
]
POTTS_LIGHT = (False, True, True, False, False, True, True, False)
LENS = (31.0, 30.0)


def _inset(poly: list[tuple[float, float]], k: float = 0.84) -> list[tuple[float, float]]:
    cx = sum(x for x, _ in poly) / len(poly)
    cy = sum(y for _, y in poly) / len(poly)
    return [(cx + (x - cx) * k, cy + (y - cy) * k) for x, y in poly]


def mark_body(p: Palette) -> list[str]:
    """The mark's shapes on a 64 by 64 grid."""
    out = [f'<circle cx="{x}" cy="{y}" r="2.8" fill="{p.soft if light else p.cell}"/>' for x, y, light in LATTICE]
    out += [f'<circle cx="{x}" cy="{y}" r="{r}" fill="{p.soft if light else p.cell}"/>' for x, y, r, light in FREE]
    for poly, light in zip(POTTS, POTTS_LIGHT, strict=True):
        fill = p.soft if light else p.cell
        points = " ".join(f"{x:.2f},{y:.2f}" for x, y in _inset(poly))
        out.append(f'<polygon points="{points}" fill="{fill}" stroke="{fill}" stroke-width="1.2" '
                   f'stroke-linejoin="round"/>')  # fmt: skip
    x, y = LENS
    out += [
        f'<circle cx="{x:g}" cy="{y:g}" r="3.4" fill="none" stroke="{p.lens}" stroke-width="1.8" '
        f'stroke-dasharray="2.2 1.6"/>',
        f'<circle cx="{x:g}" cy="{y:g}" r="7.6" fill="none" stroke="{p.lens}" stroke-width="2.4"/>',
        f'<line x1="{x + 5.6:g}" y1="{y + 5.6:g}" x2="{x + 11.5:g}" y2="{y + 11.5:g}" stroke="{p.lens}" '
        f'stroke-width="3.4" stroke-linecap="round"/>',
    ]
    return out


def _svg(width: int, height: int, title: str, body: list[str]) -> str:
    head = (
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width} {height}" width="{width}" height="{height}" '
        f'role="img" aria-labelledby="t">\n  <title id="t">{title}</title>\n'
    )
    return head + "".join(f"  {line}\n" for line in body) + "</svg>\n"


def _wordmark(p: Palette, x: float, y: float, size: float) -> list[str]:
    return [
        f'<text x="{x:g}" y="{y:g}" font-family="{SANS}" font-size="{size:g}" letter-spacing="{-size / 68:g}" '
        f'fill="{p.ink}"><tspan font-weight="700">ABM</tspan><tspan font-weight="400" fill="{p.cell}">fact</tspan>'
        f"</text>",
        f'<text x="{x + size / 17:g}" y="{y + size * 16 / 34:g}" font-family="{SANS}" font-size="{size * 10.5 / 34:g}" '
        f'letter-spacing="{size * 0.6 / 34:g}" fill="{p.sub}">FINDING OR ARTEFACT?</text>',
    ]


def logo(p: Palette) -> str:
    """The mark and the wordmark, 300 by 64."""
    return _svg(300, 64, "ABMfact", mark_body(p) + _wordmark(p, 74, 41, 34))


def mark(p: Palette) -> str:
    """The mark alone on a rounded tile, for favicons and avatars."""
    return _svg(64, 64, "ABMfact mark", [f'<rect width="64" height="64" rx="14" fill="{p.tile}"/>', *mark_body(p)])


def social_preview(p: Palette) -> str:
    """The 1280 by 640 card GitHub shows when the repository is shared."""
    body = [f'<rect width="1280" height="640" fill="{p.page}"/>']
    body.append(f'<g transform="translate(112 192) scale(4)">{"".join(mark_body(p))}</g>')
    body += _wordmark(p, 440, 330, 120)
    lines = ["Run agent-based models across simulators through one", "interface, and check that their conclusions are",
             "findings, not implementation artefacts."]  # fmt: skip
    for k, line in enumerate(lines):
        y = 452 + k * 40
        body.append(f'<text x="447" y="{y}" font-family="{SANS}" font-size="28" fill="{p.ink}">{line}</text>')
    return _svg(1280, 640, "ABMfact", body)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--out", type=Path, default=Path("assets"))
    args = parser.parse_args(argv)
    args.out.mkdir(parents=True, exist_ok=True)
    for suffix, palette in (("", LIGHT), ("-dark", DARK)):
        (args.out / f"logo{suffix}.svg").write_text(logo(palette), encoding="utf-8")
        (args.out / f"mark{suffix}.svg").write_text(mark(palette), encoding="utf-8")
    (args.out / "social-preview.svg").write_text(social_preview(LIGHT), encoding="utf-8")
    print(f"wrote logo, mark and social preview to {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
