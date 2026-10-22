"""Adapters turn a model, in whatever framework it is written, into ``run(params, seed, knobs)``."""

from __future__ import annotations

import importlib
import json
import math
import os
import subprocess
import sys
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any

from .knobs import Knobs, Support, derived_seed

Outputs = dict[str, float | list[float]]
RunFn = Callable[[Mapping[str, Any], int, Knobs], Mapping[str, Any]]


class AdapterError(RuntimeError):
    """A model could not be built, run, or returned outputs ABMfact cannot read."""


def normalise_outputs(raw: Mapping[str, Any]) -> Outputs:
    """Coerce model outputs to floats and lists of floats, rejecting anything else."""
    if not isinstance(raw, Mapping):
        raise AdapterError(f"model must return a mapping of outputs, got {type(raw).__name__}")
    out: Outputs = {}
    for key, value in raw.items():
        if isinstance(value, int | float):
            out[str(key)] = float(value)
        elif hasattr(value, "tolist") and not isinstance(value, str):
            v = value.tolist()
            out[str(key)] = [float(x) for x in v] if isinstance(v, list) else float(v)
        elif isinstance(value, Sequence) and not isinstance(value, str):
            out[str(key)] = [float(x) for x in value]
        else:
            raise AdapterError(f"output {key!r} must be a number or a sequence of numbers")
    return out


class Adapter:
    """Base class. Subclasses implement :meth:`_run`.

    The base class implements ``rng_streams="independent"`` for models that do not manage their own
    streams, by remapping the seed through an independent derivation.
    """

    name: str = "model"
    support: Support = Support()

    def run(self, params: Mapping[str, Any], seed: int, knobs: Knobs) -> Outputs:
        if knobs.rng_streams == "independent" and not self.support.rng_streams:
            seed = derived_seed(seed)
            knobs = knobs.with_(rng_streams="default")
        return normalise_outputs(self._run(dict(params), int(seed), knobs))

    def _run(self, params: dict[str, Any], seed: int, knobs: Knobs) -> Mapping[str, Any]:
        raise NotImplementedError

    def describe(self) -> dict[str, Any]:
        return {"adapter": type(self).__name__, "name": self.name, "support": self.support.to_dict()}


class PythonAdapter(Adapter):
    """Wrap a plain Python callable ``fn(params, seed, knobs) -> dict``."""

    def __init__(self, fn: RunFn, supports: Mapping[str, Any] | Support | None = None, name: str | None = None):
        self.fn = fn
        self.support = supports if isinstance(supports, Support) else Support.from_mapping(supports)
        self.name = str(name or getattr(fn, "__qualname__", "python-model"))

    def _run(self, params: dict[str, Any], seed: int, knobs: Knobs) -> Mapping[str, Any]:
        return self.fn(params, seed, knobs)


class MesaAdapter(Adapter):
    """Run a Mesa 3 model class.

    ``outputs`` maps output names to either a callable taking the model, or the string
    ``"datacollector:<column>"`` for a model-level reporter series. ``knob_kwargs`` maps knob names
    (``update_order``, ``agent_order``, ``rng_streams``, ``grid_offset``, ``grid_rotation``,
    ``dt_scale``, ``pop_scale``, ``dx_scale``) to constructor keyword names. A knob is supported exactly when it is
    mapped.
    """

    def __init__(
        self,
        model_cls: Any,
        steps: int,
        outputs: Mapping[str, Any],
        knob_kwargs: Mapping[str, str] | None = None,
        update_orders: Sequence[str] = (),
        update_order_default: str | None = None,
        seed_kwarg: str = "rng",
        name: str | None = None,
    ):
        self.model_cls = model_cls
        self.steps = int(steps)
        self.outputs = dict(outputs)
        self.knob_kwargs = dict(knob_kwargs or {})
        self.seed_kwarg = seed_kwarg
        self.name = str(name or getattr(model_cls, "__name__", "mesa-model"))
        kk = self.knob_kwargs
        self.support = Support(
            update_order=tuple(update_orders) if "update_order" in kk else (),
            update_order_default=update_order_default or (update_orders[0] if update_orders else None),
            agent_order="agent_order" in kk,
            rng_streams="rng_streams" in kk,
            grid="grid_offset" in kk and "grid_rotation" in kk,
            dt_scale="dt_scale" in kk,
            pop_scale="pop_scale" in kk,
            dx_scale="dx_scale" in kk,
        )

    def _run(self, params: dict[str, Any], seed: int, knobs: Knobs) -> Mapping[str, Any]:
        kwargs = dict(params)
        kwargs[self.seed_kwarg] = seed
        kd = knobs.to_dict()
        for knob, kw in self.knob_kwargs.items():
            if knob == "agent_order":
                kwargs[kw] = knobs.agent_perm_seed if knobs.agent_order == "shuffled" else None
            elif knob == "update_order":
                kwargs[kw] = knobs.update_order or self.support.update_order_default
            else:
                kwargs[kw] = kd[knob]
        try:
            model = self.model_cls(**kwargs)
        except TypeError as exc:
            raise AdapterError(f"could not build {self.name}: {exc}") from exc
        n = max(1, round(self.steps / knobs.dt_scale)) if self.support.dt_scale else self.steps
        for _ in range(n):
            model.step()
        result: dict[str, Any] = {}
        for out_name, spec in self.outputs.items():
            if callable(spec):
                result[out_name] = spec(model)
            elif isinstance(spec, str) and spec.startswith("datacollector:"):
                col = spec.split(":", 1)[1]
                df = model.datacollector.get_model_vars_dataframe()
                result[out_name] = [float(x) for x in df[col].tolist()]
            else:
                raise AdapterError(f"output spec for {out_name!r} must be callable or 'datacollector:<col>'")
        return result


