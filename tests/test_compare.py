"""Comparing one model across engines against a disagreement budget."""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest

from abmfact.claims import Metric
from abmfact.cli import main
from abmfact.engines import registry
from abmfact.engines.base import Availability
from abmfact.engines.reference import ReferenceEngine
from abmfact.verify.compare import (
    Budget,
    CompareError,
    CompareMetric,
    Comparison,
    _verdicts,
    compare,
    load_comparison,
)

ROOT = Path(__file__).parent.parent
MODEL = ROOT / "examples" / "ir" / "monolayer.yaml"
SHORT = {"experiment.max_time": 240, "experiment.save_interval": 60}


class ScaledEngine(ReferenceEngine):
    """The reference engine with every output series multiplied by ``factor``: a stand-in second engine."""

    name = "scaled"

    def __init__(self, factor: float = 1.0, **kw):
        super().__init__(**kw)
        self.factor = float(factor)

    def collect(self, handle):
        rec = super().collect(handle)
        if rec.outputs is not None:
            for k, v in rec.outputs.scalars.items():
                if k != "time" and isinstance(v, list):
                    rec.outputs.scalars[k] = [x * self.factor for x in v]
        return rec


class AbsentEngine(ReferenceEngine):
    name = "absent"

    def availability(self) -> Availability:
        return Availability(False, "not installed in this test")


@pytest.fixture(autouse=True)
def engines(monkeypatch):
    monkeypatch.setitem(registry.BUILTIN, "scaled", "test_compare:ScaledEngine")
    monkeypatch.setitem(registry.BUILTIN, "absent", "test_compare:AbsentEngine")


def comparison(factor: float, budget: float = 0.1, **kw) -> Comparison:
    return Comparison(title="t", model=MODEL, engines=["reference", "scaled", "absent"], baseline="reference",
                      metrics=[CompareMetric(Metric.parse("last(cells)"), Budget(relative=budget))],
                      options={"scaled": {"factor": factor}}, params=SHORT, seeds=4, **kw)  # fmt: skip


def test_budgets_parse_and_scale():
    assert Budget.parse(5).margin(np.array([100.0])) == 5.0
    assert Budget.parse({"relative": 0.25}).margin(np.array([-80.0, -120.0])) == 25.0
    assert Budget.parse({"relative": 0.25}).describe() == "25% of the baseline mean"
    for bad in ({"relative": -1}, {"both": 1}, "ten", True):
        with pytest.raises(CompareError):
            Budget.parse(bad)


def test_the_same_engine_agrees_and_a_shifted_one_disagrees(tmp_path):
    same = compare(comparison(1.0), workdir=tmp_path / "a")
    assert [v["verdict"] for v in same.verdicts] == ["AGREE", "NOT_RUN"]
    assert "not installed" in same.verdicts[1]["reason"] and not same.any_disagree
    assert same.data["engines"]["scaled"]["ok"] == 4 and same.data["engines"]["absent"]["runs"] is False
    doubled = compare(comparison(2.0), workdir=tmp_path / "b")
    assert doubled.verdicts[0]["verdict"] == "DISAGREE" and doubled.any_disagree
    assert doubled.verdicts[0]["difference"]["estimate"] == pytest.approx(
        doubled.data["means"]["reference"]["last(cells)"]["estimate"]
    )


def test_wide_intervals_are_inconclusive_with_seeds_needed():
    cmp = Comparison(title="t", model=MODEL, engines=["a", "b"], baseline="a",
                     metrics=[CompareMetric(Metric.parse("last(x)"), Budget(absolute=1.0))])  # fmt: skip
    runs = {"a": {"runs": True}, "b": {"runs": True}}
    v = _verdicts(cmp, runs, {"a": {"last(x)": [0.0, 2.0, -2.0, 1.0]}, "b": {"last(x)": [1.0, -1.0, 3.0, 0.0]}})[0]
    assert v["verdict"] == "INCONCLUSIVE" and v["seeds_needed"] > 4


def test_a_store_reuses_runs(tmp_path):
    first = compare(comparison(1.0), workdir=tmp_path / "w", store=tmp_path / "store")
    again = compare(comparison(1.0), workdir=tmp_path / "w", store=tmp_path / "store")
    assert again.data["engines"]["scaled"]["cached"] == 4 and again.data["engines"]["reference"]["ok"] == 0
    assert again.verdicts == first.verdicts


def test_comparison_files_carry_budgets_and_claims(tmp_path):
    audit = tmp_path / "m.audit.yaml"
    audit.write_text(
        "claims:\n"
        "  - {id: M1, statement: grows, metric: last(cells), test: greater_than, value: 40}\n"
        "  - {id: M2, statement: trend, metric: last(cells), test: monotone_increasing, over: p, where: {p: [1, 2]}}\n"
    )
    f = tmp_path / "c.yaml"
    f.write_text(
        f"model: {MODEL}\nengines: [reference, scaled]\nseeds: 3\nclaims_from: m.audit.yaml\n"
        f"params: {json.dumps(SHORT)}\n"
        "metrics:\n  - {metric: last(cells), budget: {relative: 0.2}, note: a fifth of the colony}\n"
    )
    cmp = load_comparison(f, {"seeds": 4})  # one job: worker processes would not see the test engine
    assert cmp.seeds == 4 and [c.id for c in cmp.claims] == ["M1"]  # monotone claims need a sweep, not one point
    result = compare(cmp, workdir=tmp_path / "w")
    claim = result.data["claims"][0]
    assert claim["status"]["reference"] == claim["status"]["scaled"] and claim["same_conclusion"]
    assert result.data["comparison"]["metrics"][0]["note"] == "a fifth of the colony"
    for broken, match in (("engines: [reference]", "two different"), ("baseline: other", "baseline"),
                          ("seeds: 2", "at least 3"), ("extra: 1", "unknown")):  # fmt: skip
        g = tmp_path / "bad.yaml"
        g.write_text(f.read_text() + broken + "\n" if not broken.startswith("engines") else
                     f.read_text().replace("engines: [reference, scaled]", broken))  # fmt: skip
        with pytest.raises(CompareError, match=match):
            load_comparison(g)


def test_the_command_writes_json_and_exits_1_on_disagreement(tmp_path, capsys):
    f = tmp_path / "c.yaml"
    f.write_text(
        f"model: {MODEL}\nengines: [reference, scaled]\nseeds: 3\noptions: {{scaled: {{factor: 3}}}}\n"
        f"params: {json.dumps(SHORT)}\nmetrics:\n  - {{metric: last(cells), budget: 1}}\n"
    )
    assert main(["compare", str(f), "--out", str(tmp_path / "out"), "--workdir", str(tmp_path / "w")]) == 1
    printed = capsys.readouterr().out
    assert "DISAGREE" in printed and "scaled" in printed
    data = json.loads((tmp_path / "out" / "compare.json").read_text())
    assert data["summary"]["DISAGREE"] == 1
    assert main(["compare", str(MODEL), "--engines", "reference,scaled", "-q"]) == 2  # no --metric
