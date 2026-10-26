from __future__ import annotations

import json
from pathlib import Path

import pytest

from abmfact import cli
from abmfact.engines.base import Availability
from abmfact.engines.legacy import PythonEngine
from abmfact.engines.physicell import PhysiCellEngine
from abmfact.engines.recommend import CONVERGENCE, Recommendation, recommend
from abmfact.engines.reference import ReferenceEngine
from abmfact.ir import apply_params, load_model

ROOT = Path(__file__).parent.parent
MONOLAYER = ROOT / "examples" / "ir" / "monolayer.yaml"
TEMPLATE = ROOT / "tests" / "fixtures" / "physicell" / "template"


class InstalledPhysiCell(PhysiCellEngine):
    """PhysiCell with availability fixed, so the ranking does not depend on this machine."""

    def __init__(self, ok: bool = True, **kw):
        super().__init__(**kw)
        self.ok = ok

    def availability(self) -> Availability:
        return Availability(self.ok, "fixed for the test")


def engines(physicell_ok: bool = True):
    return {"reference": ReferenceEngine(), "physicell": InstalledPhysiCell(physicell_ok), "python": PythonEngine()}


def by_name(recs: list[Recommendation]) -> dict[str, Recommendation]:
    return {r.engine: r for r in recs}


def test_ir_model_ranks_engines_that_run_it_first():
    recs = recommend(MONOLAYER, engines())
    assert [r.engine for r in recs] == ["reference", "physicell", "python"]
    ref, pc, py = recs
    assert ref.runs and ref.route == "reads ABM-IR" and ref.tier == "T0" and ref.fidelity == {}
    assert pc.runs and pc.route == "translated" and pc.tier == "T1"
    assert pc.fidelity["approximated"] == len(pc.losses) > 0
    assert any(path == "space" for path, _, _ in pc.losses)
    assert not py.runs and "audit file" in py.reasons[0]


def test_checks_come_from_what_each_engine_honours():
    rec = by_name(recommend(MONOLAYER, engines()))
    assert rec["reference"].checks[:2] == ["determinism", "update_order"]
    assert CONVERGENCE in rec["reference"].checks and "grid" in rec["reference"].checks
    assert rec["physicell"].checks == ["determinism", "rng_streams", "time_step", CONVERGENCE]
    assert rec["python"].checks == []


def test_an_installed_engine_outranks_one_that_is_not():
    recs = recommend(TEMPLATE, {"a": InstalledPhysiCell(False), "b": InstalledPhysiCell(True)})
    assert [r.engine for r in recs] == ["b", "a"]
    assert all(r.runs and r.route == "as given" and r.tier == "T0" for r in recs)


def test_less_translation_loss_ranks_higher():
    lossless = Recommendation("x", "", runs=True, available=True, fidelity={"exact": 3})
    lossy = Recommendation("y", "", runs=True, available=True, fidelity={"approximated": 1})
    dropped = Recommendation("z", "", runs=True, available=True, fidelity={"approximated": 0, "dropped": 1})
    assert sorted([dropped, lossy, lossless], key=Recommendation.rank_key) == [lossless, lossy, dropped]


def test_native_project_runs_only_in_its_own_engine():
    rec = by_name(recommend(TEMPLATE, engines()))
    assert rec["physicell"].runs and rec["physicell"].route == "as given"
    assert not rec["reference"].runs and "is a folder" in rec["reference"].reasons[0]


def test_refusals_carry_the_engines_reason(tmp_path):
    model = apply_params(load_model(MONOLAYER), {"space.dimensions": 3})
    rec = by_name(recommend(model, engines()))
    assert not rec["reference"].runs and any("2D only" in r for r in rec["reference"].reasons)
    assert rec["physicell"].runs


def test_unreadable_model_is_a_reason_not_an_error(tmp_path):
    bad = tmp_path / "bad.yaml"
    bad.write_text("schema_version: '0.1'\nname: x\n")
    recs = recommend(bad, engines())
    assert not any(r.runs for r in recs)
    assert all(r.reasons for r in recs)


def test_cli_table_and_json(capsys, monkeypatch):
    monkeypatch.setattr("abmfact.engines.recommend.list_engines", lambda: {"reference": ReferenceEngine,
                                                                           "python": PythonEngine})  # fmt: skip
    assert cli.main(["recommend", str(MONOLAYER)]) == 0
    out = capsys.readouterr().out
    assert "1. reference" in out and "testable checks:" in out and "cannot run it:" in out
    assert cli.main(["recommend", str(MONOLAYER), "--json"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert [d["engine"] for d in data] == ["reference", "python"] and data[0]["runs"] is True


@pytest.mark.parametrize("flag", [[], ["--details"]])
def test_cli_lists_losses_only_with_details(capsys, monkeypatch, flag):
    monkeypatch.setattr("abmfact.engines.recommend.list_engines", lambda: {"physicell": InstalledPhysiCell})
    assert cli.main(["recommend", str(MONOLAYER), *flag]) == 0
    out = capsys.readouterr().out
    assert ("approximated space" in out) == bool(flag)
