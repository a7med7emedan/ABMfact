"""Write an audit's outputs: results.json, a self-contained report.html and an RO-Crate."""

from __future__ import annotations

import hashlib
import html
import json
import shlex
import shutil
from pathlib import Path
from typing import Any

from . import __version__
from .audit import BASELINE, RERUN, AuditResult
from .checks import CHECK_NAMES

VERDICT_CLASS = {
    "ROBUST": "ok",
    "SHIFTED": "warn",
    "FRAGILE": "bad",
    "INCONCLUSIVE": "unk",
    "NOT_TESTABLE": "na",
    "SKIPPED": "na",
    "BASELINE_FAILS": "bad",
}

CSS = """
:root{--bg:#fbfaf7;--fg:#1d2430;--muted:#5b6575;--line:#d9dde3;--card:#ffffff;
--ok:#1f7a4d;--ok-bg:#e3f3ea;--warn:#8a5a00;--warn-bg:#fbefd5;--bad:#a12a2a;--bad-bg:#f8e1e1;
--unk:#3d5a80;--unk-bg:#e3ebf5;--na:#6b7280;--na-bg:#eef0f3;--accent:#0f766e;--band:rgba(15,118,110,.12)}
@media (prefers-color-scheme: dark){:root:not([data-theme="light"]){--bg:#11151b;--fg:#e6e9ee;
--muted:#9aa4b2;--line:#2a313b;--card:#171c23;--ok:#6fd3a0;--ok-bg:#16301f;--warn:#f1c36b;
--warn-bg:#352a12;--bad:#f08b8b;--bad-bg:#3a1a1a;--unk:#9cc0ec;--unk-bg:#1a2738;--na:#9aa4b2;
--na-bg:#20262e;--accent:#5eead4;--band:rgba(94,234,212,.14)}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--fg);
font:15px/1.5 system-ui,-apple-system,"Segoe UI",Roboto,sans-serif}
main{max-width:1100px;margin:0 auto;padding:24px 16px 64px}
h1{font-size:1.6rem;margin:.2em 0}h2{font-size:1.2rem;margin:2em 0 .6em}h3{font-size:1rem;margin:1.4em 0 .4em}
.muted{color:var(--muted)}
.card{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:14px 16px}
.scroll{overflow-x:auto}table{border-collapse:collapse;width:100%}
th,td{border-bottom:1px solid var(--line);padding:6px 8px;text-align:left;vertical-align:top}
th{font-weight:600;font-size:.85rem;color:var(--muted)}
.v{display:inline-block;border-radius:6px;padding:1px 7px;font-size:.78rem;font-weight:600;white-space:nowrap}
.ok{color:var(--ok);background:var(--ok-bg)}.warn{color:var(--warn);background:var(--warn-bg)}
.bad{color:var(--bad);background:var(--bad-bg)}.unk{color:var(--unk);background:var(--unk-bg)}
.na{color:var(--na);background:var(--na-bg)}
code,pre{font:13px/1.45 ui-monospace,SFMono-Regular,Menlo,monospace}
pre{background:var(--na-bg);padding:10px 12px;border-radius:8px;overflow-x:auto;white-space:pre}
.chips{display:flex;flex-wrap:wrap;gap:8px;margin:10px 0}
svg text{fill:var(--fg);font:12px system-ui,sans-serif}svg .axis{stroke:var(--line)}
svg .band{fill:var(--band)}svg .thr{stroke:var(--bad);stroke-dasharray:4 3}
svg .ci{stroke:var(--accent);stroke-width:2}svg .pt{fill:var(--accent)}
svg .base{fill:var(--fg)}svg .flag{fill:var(--bad)}
"""


def _esc(x: Any) -> str:
    return html.escape(str(x), quote=True)


def _fmt(x: float | None) -> str:
    if x is None:
        return "n/a"
    return f"{x:.4g}"


