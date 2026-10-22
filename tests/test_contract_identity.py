"""Audits run through the engine contract give exactly what the v0.1 adapters give."""

from __future__ import annotations

import json

from abmfact import zoo
from abmfact.audit import execute
from abmfact.config import parse_audit
from abmfact.knobs import Knobs
from abmfact.runtime.local import LocalBackend


def _jobs(seeds: int = 4):
    params = dict(zoo.DEFAULT_PARAMS, T=20)
    knobs = [Knobs(), Knobs(update_order="synchronous"), Knobs(dt_scale=0.5), Knobs(grid_rotation=90)]
    return [(f"c{i}", json.dumps(params), params, s, k.to_dict()) for s in range(seeds) for i, k in enumerate(knobs)]


def test_every_job_matches_the_adapter_called_directly():
    adapter = zoo.MODELS["stale_state"]
    jobs = _jobs()
    serial = execute(adapter, jobs, workers=1)
    pooled = execute(adapter, jobs, workers=2)
    assert len(serial) == len(jobs)
    for label, pkey, params, seed, knobs in jobs:
        direct = adapter.run(params, seed, Knobs.from_dict(knobs))
        assert serial[(label, pkey, seed)] == pooled[(label, pkey, seed)] == direct


def test_audit_results_are_unchanged_by_the_contract():
    doc = zoo.audit_document("boundary", seeds=6)
    doc["params"]["T"] = 20
    a = parse_audit(doc)
    from abmfact.audit import run_audit

    first = run_audit(a).to_json(include_timestamp=False)
    assert run_audit(a).to_json(include_timestamp=False) == first
    data = json.loads(first)
    assert data["audit"]["model"]["adapter"] == "PythonAdapter"
    assert "dx_scale" not in data["audit"]["model"]["support"]


def test_local_map_keeps_order_in_a_pool():
    assert LocalBackend(workers=2).map(abs, [-3, 2, -1, 0]) == [3, 2, 1, 0]
