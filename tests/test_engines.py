from __future__ import annotations

import json
import sys
from types import SimpleNamespace

import pytest
from toymodels import noisy_model

from abmfact import cli
from abmfact.adapters import Adapter, CommandAdapter
from abmfact.engines import EngineError, get_engine, list_engines
from abmfact.engines.base import Capabilities, Engine, PreparedRun, changed_knobs
from abmfact.engines.legacy import AdapterEngine, CommandEngine, PythonEngine, engine_for_adapter
from abmfact.knobs import Knobs, derived_seed
from abmfact.records import RunRequest
from abmfact.runtime import LocalBackend


def test_registry_lists_builtins_and_rejects_unknown():
    engines = list_engines()
    assert {"python", "mesa", "netlogo", "command"} <= set(engines)
    assert isinstance(get_engine("python"), PythonEngine)
    with pytest.raises(EngineError, match="unknown engine"):
        get_engine("nope")


def test_entry_points_extend_but_never_replace(monkeypatch):
    from abmfact.engines import registry

    fake = [SimpleNamespace(name="toy", value="abmfact.engines.legacy:PythonEngine"),
            SimpleNamespace(name="python", value="abmfact.engines.legacy:CommandEngine")]  # fmt: skip
    monkeypatch.setattr(registry, "entry_points", lambda group: fake)
    targets = registry.engine_targets()
    assert targets["toy"].endswith("PythonEngine") and targets["python"].endswith("PythonEngine")
    with pytest.raises(EngineError, match="not an Engine"):
        registry._load("abmfact.knobs:Knobs")


def test_legacy_engine_gives_exactly_the_adapter_outputs():
    engine = engine_for_adapter(noisy_model)
    assert isinstance(engine, PythonEngine)
    knobs = Knobs(update_order="id")
    rec = engine.run_once(noisy_model, {"mu": 3.0}, 7, knobs)
    assert rec.outputs is not None and rec.outputs.scalars == noisy_model.run({"mu": 3.0}, 7, knobs)
    assert rec.status == "ok" and rec.request.seed == 7 and rec.request.engine == "python"
    assert rec.request.knobs["update_order"] == "id" and rec.wall_time is not None
    assert rec.request.model_hash == engine.prepare(noisy_model, {}, Knobs(), 1).request.model_hash
    assert isinstance(engine_for_adapter(Adapter()), AdapterEngine)
    assert AdapterEngine().refuse("not an adapter", Knobs()) and AdapterEngine().refuse(noisy_model, Knobs()) == []
    with pytest.raises(EngineError):
        AdapterEngine().prepare("nope", {}, Knobs(), 1)


class Fixed(Engine):
    name = "fixed"

    def capabilities(self):
        return Capabilities(name="fixed", mechanics="none", knobs=("dt_scale",))

    def prepare(self, model, params, knobs, seed, workdir=None):
        raise AssertionError("not reached")

    def collect(self, handle):
        raise AssertionError("not reached")


def test_refusal_and_seed_remapping():
    e = Fixed()
    assert changed_knobs(Knobs(grid_rotation=90, agent_order="shuffled", agent_perm_seed=3)) == [
        "agent_order",
        "grid_rotation",
    ]
    assert e.refuse(None, Knobs(dt_scale=0.5, rng_streams="independent")) == []
    assert e.refuse(None, Knobs(pop_scale=2.0)) == ["fixed does not honour the pop_scale knob"]
    with pytest.raises(EngineError, match="pop_scale"):
        e.run_once(None, knobs=Knobs(pop_scale=2.0))
    assert e.seed_for(5, Knobs(rng_streams="independent")) == derived_seed(5)
    assert e.seed_for(5, Knobs()) == 5


def test_local_backend_runs_commands_and_reports_failures(tmp_path):
    req = RunRequest("x", "h", {}, 0, {})
    ok = PreparedRun("x", req, workdir=tmp_path, argv=[sys.executable, "-c", "print('hello')"])
    h = LocalBackend().submit(ok)
    assert h.status == "done" and h.returncode == 0 and "hello" in h.log_path.read_text()
    bad = PreparedRun("x", req, workdir=tmp_path, argv=[sys.executable, "-c", "import sys; print('boom'); sys.exit(3)"])
    h = LocalBackend().submit(bad)
    assert h.status == "failed" and h.returncode == 3 and "boom" in h.error
    slow = PreparedRun("x", req, workdir=tmp_path, argv=[sys.executable, "-c", "import time; time.sleep(5)"])
    assert "timed out" in LocalBackend(timeout=0.2).submit(slow).error
    missing = PreparedRun("x", req, workdir=tmp_path, argv=[str(tmp_path / "no-such-binary")])
    assert LocalBackend().submit(missing).status == "failed"
    with pytest.raises(EngineError):
        LocalBackend().submit(PreparedRun("x", req))
    assert LocalBackend().availability().ok


def test_command_engine_record_is_failed_on_error(tmp_path):
    script = tmp_path / "m.py"
    script.write_text("import json,sys; json.load(sys.stdin); print(json.dumps({'x': 1}))")
    adapter = CommandAdapter([sys.executable, str(script)])
    rec = CommandEngine(adapter).run_once(adapter, {}, 1)
    assert rec.outputs.scalars == {"x": 1.0}


def test_engines_command(capsys):
    assert cli.main(["engines"]) == 0
    out = capsys.readouterr().out
    assert "python" in out and "knobs:" in out
    assert cli.main(["engines", "--json"]) == 0
    rows = json.loads(capsys.readouterr().out)
    assert {r["name"] for r in rows} >= {"python", "command"}
    assert all("capabilities" in r and "available" in r for r in rows)
