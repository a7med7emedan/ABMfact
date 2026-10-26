"""Sphinx configuration for the ABMfact manual: HTML for the web, and a PDF through LaTeX.

Build from the repository root, with the ``[docs]`` extra installed:

    sphinx-build -W -b html docs docs/_build/html
    sphinx-build -W -b latex docs docs/_build/latex && make -C docs/_build/latex
    sphinx-build -W -b doctest docs docs/_build/doctest
"""

from __future__ import annotations

import importlib
import shutil
import subprocess
from pathlib import Path
from typing import Any

import abmfact

ROOT = Path(__file__).resolve().parent.parent

project = "ABMfact"
author = "Ahmed Hemedan"
copyright = "2026, Ahmed Hemedan"
release = abmfact.__version__
version = ".".join(release.split(".")[:2])

extensions = [
    "myst_parser",
    "sphinx.ext.autodoc",
    "sphinx.ext.napoleon",
    "sphinx.ext.doctest",
    "sphinx.ext.viewcode",
    "sphinxcontrib.rsvgconverter",
]

source_suffix = {".md": "markdown", ".rst": "restructuredtext"}
root_doc = "index"
# docs/README.md is the index for readers browsing the repository on GitHub.
exclude_patterns = ["_build", "README.md"]

myst_heading_anchors = 3
myst_enable_extensions = ["colon_fence"]

# Each object is documented once, in the module that defines it, not again where a package re-exports it.
autodoc_default_options = {"members": True, "show-inheritance": True, "ignore-module-all": True}
autodoc_member_order = "bysource"
autodoc_typehints = "signature"
napoleon_google_docstring = True
napoleon_numpy_docstring = False

# Examples in the manual read the example models shipped in the repository.
doctest_global_setup = f"from pathlib import Path\nEXAMPLES = Path({str(ROOT / 'examples')!r})"

html_theme = "furo"
html_title = f"ABMfact {release}"
html_static_path = ["../assets"]
html_favicon = "../assets/mark.svg"
html_theme_options = {"light_logo": "logo.svg", "dark_logo": "logo-dark.svg", "sidebar_hide_name": True}
html_show_sourcelink = False


def _latex_logo() -> str | None:
    """The logo as a PDF for the manual's title page, when ``rsvg-convert`` is installed to convert it."""
    converter = shutil.which("rsvg-convert")
    if converter is None:
        return None
    out = ROOT / "docs" / "_build" / "logo.pdf"
    out.parent.mkdir(parents=True, exist_ok=True)
    logo = ROOT / "assets" / "logo.svg"
    subprocess.run([converter, "-f", "pdf", "-o", str(out), str(logo)], check=True)  # noqa: S603 - fixed argv
    return str(out)


latex_engine = "xelatex"
latex_logo = _latex_logo()
latex_documents = [("index", "abmfact.tex", "ABMfact manual", author, "manual")]
latex_show_urls = "footnote"
latex_elements = {
    "papersize": "a4paper",
    "pointsize": "10pt",
    # One-sided, so chapters do not leave blank pages before them.
    "extraclassoptions": "openany,oneside",
    # Without this the contents page takes the first toctree caption as its title.
    "tableofcontents": r"\renewcommand{\contentsname}{Contents}\sphinxtableofcontents",
    # Code is never hyphenated, and tables use a smaller font so code fits their columns.
    "preamble": r"""
\setcounter{tocdepth}{1}
\usepackage[htt]{hyphenat}
\usepackage{etoolbox}
\AtBeginEnvironment{tabulary}{\small}
\AtBeginEnvironment{longtable}{\small}
""",
}


def _resolve_short_or_reexported(app: Any, env: Any, node: Any, contnode: Any) -> Any:
    """Link a Python reference that names a re-exported object, or a member by its bare name.

    Docstrings may say ``abmfact.records.RunRecord`` where the class is documented in
    ``abmfact.records.record``, or ``seed_for`` in a docstring inherited by an engine subclass.
    """
    if node.get("refdomain") != "py":
        return None
    target = node["reftarget"].lstrip("~.")
    domain = env.get_domain("py")
    candidates = []
    if target.startswith("abmfact."):
        module, _, name = target.rpartition(".")
        try:
            obj = getattr(importlib.import_module(module), name)
            candidates.append(f"{obj.__module__}.{obj.__qualname__}")
        except (ImportError, AttributeError):
            pass
    else:
        suffix = "." + target
        matches = {full for full, *_ in domain.get_objects() if full.endswith(suffix)}
        if len(matches) == 1:
            candidates.extend(matches)
    for full in candidates:
        found = domain.resolve_xref(env, node["refdoc"], app.builder, node["reftype"], full, node, contnode)
        if found is not None:
            return found
    return None


def setup(app: Any) -> None:
    app.connect("missing-reference", _resolve_short_or_reexported)