_NETLOGO_LINKS: dict[tuple[str, str | None], Any] = {}


def _default_netlogo_link(model_path: str, netlogo_home: str | None) -> Any:
    try:
        import pynetlogo
    except ImportError as exc:  # pragma: no cover - depends on optional install
        raise AdapterError("NetLogo support needs: pip install 'abmfact[netlogo]' and a NetLogo install") from exc
    link = pynetlogo.NetLogoLink(gui=False, netlogo_home=netlogo_home)  # pragma: no cover
    link.load_model(model_path)  # pragma: no cover
    return link  # pragma: no cover


def _nl_literal(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int | float):
        if isinstance(value, float) and not math.isfinite(value):
            raise AdapterError("NetLogo globals must be finite numbers")
        return repr(value)
    if isinstance(value, list | tuple):
        return "[" + " ".join(_nl_literal(v) for v in value) + "]"
    return json.dumps(str(value))


class NetLogoAdapter(Adapter):
    """Run a NetLogo model through pynetlogo.

    ``reporters`` maps output names to NetLogo reporter strings. A reporter whose name ends with
    ``[]`` is collected every tick as a series, otherwise once at the end. ``knob_globals`` maps knob
    names to NetLogo global variable names. One workspace is kept per process and model file.
    """

    def __init__(
        self,
        model_path: str | os.PathLike[str],
        reporters: Mapping[str, str],
        ticks: int,
        knob_globals: Mapping[str, str] | None = None,
        update_orders: Sequence[str] = (),
        update_order_default: str | None = None,
        setup: str = "setup",
        go: str = "go",
        netlogo_home: str | None = None,
        link_factory: Callable[[str, str | None], Any] | None = None,
        name: str | None = None,
    ):
        self.model_path = str(model_path)
        self.reporters = dict(reporters)
        self.ticks = int(ticks)
        self.knob_globals = dict(knob_globals or {})
        self.setup_cmd, self.go_cmd = setup, go
        self.netlogo_home = netlogo_home
        self.link_factory = link_factory or _default_netlogo_link
        self.name = name or Path(self.model_path).stem
        kg = self.knob_globals
        self.support = Support(
            update_order=tuple(update_orders) if "update_order" in kg else (),
            update_order_default=update_order_default or (update_orders[0] if update_orders else None),
            agent_order="agent_order" in kg,
            rng_streams="rng_streams" in kg,
            grid="grid_offset" in kg and "grid_rotation" in kg,
            dt_scale="dt_scale" in kg,
            pop_scale="pop_scale" in kg,
            dx_scale="dx_scale" in kg,
        )

    def _link(self) -> Any:
        key = (self.model_path, self.netlogo_home)
        if key not in _NETLOGO_LINKS:
            _NETLOGO_LINKS[key] = self.link_factory(self.model_path, self.netlogo_home)
        return _NETLOGO_LINKS[key]

    def _run(self, params: dict[str, Any], seed: int, knobs: Knobs) -> Mapping[str, Any]:
        link = self._link()
        for name, param in params.items():
            link.command(f"set {name} {_nl_literal(param)}")
        kd = knobs.to_dict()
        for knob, glob in self.knob_globals.items():
            if knob == "update_order":
                value: Any = knobs.update_order or self.support.update_order_default
            elif knob == "agent_order":
                value = knobs.agent_perm_seed if knobs.agent_order == "shuffled" else -1
            else:
                value = kd[knob]
            link.command(f"set {glob} {_nl_literal(value)}")
        link.command(f"random-seed {int(seed) % 2147483647}")
        link.command(self.setup_cmd)
        ticks = max(1, round(self.ticks / knobs.dt_scale)) if self.support.dt_scale else self.ticks
        series = {k: v for k, v in self.reporters.items() if k.endswith("[]")}
        finals = {k: v for k, v in self.reporters.items() if not k.endswith("[]")}
        result: dict[str, Any] = {}
        if series:
            collected: dict[str, list[float]] = {k: [] for k in series}
            for _ in range(ticks):
                link.command(self.go_cmd)
                for k, rep in series.items():
                    collected[k].append(float(link.report(rep)))
            for k, vals in collected.items():
                result[k[:-2]] = vals
        else:
            link.command(f"repeat {ticks} [ {self.go_cmd} ]")
        for k, rep in finals.items():
            result[k] = float(link.report(rep))
        return result


