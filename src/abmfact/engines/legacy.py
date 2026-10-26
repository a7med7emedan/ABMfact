"""The v0.1 adapters (Python, Mesa, NetLogo, command) as engines under the adapter contract.

The model is the adapter itself. ``prepare`` builds an in-memory run, the local backend calls the
adapter exactly as v0.1 did, and ``collect`` returns a record whose ``scalars`` are the v0.1 outputs,
so audits through this contract give the same results as before.
"""

from __future__ import annotations

import hashlib
import importlib.util
import os
from collections.abc import Mapping
from pathlib import Path
from typing import Any, ClassVar

from ..adapters import Adapter, CommandAdapter, MesaAdapter, NetLogoAdapter, PythonAdapter
from ..knobs import KNOB_NAMES, Knobs
from ..records import RunOutputs, RunRecord, RunRequest
from ..records.record import canonical
from .base import Availability, Capabilities, Engine, EngineError, PreparedRun, RunHandle


def adapter_hash(adapter: Adapter) -> str:
    """Identifies a v0.1 model by its description (target, name and declared support)."""
    return hashlib.sha256(canonical(adapter.describe()).encode()).hexdigest()


class AdapterEngine(Engine):
    """Runs any v0.1 :class:`~abmfact.adapters.Adapter`."""

    name: ClassVar[str] = "adapter"
    description: ClassVar[str] = "any v0.1 adapter"
    adapter_type: ClassVar[type[Adapter]] = Adapter
    native_formats: ClassVar[tuple[str, ...]] = ()

    def __init__(self, adapter: Adapter | None = None):
        self.adapter = adapter
        self._hash = adapter_hash(adapter) if adapter is not None else None

    def capabilities(self) -> Capabilities:
        return Capabilities(
            name=self.name,
            mechanics="defined by the model code",
            knobs=tuple(KNOB_NAMES),
            native_formats=self.native_formats,
            notes="the model declares which knobs it honours in its support section",
        )

    def refuse(self, model: Any, knobs: Knobs) -> list[str]:
        adapter = model if isinstance(model, Adapter) else self.adapter
        if not isinstance(adapter, Adapter):
            return [f"the {self.name} engine runs {self.description}, set up in an audit file"]
        return []

    def prepare(
        self, model: Any, params: Mapping[str, Any], knobs: Knobs, seed: int, workdir: Path | None = None
    ) -> PreparedRun:
        adapter = model if isinstance(model, Adapter) else self.adapter
        if not isinstance(adapter, Adapter):
            raise EngineError(f"{self.name} needs a v0.1 adapter as its model")
        model_hash = self._hash if adapter is self.adapter and self._hash else adapter_hash(adapter)
        p = dict(params)
        request = RunRequest(self.name, model_hash, p, int(seed), knobs.to_dict())

        def call() -> RunOutputs:
            return RunOutputs(scalars=adapter.run(p, seed, knobs))

        return PreparedRun(self.name, request, workdir=workdir, call=call)

    def collect(self, handle: RunHandle) -> RunRecord:
        return self.record(handle)


class PythonEngine(AdapterEngine):
    """Runs a v0.1 :class:`~abmfact.adapters.PythonAdapter`, a Python callable ``run(params, seed, knobs)``."""

    name = "python"
    description = "a Python callable run(params, seed, knobs)"
    adapter_type = PythonAdapter
    native_formats = ("python callable",)


class MesaEngine(AdapterEngine):
    """Runs a v0.1 :class:`~abmfact.adapters.MesaAdapter`, a Mesa 3 model class."""

    name = "mesa"
    description = "a Mesa 3 model class"
    adapter_type = MesaAdapter
    native_formats = ("mesa model class",)

    def availability(self) -> Availability:
        """Available when the ``mesa`` package is installed; the version is the installed Mesa version."""
        if importlib.util.find_spec("mesa") is None:
            return Availability(False, "needs: pip install 'abmfact[mesa]'")
        from importlib.metadata import version

        return Availability(True, "mesa is installed", version("mesa"))


class NetLogoEngine(AdapterEngine):
    """Runs a v0.1 :class:`~abmfact.adapters.NetLogoAdapter`, a NetLogo model driven through pynetlogo."""

    name = "netlogo"
    description = "a NetLogo model through pynetlogo"
    adapter_type = NetLogoAdapter
    native_formats = (".nlogo",)

    def availability(self) -> Availability:
        """Available when ``pynetlogo`` is installed and ``NETLOGO_HOME`` names an existing NetLogo folder."""
        if importlib.util.find_spec("pynetlogo") is None:
            return Availability(False, "needs: pip install 'abmfact[netlogo]' and a NetLogo install")
        home = os.environ.get("NETLOGO_HOME")
        if home and Path(home).exists():
            return Availability(True, f"pynetlogo with NETLOGO_HOME={home}")
        return Availability(False, "pynetlogo is installed but NETLOGO_HOME is not set to a NetLogo install")


class CommandEngine(AdapterEngine):
    """Runs a v0.1 :class:`~abmfact.adapters.CommandAdapter`, a program that reads JSON on stdin and prints JSON."""

    name = "command"
    description = "any program reading JSON on stdin and printing JSON"
    adapter_type = CommandAdapter
    native_formats = ("executable",)

    def availability(self) -> Availability:
        """Always available: the model names its own executable, which is not checked here."""
        return Availability(True, "runs any executable as a subprocess")


LEGACY_ENGINES: dict[type[Adapter], type[AdapterEngine]] = {
    PythonAdapter: PythonEngine,
    MesaAdapter: MesaEngine,
    NetLogoAdapter: NetLogoEngine,
    CommandAdapter: CommandEngine,
}


def engine_for_adapter(adapter: Adapter) -> AdapterEngine:
    """The engine that runs this adapter; unknown Adapter subclasses get the generic engine."""
    for cls in type(adapter).__mro__:
        if cls in LEGACY_ENGINES:
            return LEGACY_ENGINES[cls](adapter)
    return AdapterEngine(adapter)
