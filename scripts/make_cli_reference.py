"""Write the command-line reference page to docs/, generated from the argument parser, never by hand.

The page is built from the parser's actions rather than argparse's own help text, whose layout changes
between Python versions.

Run from the repository root:  python scripts/make_cli_reference.py
"""

from __future__ import annotations

import argparse
import re
from pathlib import Path

from abmfact.cli import build_parser

ROOT = Path(__file__).resolve().parent.parent

EXIT_CODES = {
    "run": "0 when the audit finished.",
    "check": "0 unless any claim is FRAGILE or SHIFTED, then 1.",
    "demo": "0 when the detection matrix is as expected, 1 otherwise.",
    "init": "0 when the template was written, 2 when the file exists and `--force` was not given.",
    "replay": "0 when the run finished.",
    "run-model": "0 when the run finished or was reused from the store, 1 when it failed.",
    "converge": "0 unless a claim metric is NOT_CONVERGED, then 1.",
    "import": "0 when the model was written.",
    "export": "0 when the project was written.",
    "engines": "0.",
    "recommend": "0, also when no engine can run the model; the output says why.",
}


def _subcommands(parser: argparse.ArgumentParser) -> argparse._SubParsersAction:
    return next(a for a in parser._actions if isinstance(a, argparse._SubParsersAction))


def _metavar(action: argparse.Action) -> str:
    if action.nargs == 0:
        return ""
    if action.choices:
        return "{" + ",".join(str(c) for c in action.choices) + "}"
    if action.metavar:
        return str(action.metavar)
    return action.dest.upper() if action.option_strings else action.dest


def _synopsis(name: str, sub: argparse.ArgumentParser) -> str:
    parts = ["abmfact", name]
    for action in sub._actions:
        if isinstance(action, argparse._HelpAction):
            continue
        if not action.option_strings:
            parts.append(_metavar(action))
            continue
        flag = max(action.option_strings, key=len)
        text = f"{flag} {_metavar(action)}".strip()
        parts.append(text if action.required else f"[{text}]")
    return " ".join(parts)


def _default(action: argparse.Action) -> str:
    if action.required or not action.option_strings:
        return "required"
    if action.nargs == 0 or action.default is None:
        return ""
    return f"`{action.default}`"


def _cell(text: str) -> str:
    # The default has its own column, so a trailing "(default: ...)" in the help is dropped.
    text = re.sub(r"\s*\(default: [^)]*\)$", "", text)
    return text.replace("|", "\\|")


def _sentence(text: str) -> str:
    return text[:1].upper() + text[1:] + "."


def render(parser: argparse.ArgumentParser) -> str:
    """The reference page for every subcommand of ``parser``."""
    subs = _subcommands(parser)
    helps = {a.dest: a.help or "" for a in subs._choices_actions}
    lines = [
        "# Command-line reference",
        "",
        "Generated from the argument parser by `scripts/make_cli_reference.py`. Every command also takes",
        "`-h` or `--help`, and `abmfact --version` prints the version. Every command exits with 2 on an",
        "invalid audit file, claim, model or engine, and prints the reason.",
        "",
    ]
    for name, sub in subs.choices.items():
        lines += [f"## `abmfact {name}`", "", _sentence(_cell(helps.get(name, ""))), ""]
        lines += ["```text", _synopsis(name, sub), "```", ""]
        rows = [a for a in sub._actions if not isinstance(a, argparse._HelpAction)]
        if rows:
            lines += ["| Argument | Meaning | Default |", "|---|---|---|"]
            for action in rows:
                names = ", ".join(f"`{s}`" for s in action.option_strings) or f"`{_metavar(action)}`"
                lines.append(f"| {names} | {_cell(action.help or '')} | {_default(action)} |")
            lines.append("")
        lines += [f"Exit status: {EXIT_CODES[name]}", ""]
    return "\n".join(lines)


def main() -> None:
    path = ROOT / "docs" / "cli-reference.md"
    path.write_text(render(build_parser()), encoding="utf-8")
    print(f"wrote {path.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