class CommandAdapter(Adapter):
    """Run any program. It reads ``{"params", "seed", "knobs"}`` as JSON on stdin and prints a JSON object."""

    def __init__(
        self,
        argv: Sequence[str],
        supports: Mapping[str, Any] | None = None,
        timeout: float = 600.0,
        cwd: str | None = None,
        name: str | None = None,
    ):
        if not argv:
            raise AdapterError("command adapter needs a non-empty argv")
        self.argv = [sys.executable if a == "{python}" else str(a) for a in argv]
        self.support = Support.from_mapping(supports)
        self.timeout = float(timeout)
        self.cwd = cwd
        self.name = name or Path(self.argv[-1]).name

    def _run(self, params: dict[str, Any], seed: int, knobs: Knobs) -> Mapping[str, Any]:
        payload = json.dumps({"params": params, "seed": seed, "knobs": knobs.to_dict()})
        try:
            proc = subprocess.run(  # noqa: S603 - argv comes from the user's own audit file
                self.argv,
                input=payload,
                capture_output=True,
                text=True,
                timeout=self.timeout,
                cwd=self.cwd,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            raise AdapterError(f"{self.name} timed out after {self.timeout:g}s (seed {seed})") from exc
        if proc.returncode != 0:
            tail = proc.stderr.strip().splitlines()[-5:]
            raise AdapterError(f"{self.name} exited with {proc.returncode} (seed {seed}): " + " | ".join(tail))
        try:
            data = json.loads(proc.stdout)
        except json.JSONDecodeError as exc:
            raise AdapterError(f"{self.name} did not print a JSON object (seed {seed})") from exc
        if not isinstance(data, dict):
            raise AdapterError(f"{self.name} must print a JSON object, got {type(data).__name__}")
        return data


def import_target(target: str) -> Any:
    """Import ``package.module:attribute``."""
    if ":" not in target:
        raise AdapterError(f"target must look like 'package.module:attribute', got {target!r}")
    mod_name, _, attr = target.partition(":")
    try:
        module = importlib.import_module(mod_name)
    except ImportError as exc:
        raise AdapterError(f"cannot import {mod_name!r}: {exc}") from exc
    obj: Any = module
    for part in attr.split("."):
        try:
            obj = getattr(obj, part)
        except AttributeError as exc:
            raise AdapterError(f"{mod_name!r} has no attribute {attr!r}") from exc
    return obj


def load_adapter(cfg: Mapping[str, Any], base_dir: Path | None = None) -> Adapter:
    """Build an adapter from the ``model:`` section of an audit file."""
    kind = str(cfg.get("adapter", "python"))
    base = base_dir or Path.cwd()
    if base_dir is not None and str(base_dir) not in sys.path:
        sys.path.insert(0, str(base_dir))
    if kind == "python":
        obj = import_target(str(cfg["target"]))
        if isinstance(obj, Adapter):
            return obj
        if callable(obj):
            return PythonAdapter(obj, supports=cfg.get("supports"), name=str(cfg["target"]))
        raise AdapterError(f"{cfg['target']} is neither an Adapter nor a callable")
    if kind == "mesa":
        uo = cfg.get("update_orders", [])
        return MesaAdapter(
            import_target(str(cfg["target"])),
            steps=int(cfg["steps"]),
            outputs={
                k: (import_target(v) if ":" in v and not v.startswith("datacollector:") else v)
                for k, v in dict(cfg["outputs"]).items()
            },
            knob_kwargs=cfg.get("knob_kwargs"),
            update_orders=uo,
            update_order_default=cfg.get("update_order_default"),
            seed_kwarg=str(cfg.get("seed_kwarg", "rng")),
            name=str(cfg["target"]),
        )
    if kind == "netlogo":
        path = Path(str(cfg["path"]))
        return NetLogoAdapter(
            path if path.is_absolute() else base / path,
            reporters=dict(cfg["reporters"]),
            ticks=int(cfg["ticks"]),
            knob_globals=cfg.get("knob_globals"),
            update_orders=cfg.get("update_orders", []),
            update_order_default=cfg.get("update_order_default"),
            setup=str(cfg.get("setup", "setup")),
            go=str(cfg.get("go", "go")),
            netlogo_home=cfg.get("netlogo_home"),
        )
    if kind == "command":
        return CommandAdapter(
            list(cfg["argv"]),
            supports=cfg.get("supports"),
            timeout=float(cfg.get("timeout", 600)),
            cwd=str(base),
        )
    raise AdapterError(f"unknown adapter {kind!r}; expected python, mesa, netlogo or command")