def replay_command(audit_path: str | None, item: dict[str, Any]) -> str:
    parts = ["abmfact", "replay", audit_path or "audit.yaml", "--seed", str(item["seed"])]
    if item.get("params"):
        parts += ["--params", json.dumps(item["params"], sort_keys=True)]
    if item.get("knobs"):
        parts += ["--knobs", json.dumps(item["knobs"], sort_keys=True)]
    return " ".join(shlex.quote(p) for p in parts)


def _plot(claim: dict[str, Any], conds: dict[str, Any], pkey: str, flagged_labels: set[str]) -> str:
    """Horizontal interval plot: one row per condition, tolerance band around the baseline mean."""
    rows = [
        (label, c["claims"][claim["id"]][pkey])
        for label, c in conds.items()
        if label != RERUN and claim["id"] in c["claims"] and pkey in c["claims"][claim["id"]]
    ]
    if not rows:
        return ""
    base = conds[BASELINE]["claims"][claim["id"]][pkey]["estimate"]
    tol = claim["tolerance"].get(pkey) or 0.0
    lows = [r[1]["low"] for r in rows] + [base - tol]
    highs = [r[1]["high"] for r in rows] + [base + tol]
    thr: list[float] = []
    if claim["test"] in ("greater_than", "less_than"):
        thr = [float(claim["value"])]
    elif claim["test"] == "between":
        thr = [float(v) for v in claim["value"]]
    lo, hi = min(lows + thr), max(highs + thr)
    if hi - lo < 1e-12:
        lo, hi = lo - 1, hi + 1
    pad = (hi - lo) * 0.06
    lo, hi = lo - pad, hi + pad
    left, width, rh = 190, 470, 22
    height = 30 + rh * len(rows)

    def X(v: float) -> float:
        return left + (v - lo) / (hi - lo) * width

    out = [
        f'<svg viewBox="0 0 {left + width + 20} {height}" width="100%" role="img" '
        f'aria-label="{_esc(claim["id"])} by condition">'
    ]
    out.append(
        f'<rect class="band" x="{X(base - tol):.1f}" y="4" '
        f'width="{max(1.0, X(base + tol) - X(base - tol)):.1f}" height="{height - 26}"/>'
    )
    for t in thr:
        out.append(f'<line class="thr" x1="{X(t):.1f}" x2="{X(t):.1f}" y1="4" y2="{height - 22}"/>')
    for i, (label, iv) in enumerate(rows):
        y = 16 + i * rh
        cls = "base" if label == BASELINE else ("flag" if label in flagged_labels else "pt")
        out.append(f'<text x="{left - 8}" y="{y + 4}" text-anchor="end">{_esc(label)}</text>')
        out.append(f'<line class="ci" x1="{X(iv["low"]):.1f}" x2="{X(iv["high"]):.1f}" y1="{y}" y2="{y}"/>')
        out.append(f'<circle class="{cls}" cx="{X(iv["estimate"]):.1f}" cy="{y}" r="4"/>')
    yb = height - 8
    out.append(f'<line class="axis" x1="{left}" x2="{left + width}" y1="{yb - 10}" y2="{yb - 10}"/>')
    out.append(f'<text x="{left}" y="{yb}">{_fmt(lo)}</text>')
    out.append(f'<text x="{left + width}" y="{yb}" text-anchor="end">{_fmt(hi)}</text>')
    out.append("</svg>")
    return "".join(out)


