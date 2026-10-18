from __future__ import annotations

import pytest

pytest.importorskip("mesa")

from abmfact import MesaAdapter, load_audit, run_audit
from abmfact.adapters import AdapterError, load_adapter
from abmfact.knobs import Knobs


def test_mesa_example_audit_is_robust(examples):
    audit = load_audit(examples / "mesa_sir" / "audit.yaml", {"seeds": 20})
    res = run_audit(audit)
    m = res.matrix()
    assert m["M1"]["update_order"] == "ROBUST"
    assert m["M1"]["agent_order"] == "ROBUST"
    assert m["M1"]["grid"] == "NOT_TESTABLE"
    assert not res.any_flagged


def test_mesa_adapter_details(examples):
    import sys

    sys.path.insert(0, str(examples / "mesa_sir"))
    import mesa_sir

    a = MesaAdapter(
        mesa_sir.SIRModel,
        steps=5,
        outputs={"ar": mesa_sir.attack_rate, "prev": "datacollector:Infected"},
        knob_kwargs={"update_order": "activation"},
        update_orders=["random", "id"],
    )
    out = a.run({"side": 7}, 1, Knobs(update_order="id"))
    assert len(out["prev"]) == 5 and 0 < out["ar"] <= 1
    bad = MesaAdapter(mesa_sir.SIRModel, steps=1, outputs={"x": 3})
    with pytest.raises(AdapterError, match="output spec"):
        bad.run({}, 1, Knobs())
    with pytest.raises(AdapterError, match="could not build"):
        MesaAdapter(mesa_sir.SIRModel, steps=1, outputs={}).run({"nope": 1}, 1, Knobs())
    cfg = {
        "adapter": "mesa",
        "target": "mesa_sir:SIRModel",
        "steps": 3,
        "outputs": {"ar": "mesa_sir:attack_rate", "p": "datacollector:Infected"},
    }
    assert load_adapter(cfg).run({"side": 5}, 2, Knobs())["ar"] > 0
