from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest
from toymodels import noisy_model

from abmfact.engines.legacy import engine_for_adapter
from abmfact.knobs import Knobs
from abmfact.records import FieldFrame, RunOutputs, RunRecord, RunRequest, cells_table
from abmfact.records.store import RunStore


def outputs() -> RunOutputs:
    cells = cells_table({"time": [0.0, 1.0], "id": [0, 0], "type": [0, 0], "x": [1.5, 2.5], "y": [0.0, 0.1],
                         "volume": [2.0, 2.0], "state": [0, 2]})  # fmt: skip
    frames = [FieldFrame(t, "o2", (0.5, 0.5), (1.0, 1.0), np.arange(6.0).reshape(2, 3) * (t + 1)) for t in (0.0, 1.0)]
    obs = {"time": np.array([0.0, 1.0]), "name": np.array(["n", "n"], dtype=object), "value": np.array([1.0, 0.0])}
    return RunOutputs(cells=cells, fields=frames, observables=obs, scalars={"n": [1.0, 0.0]}, type_names=["a"],
                      save_times=[0.0, 1.0])  # fmt: skip


def record(seed=1) -> RunRecord:
    return RunRecord(RunRequest("reference", "h", {"p": 1}, seed, {"dt_scale": 1.0}), wall_time=0.5, outputs=outputs())


def same(a: RunOutputs, b: RunOutputs) -> None:
    for c in a.cells:
        np.testing.assert_array_equal(a.cells[c], b.cells[c])
    assert [(f.time, f.substrate, f.origin, f.spacing) for f in a.fields] == [
        (f.time, f.substrate, f.origin, f.spacing) for f in b.fields
    ]
    for fa, fb in zip(a.fields, b.fields, strict=True):
        np.testing.assert_array_equal(fa.values, fb.values)
    assert a.observables["name"].tolist() == b.observables["name"].tolist()
    np.testing.assert_array_equal(a.observables["value"], b.observables["value"])
    assert a.scalars == b.scalars and a.type_names == b.type_names and a.save_times == b.save_times


@pytest.mark.parametrize("formats,index", [("basic", "jsonl"), ("columnar", "duckdb")])
def test_put_get_query_reindex(tmp_path, formats, index):
    if formats == "columnar":
        pytest.importorskip("pyarrow")
        pytest.importorskip("zarr")
        pytest.importorskip("duckdb")
    store = RunStore(tmp_path / "s", formats=formats, index=index)
    rec = record()
    assert store.get(rec.request) is None
    saved = store.put(rec)
    assert set(saved.output_paths) == {"cells", "fields", "observables"}
    assert all(len(h) == 64 for h in saved.output_sha256.values())
    hit = store.get(rec.request)
    assert hit is not None and hit.status == "cached" and hit.run_id == rec.run_id
    same(rec.outputs, hit.outputs)
    store.put(record(seed=2))
    rows = store.query(engine="reference")
    assert len(rows) == 2 and {r["seed"] for r in rows} == {1, 2}
    assert store.query(seed=2)[0]["run_id"] == record(seed=2).run_id
    assert store.reindex() == 2 and len(store.query()) == 2
    on_disk = json.loads((store.run_dir(rec.run_id) / "record.json").read_text())
    assert on_disk["run_id"] == rec.run_id and on_disk["status"] == "ok"


def test_failed_runs_are_not_served(tmp_path):
    store = RunStore(tmp_path, formats="basic", index="jsonl")
    rec = record()
    rec.status = "failed"
    store.put(rec)
    assert store.get(rec.request) is None


def test_log_is_copied(tmp_path):
    store = RunStore(tmp_path / "s", formats="basic", index="jsonl")
    log = tmp_path / "run.log"
    log.write_text("hello")
    rec = record()
    rec.log_path = str(log)
    saved = store.put(rec)
    assert "log" in saved.output_paths and Path(saved.log_path).read_text() == "hello"


def test_engine_run_once_uses_the_cache(tmp_path):
    store = RunStore(tmp_path, formats="basic", index="jsonl")
    engine = engine_for_adapter(noisy_model)
    first = engine.run_once(noisy_model, {"mu": 1.0}, 3, Knobs(), store=store)
    second = engine.run_once(noisy_model, {"mu": 1.0}, 3, Knobs(), store=store)
    assert first.status == "ok" and second.status == "cached"
    assert second.outputs.scalars == first.outputs.scalars
    assert engine.run_once(noisy_model, {"mu": 1.0}, 4, Knobs(), store=store).status == "ok"


def test_missing_extras_are_reported(tmp_path, monkeypatch):
    from abmfact.records import store as mod

    monkeypatch.setattr(mod, "_have", lambda name: False)
    with pytest.raises(RuntimeError, match="store"):
        RunStore(tmp_path, formats="columnar")
    with pytest.raises(RuntimeError, match="DuckDB"):
        RunStore(tmp_path, index="duckdb")
    s = RunStore(tmp_path)
    assert not s.columnar and s.index_kind == "jsonl"