def render_html(result: AuditResult) -> str:
    d = result.data
    audit = d["audit"]
    claims = d["claims"]
    conds = d["conditions"]
    matrix = result.matrix()
    rows_by = {(v["claim"], v["check"]): v for v in d["verdicts"]}
    checks = [c for c in CHECK_NAMES if any((cl["id"], c) in rows_by for cl in claims)]
    src = audit.get("source")

    h: list[str] = []
    h.append(f"<main><p class='muted'>ABMfact {_esc(d['tool']['version'])} · {_esc(d['created'])}</p>")
    h.append(f"<h1>{_esc(audit['title'])}</h1>")
    h.append(
        f"<p class='muted'>Model <code>{_esc(audit['model']['name'])}</code> · "
        f"{audit['budget']['seeds']} seeds per condition · alpha {audit['budget']['alpha']} "
        f"(intervals {round(100 * (1 - 2 * audit['budget']['alpha']))}%)</p>"
    )
    h.append("<div class='chips'>")
    for name, count in d["summary"].items():
        if count:
            h.append(f"<span class='v {VERDICT_CLASS[name]}'>{_esc(name)} {count}</span>")
    h.append("</div>")

    h.append("<h2>Verdicts</h2><div class='card scroll'><table><thead><tr><th>Claim</th>")
    h += [f"<th>{_esc(c.replace('_', ' '))}</th>" for c in checks]
    h.append("</tr></thead><tbody>")
    for cl in claims:
        h.append(f"<tr><td><b>{_esc(cl['id'])}</b><br><span class='muted'>{_esc(cl['statement'])}</span></td>")
        for c in checks:
            v = rows_by.get((cl["id"], c))
            verdict = matrix.get(cl["id"], {}).get(c, "")
            title = _esc(v["reason"]) if v else ""
            cls = VERDICT_CLASS.get(verdict, "na")
            h.append(f"<td><span class='v {cls}' title='{title}'>{_esc(verdict)}</span></td>")
        h.append("</tr>")
    h.append("</tbody></table></div>")

    if d["diagnosis"]:
        h.append(
            "<h2>Likely causes</h2><div class='card'><table><thead><tr><th>Check</th><th>Cause</th>"
            "<th>Look first at</th></tr></thead><tbody>"
        )
        for f in d["diagnosis"]:
            h.append(f"<tr><td>{_esc(f['check'])}</td><td>{_esc(f['cause'])}</td><td>{_esc(f['fix'])}</td></tr>")
        h.append("</tbody></table></div>")

    h.append("<h2>Claims</h2>")
    for cl in claims:
        h.append(f"<h3>{_esc(cl['id'])}: {_esc(cl['statement'])}</h3>")
        target = f"{cl['test'].replace('_', ' ')}"
        if "value" in cl:
            target += f" {_esc(cl['value'])}"
        if cl.get("over"):
            target += f" over {_esc(cl['over'])}"
        h.append(
            f"<p class='muted'>Metric <code>{_esc(cl['metric'])}</code>, {target}. "
            f"Baseline: <b>{_esc(cl['baseline']['status'])}</b>.</p>"
        )
        flagged_labels: set[str] = set()
        for c in checks:
            v = rows_by.get((cl["id"], c))
            if v and v["verdict"] in ("FRAGILE", "SHIFTED"):
                flagged_labels |= {r["label"] for r in v.get("reproduce", [])}
        for pkey in cl["tolerance"]:
            params = json.loads(pkey)
            shown = {k: params[k] for k in sorted(cl.get("where", {}))} if cl.get("where") else {}
            if shown:
                h.append(f"<p class='muted'>At {_esc(json.dumps(shown))}</p>")
            h.append(f"<div class='card'>{_plot(cl, conds, pkey, flagged_labels)}</div>")
        details = [rows_by[(cl["id"], c)] for c in checks if (cl["id"], c) in rows_by]
        h.append(
            "<div class='card scroll' style='margin-top:10px'><table><thead><tr><th>Check</th><th>Verdict</th>"
            "<th>Why</th></tr></thead><tbody>"
        )
        for v in details:
            extra = f" Seeds needed: {v['seeds_needed']}." if v.get("seeds_needed") else ""
            h.append(
                f"<tr><td>{_esc(v['check'])}</td><td><span class='v {VERDICT_CLASS[v['verdict']]}'>"
                f"{_esc(v['verdict'])}</span></td><td>{_esc(v['reason'])}{_esc(extra)}</td></tr>"
            )
        h.append("</tbody></table></div>")
        repro = [r for v in details for r in v.get("reproduce", [])]
        if repro:
            h.append("<p class='muted'>Reproduce:</p><pre>")
            h.append("\n".join(_esc(replay_command(src, r)) for r in repro[:4]))
            h.append("</pre>")

    h.append("<h2>Run</h2><div class='card'><pre>")
    h.append(
        _esc(
            json.dumps(
                {
                    "environment": d["environment"],
                    "budget": audit["budget"],
                    "params": audit["params"],
                    "audit_sha256": audit["sha256"],
                },
                indent=2,
            )
        )
    )
    h.append("</pre></div>")
    h.append(
        "<p class='muted'>Shaded band: baseline mean ± tolerance. Dashed line: the claim's threshold. "
        "Bars: intervals over seeds.</p></main>"
    )
    title = _esc(audit["title"])
    return (
        "<!doctype html><html lang='en'><head><meta charset='utf-8'>"
        "<meta name='viewport' content='width=device-width,initial-scale=1'>"
        f"<title>{title}</title><style>{CSS}</style></head><body>" + "".join(h) + "</body></html>\n"
    )


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write_rocrate(out: Path, files: dict[str, str], result: AuditResult) -> Path:
    d = result.data
    graph: list[dict[str, Any]] = [
        {
            "@id": "ro-crate-metadata.json",
            "@type": "CreativeWork",
            "conformsTo": {"@id": "https://w3id.org/ro/crate/1.1"},
            "about": {"@id": "./"},
        },
        {
            "@id": "./",
            "@type": "Dataset",
            "name": d["audit"]["title"],
            "description": "Implementation-artefact audit of an agent-based model, produced by ABMfact.",
            "datePublished": d["created"],
            "license": {"@id": "https://spdx.org/licenses/CC-BY-4.0"},
            "hasPart": [{"@id": name} for name in files],
        },
        {
            "@id": "#abmfact",
            "@type": "SoftwareApplication",
            "name": "ABMfact",
            "version": __version__,
            "url": "https://github.com/a7med7emedan/ABMfact",
        },
        {
            "@id": "#run",
            "@type": "CreateAction",
            "name": "ABMfact audit run",
            "instrument": {"@id": "#abmfact"},
            "object": [{"@id": n} for n, kind in files.items() if kind == "input"],
            "result": [{"@id": n} for n, kind in files.items() if kind == "output"],
            "endTime": d["created"],
        },
    ]
    for name, kind in files.items():
        path = out / name
        graph.append(
            {
                "@id": name,
                "@type": "File",
                "name": name,
                "encodingFormat": {"json": "application/json", "html": "text/html", "yaml": "application/yaml"}.get(
                    path.suffix.lstrip(".").replace("yml", "yaml"), "application/octet-stream"
                ),
                "contentSize": str(path.stat().st_size),
                "sha256": _sha256(path),
                "description": "audit file" if kind == "input" else f"audit {path.stem}",
            }
        )
    meta = {"@context": "https://w3id.org/ro/crate/1.1/context", "@graph": graph}
    target = out / "ro-crate-metadata.json"
    target.write_text(json.dumps(meta, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return target


def write_outputs(result: AuditResult, out: str | Path, audit_file: str | Path | None = None) -> dict[str, Path]:
    out = Path(out)
    out.mkdir(parents=True, exist_ok=True)
    written: dict[str, Path] = {}
    files: dict[str, str] = {}
    if audit_file is not None and Path(audit_file).exists():
        dest = out / "audit.yaml"
        if Path(audit_file).resolve() != dest.resolve():
            shutil.copyfile(audit_file, dest)
        written["audit"] = dest
        files["audit.yaml"] = "input"
    rj = out / "results.json"
    rj.write_text(result.to_json(), encoding="utf-8")
    written["results"] = rj
    files["results.json"] = "output"
    rh = out / "report.html"
    rh.write_text(render_html(result), encoding="utf-8")
    written["report"] = rh
    files["report.html"] = "output"
    written["rocrate"] = write_rocrate(out, files, result)
    return written
