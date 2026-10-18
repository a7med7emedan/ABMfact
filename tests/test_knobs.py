from __future__ import annotations

from abmfact.knobs import Knobs, Support, derived_seed, perm_seed


def test_knobs_round_trip_and_changed():
    k = Knobs(update_order="id", grid_offset=(0.5, 0.25), dt_scale=0.5)
    again = Knobs.from_dict(k.to_dict())
    assert again == k
    assert k.changed() == {"update_order": "id", "grid_offset": [0.5, 0.25], "dt_scale": 0.5}
    assert Knobs().changed() == {}
    assert k.with_(dt_scale=1.0).changed() == {"update_order": "id", "grid_offset": [0.5, 0.25]}


def test_support_from_mapping_forms():
    s = Support.from_mapping({"update_order": {"values": ["random", "id"], "default": "id"}, "grid": True})
    assert s.update_order == ("random", "id")
    assert s.update_order_default == "id"
    assert s.grid and not s.agent_order
    s2 = Support.from_mapping({"update_order": ["a", "b"], "custom": 1})
    assert s2.update_order_default == "a"
    assert s2.extra == {"custom": 1}
    s3 = Support.from_mapping({"update_order": {"values": ["x", "y"]}})
    assert s3.update_order_default == "x"
    assert Support.from_mapping(None) == Support()
    assert Support().to_dict()["update_order"] == {"values": [], "default": None}


def test_derived_seeds_are_stable_and_distinct():
    assert derived_seed(1) == derived_seed(1)
    assert derived_seed(1) != derived_seed(2)
    assert derived_seed(1) != perm_seed(1)
    assert 0 <= derived_seed(2**40) < 2**31
