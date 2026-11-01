"""Self-contained HTML pages for the modes, in the same style as the audit report."""

from __future__ import annotations

import html
import json
from collections.abc import Sequence
from typing import Any

from ..audit import _clean, _json_default
from ..report import CSS


def esc(x: Any) -> str:
    """``x`` as HTML-escaped text."""
    return html.escape(str(x), quote=True)


def num(x: Any) -> str:
    """A number with four significant digits; anything else as text, and ``n/a`` for None."""
    if x is None:
        return "n/a"
    if isinstance(x, bool) or not isinstance(x, int | float):
        return esc(x)
    return f"{x:.4g}"


def table(headers: Sequence[str], rows: Sequence[Sequence[Any]]) -> str:
    """An HTML table; numbers get four significant digits."""
    head = "".join(f"<th>{esc(h)}</th>" for h in headers)
    body = "".join("<tr>" + "".join(f"<td>{num(c)}</td>" for c in r) + "</tr>" for r in rows)
    return f"<div class='scroll'><table><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table></div>"


def page(title: str, subtitle: str, sections: Sequence[tuple[str, str]]) -> str:
    """A whole HTML page: a title, a muted subtitle and ``(heading, html)`` sections."""
    body = [f"<main><p class='muted'>{esc(subtitle)}</p><h1>{esc(title)}</h1>"]
    body += [f"<h2>{esc(h)}</h2>{content}" for h, content in sections]
    body.append("</main>")
    head = (
        "<!doctype html><html lang='en'><head><meta charset='utf-8'>"
        "<meta name='viewport' content='width=device-width,initial-scale=1'>"
        f"<title>{esc(title)}</title><style>{CSS}</style></head><body>"
    )
    return head + "".join(body) + "</body></html>\n"


def dumps(data: Any) -> str:
    """``data`` as indented JSON, with NumPy values and non-finite numbers made plain."""
    return json.dumps(_clean(data), indent=2, default=_json_default) + "\n"
