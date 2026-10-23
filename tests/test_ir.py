from __future__ import annotations

import copy
import json

import pytest
import yaml

from abmfact.ir import IRError, apply_params, load_model, model_from_dict, model_hash, model_to_dict, save_model
from abmfact.ir.vocabulary import is_behaviour, is_signal

DOC = {
    "schema_version": "0.1",
    "name": "spheroid",
    "space": {
        "bounds": {"x_min": -200, "x_max": 200, "y_min": -200, "y_max": 200},
        "voxel": {"dx": 20, "dy": 20},
    },
    "substrates": [
        {
            "name": "oxygen",
            "diffusion_coefficient": 1e5,
            "decay_rate": 0.1,
            "initial_value": 38,
            "boundary": {"kind": "dirichlet", "value": 38},
        },
    ],
    "cell_types": [
        {
            "name": "tumour",
            "cycle": {"model": "live", "rates": [0.0012]},
            "secretion": [{"substrate": "oxygen", "uptake_rate": 10}],
        },
    ],
    "rules": [
        {
            "cell_type": "tumour",
            "signal": "oxygen",
            "direction": "decreases",
            "behavior": "necrosis",
            "saturation_value": 0,
            "half_max": 3.75,
            "hill_power": 8,
        },
        {
            "cell_type": "tumour",
            "signal": "pressure",
            "direction": "decreases",
            "behaviour": "cycle entry",
            "saturation_value": 0,
            "half_max": 1,
            "hill_power": 4,
        },
    ],
    "initial_conditions": {"placements": [{"kind": "disc", "cell_type": "tumour", "count": 20, "radius": 50}]},
    "observables": [
        {"name": "n", "kind": "live_count"},
        {"name": "o2", "kind": "substrate_mean", "substrate": "oxygen"},
    ],
    "experiment": {"max_time": 600},
}


def doc(**changes):
    d = copy.deepcopy(DOC)
    d.update(changes)
    return d


def test_round_trip_yaml_and_json(tmp_path):
    m = model_from_dict(DOC)
    assert m.rules[0].behaviour == "necrosis"
    for name in ("m.yaml", "m.json"):
        path = save_model(m, tmp_path / name)
        again = load_model(path)
        assert model_to_dict(again, full=True) == model_to_dict(m, full=True)
        assert again.source_dir == tmp_path.resolve()
    assert next(iter(model_to_dict(m))) == "schema_version"
    assert "rules" in yaml.safe_load((tmp_path / "m.yaml").read_text())


def test_errors_name_paths():
    bad = doc()
    bad["cell_types"][0]["cycle"]["rates"] = [-1]
    with pytest.raises(IRError) as e:
        model_from_dict(bad, source="x.yaml")
    assert ("cell_types[tumour].cycle.rates[0]", "Input should be greater than or equal to 0") in e.value.errors
    assert str(e.value).startswith("x.yaml: invalid ABM-IR model")

    bad = doc()
    bad["cell_types"][0]["colour"] = "red"
    with pytest.raises(IRError) as e:
        model_from_dict(bad)
    assert e.value.errors[0][0] == "cell_types[tumour].colour"


def test_cross_references_are_checked():
    bad = doc()
    bad["rules"][0]["signal"] = "glucose"
    bad["rules"][1]["behaviour"] = "fly"
    bad["observables"][1]["substrate"] = "glucose"
    bad["cell_types"][0]["secretion"][0]["substrate"] = "glucose"
    with pytest.raises(IRError) as e:
        model_from_dict(bad)
    paths = [p for p, _ in e.value.errors]
    assert "rules[0].signal" in paths and "rules[1].behaviour" in paths
    assert "observables[o2].substrate" in paths and "cell_types[tumour].secretion[0].substrate" in paths


@pytest.mark.parametrize(
    "change",
    [
        {"schema_version": "9"},
        {"cell_types": []},
        {"space": {"bounds": {"x_min": 1, "x_max": 0, "y_min": 0, "y_max": 1}, "voxel": {"dx": 1, "dy": 1}}},
        {"initial_conditions": {"placements": [{"kind": "disc", "cell_type": "tumour", "count": 3}]}},
        {"events": [{"time": 10, "until": 5, "action": "set_substrate", "target": "oxygen", "value": 0}]},
        {"observables": [{"name": "f", "kind": "type_fraction"}]},
    ],
)
def test_invalid_documents(change):
    with pytest.raises(IRError):
        model_from_dict(doc(**change))


def test_not_a_mapping_and_bad_file(tmp_path):
    with pytest.raises(IRError):
        model_from_dict([1, 2])
    p = tmp_path / "bad.yaml"
    p.write_text("a: [")
    with pytest.raises(IRError, match="cannot parse"):
        load_model(p)


def test_apply_params_by_name_index_and_open_keys():
    m = model_from_dict(DOC)
    m2 = apply_params(
        m,
        {
            "cell_types[tumour].cycle.rates[0]": 0.002,
            "substrates[oxygen].decay_rate": 0.2,
            "cell_types[tumour].custom.drug_sensitivity": 1.5,
            "rules[1].half_max": 2.0,
            "experiment.max_time": 60,
        },
    )
    assert m2.cell_type("tumour").cycle.rates == [0.002]
    assert m2.substrate("oxygen").decay_rate == 0.2
    assert m2.cell_type("tumour").custom == {"drug_sensitivity": 1.5}
    assert m2.rules[1].half_max == 2.0 and m.rules[1].half_max == 1
    assert apply_params(m, {}) is m
    for bad in (
        {"cell_types[nope].volume": 1},
        {"experiment.nope": 1},
        {"cell_types[tumour].volume": -1},
        {"rules[9].half_max": 1},
        {"experiment..max_time": 1},
        {"experiment.max_time!": 1},
    ):
        with pytest.raises(IRError):
            apply_params(m, bad)


