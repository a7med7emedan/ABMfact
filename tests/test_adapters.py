from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pytest

from abmfact import adapters as ad
from abmfact.adapters import (
    AdapterError,
    CommandAdapter,
    NetLogoAdapter,
    PythonAdapter,
    import_target,
    load_adapter,
    normalise_outputs,
)
from abmfact.knobs import Knobs, derived_seed


def test_normalise_outputs():
    out = normalise_outputs({"a": 1, "b": True, "c": np.float64(2.5), "d": np.arange(3), "e": (1, 2), "f": np.int64(4)})
    assert out == {"a": 1.0, "b": 1.0, "c": 2.5, "d": [0.0, 1.0, 2.0], "e": [1.0, 2.0], "f": 4.0}
    with pytest.raises(AdapterError):
        normalise_outputs({"a": "text"})
    with pytest.raises(AdapterError):
        normalise_outputs([1, 2])  # type: ignore[arg-type]


def test_python_adapter_remaps_seed_when_streams_undeclared():
    seen = []

    def fn(params, seed, knobs):
        seen.append((seed, knobs.rng_streams))
        return {"x": seed}

    plain = PythonAdapter(fn)
    plain.run({}, 5, Knobs(rng_streams="independent"))
    assert seen[-1] == (derived_seed(5), "default")
    managed = PythonAdapter(fn, supports={"rng_streams": True})
    managed.run({}, 5, Knobs(rng_streams="independent"))
    assert seen[-1] == (5, "independent")
    assert managed.describe()["support"]["rng_streams"] is True
    assert ad.Adapter().describe()["adapter"] == "Adapter"
    with pytest.raises(NotImplementedError):
        ad.Adapter()._run({}, 1, Knobs())


SCRIPT = """
import json, sys
job = json.load(sys.stdin)
mode = job["params"].get("mode", "ok")
if mode == "fail":
    sys.stderr.write("boom\\n"); sys.exit(3)
if mode == "garbage":
    print("not json"); sys.exit(0)
if mode == "list":
    print("[1, 2]"); sys.exit(0)
if mode == "sleep":
    import time; time.sleep(5)
if mode == "text":
    print(json.dumps({"x": "t"})); sys.exit(0)
print(json.dumps({"seed": job["seed"], "order_is_b": float(job["knobs"]["update_order"] == "b")}))
"""


@pytest.fixture
def script(tmp_path: Path) -> Path:
    p = tmp_path / "model.py"
    p.write_text(SCRIPT)
    return p


def test_command_adapter_round_trip(script):
    a = CommandAdapter(["{python}", str(script)], supports={"update_order": ["a", "b"]})
    assert a.argv[0] == sys.executable
    assert a.run({}, 7, Knobs(update_order="b")) == {"seed": 7.0, "order_is_b": 1.0}


def test_command_adapter_outputs(script):
    a = CommandAdapter([sys.executable, str(script)])
    with pytest.raises(AdapterError, match="must be a number"):
        a.run({"mode": "text"}, 7, Knobs())
    ok = CommandAdapter(
        [sys.executable, "-c", "import json,sys; j=json.load(sys.stdin); print(json.dumps({'s': j['seed']}))"]
    )
    assert ok.run({}, 3, Knobs()) == {"s": 3.0}


@pytest.mark.parametrize(
    ("mode", "match"), [("fail", "exited with 3"), ("garbage", "JSON object"), ("list", "JSON object")]
)
def test_command_adapter_errors(script, mode, match):
    a = CommandAdapter([sys.executable, str(script)])
    with pytest.raises(AdapterError, match=match):
        a.run({"mode": mode}, 1, Knobs())


def test_command_adapter_timeout(script):
    a = CommandAdapter([sys.executable, str(script)], timeout=0.5)
    with pytest.raises(AdapterError, match="timed out"):
        a.run({"mode": "sleep"}, 1, Knobs())
    with pytest.raises(AdapterError):
        CommandAdapter([])


class FakeLink:
    def __init__(self, path, home):
        self.path, self.home = path, home
        self.commands: list[str] = []
        self.ticks = 0

    def command(self, cmd: str) -> None:
        self.commands.append(cmd)
        if cmd == "go":
            self.ticks += 1
        if cmd.startswith("repeat"):
            self.ticks += int(cmd.split()[1])

    def report(self, rep: str) -> float:
        return float(self.ticks) if rep == "ticks" else 0.5


def test_netlogo_adapter_with_fake_link(tmp_path):
    links: list[FakeLink] = []

    def factory(path, home):
        links.append(FakeLink(path, home))
        return links[-1]

    a = NetLogoAdapter(
        tmp_path / "m.nlogo",
        reporters={"share": "count turtles / 100", "ticks[]": "ticks"},
        ticks=4,
        knob_globals={"update_order": "update-order", "agent_order": "perm-seed", "dt_scale": "dt-scale"},
        update_orders=["random", "id"],
        link_factory=factory,
    )
    assert a.support.update_order == ("random", "id") and a.support.dt_scale and not a.support.grid
    out = a.run({"beta": 0.4, "flag": True, "name": "x", "xs": [1, 2]}, 12, Knobs(update_order="id", dt_scale=0.5))
    assert out["ticks"] == [1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0, 8.0]
    assert out["share"] == 0.5
    cmds = links[0].commands
    assert "set beta 0.4" in cmds and "set flag true" in cmds and 'set name "x"' in cmds and "set xs [1 2]" in cmds
    assert 'set update-order "id"' in cmds and "set perm-seed -1" in cmds and "random-seed 12" in cmds
    # Workspace is reused.
    a.run({}, 1, Knobs(agent_order="shuffled", agent_perm_seed=9))
    assert len(links) == 1 and "set perm-seed 9" in links[0].commands

    finals_only = NetLogoAdapter(tmp_path / "n.nlogo", reporters={"t": "ticks"}, ticks=3, link_factory=factory)
    assert finals_only.run({}, 1, Knobs())["t"] == 3.0
    with pytest.raises(AdapterError):
        ad._nl_literal(float("nan"))


def test_import_target_and_errors():
    assert import_target("toymodels:noisy_model").name == "noisy"
    assert import_target("abmfact.knobs:Knobs.from_dict")
    for bad in ("abmfact.knobs", "nope_pkg_xyz:a", "abmfact.knobs:missing"):
        with pytest.raises(AdapterError):
            import_target(bad)


def test_load_adapter_variants(tmp_path):
    assert load_adapter({"adapter": "python", "target": "toymodels:noisy_model"}).name == "noisy"
    fn = load_adapter({"adapter": "python", "target": "toymodels:noisy", "supports": {"grid": True}})
    assert isinstance(fn, PythonAdapter) and fn.support.grid
    with pytest.raises(AdapterError, match="neither"):
        load_adapter({"adapter": "python", "target": "abmfact.knobs:RNG_SALT"})
    cmd = load_adapter({"adapter": "command", "argv": ["{python}", "x.py"]}, base_dir=tmp_path)
    assert isinstance(cmd, CommandAdapter) and cmd.cwd == str(tmp_path)
    nl = load_adapter(
        {"adapter": "netlogo", "path": "m.nlogo", "reporters": {"a": "ticks"}, "ticks": 5}, base_dir=tmp_path
    )
    assert isinstance(nl, NetLogoAdapter) and nl.model_path == str(tmp_path / "m.nlogo")
    with pytest.raises(AdapterError, match="unknown adapter"):
        load_adapter({"adapter": "julia"})