def test_hash_ignores_description_and_key_order(tmp_path):
    m = model_from_dict(DOC)
    shuffled = json.loads(json.dumps(DOC, sort_keys=True))
    shuffled["description"] = "same model, new words"
    shuffled["provenance"] = {"notes": "edited"}
    assert model_hash(model_from_dict(shuffled)) == model_hash(m)
    assert model_hash(apply_params(m, {"experiment.max_time": 61})) != model_hash(m)


def test_hash_includes_referenced_files(tmp_path):
    d = doc(intracellular=[{"cell_type": "tumour", "format": "maboss", "files": {"bnd": "net.bnd"}}])
    (tmp_path / "net.bnd").write_text("node A {}")
    path = tmp_path / "m.yaml"
    path.write_text(yaml.safe_dump(d))
    h1 = model_hash(load_model(path))
    (tmp_path / "net.bnd").write_text("node B {}")
    assert model_hash(load_model(path)) != h1


def test_derived_quantities():
    m = model_from_dict(DOC)
    ct = m.cell_type("tumour")
    assert ct.radius == pytest.approx(8.4127, rel=1e-3)
    assert ct.cycle.division_rate == pytest.approx(0.0012)
    two = model_from_dict(
        doc(
            cell_types=[{"name": "a", "cycle": {"model": "ki67_basic", "durations": [60, 60]}}],
            rules=[],
            initial_conditions={},
            observables=[],
        )
    )
    assert two.cell_types[0].cycle.division_rate == pytest.approx(1 / 120)
    with pytest.raises(KeyError):
        m.substrate("glucose")
    b = m.substrate("oxygen").boundary
    assert b.value_at("x_min") == 38


def test_vocabulary_patterns():
    subs, types = ["oxygen"], ["tumour", "T cell"]
    for s in (
        "oxygen",
        "intracellular oxygen",
        "oxygen gradient",
        "contact with T cell",
        "contact with cell type 1",
        "pressure",
        "custom:drug",
        "dead",
    ):
        assert is_signal(s, subs, types), s
    for b in (
        "oxygen secretion",
        "chemotactic response to oxygen",
        "attack tumour",
        "cycle entry",
        "exit from cycle phase 2",
        "transform to cell type 0",
    ):
        assert is_behaviour(b, subs, types), b
    assert not is_signal("glucose", subs, types)
    assert not is_behaviour("attack macrophage", subs, types)


def _to_crlf(path):
    path.write_bytes(path.read_bytes().replace(b"\n", b"\r\n"))


def test_hash_is_the_same_for_lf_and_crlf_copies(tmp_path):
    d = doc(
        intracellular=[{"cell_type": "tumour", "format": "maboss", "files": {"bnd": "net.bnd", "cfg": "net.cfg"}}],
        initial_conditions={"placements": [{"kind": "positions_csv", "path": "cells.csv"}]},
    )
    hashes = []
    for name in ("lf", "crlf"):
        folder = tmp_path / name
        folder.mkdir()
        # Exact bytes: write_text would already turn LF into CRLF on Windows.
        (folder / "net.bnd").write_bytes(b"Node A {\n  logic = A;\n}\n")
        (folder / "net.cfg").write_bytes(b"$time_scale = 1;\n")
        (folder / "cells.csv").write_bytes(b"x,y,z,type\n0,0,0,tumour\n")
        (folder / "model.yaml").write_bytes(yaml.safe_dump(d).encode())
        if name == "crlf":
            for f in folder.iterdir():
                _to_crlf(f)
            assert b"\r\n" in (folder / "model.yaml").read_bytes()
        else:
            assert b"\r\n" not in (folder / "model.yaml").read_bytes()
        hashes.append(model_hash(load_model(folder / "model.yaml")))
    assert hashes[0] == hashes[1]


def test_digest_normalises_text_only(tmp_path):
    from abmfact.digest import content_digest

    text_lf, text_crlf = tmp_path / "a.csv", tmp_path / "b.csv"
    text_lf.write_bytes(b"x,y\n1,2\n")
    text_crlf.write_bytes(b"x,y\r\n1,2\r\n")
    assert content_digest(text_lf) == content_digest(text_crlf)
    make_lf, make_crlf = tmp_path / "m1" / "Makefile", tmp_path / "m2" / "Makefile"
    for p, data in ((make_lf, b"all:\n\tcc\n"), (make_crlf, b"all:\r\n\tcc\r\n")):
        p.parent.mkdir()
        p.write_bytes(data)
    assert content_digest(make_lf) == content_digest(make_crlf)
    bin_a, bin_b = tmp_path / "a.mat", tmp_path / "b.mat"
    bin_a.write_bytes(b"\x00\r\n\x01")
    bin_b.write_bytes(b"\x00\n\x01")
    assert content_digest(bin_a) != content_digest(bin_b)
