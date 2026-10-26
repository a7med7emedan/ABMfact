"""Every public module, class, function, method and property has a docstring, for the API reference."""

from __future__ import annotations

import importlib
import inspect
import pkgutil

import abmfact


def _modules() -> list[str]:
    names = [m.name for m in pkgutil.walk_packages(abmfact.__path__, "abmfact.")]
    return ["abmfact", *(n for n in names if n != "abmfact.__main__")]


def _class_doc(cls: type) -> str | None:
    doc = cls.__dict__.get("__doc__")
    # A dataclass without a docstring gets its signature as one.
    if not doc or doc.startswith(f"{cls.__name__}("):
        return None
    return doc


def undocumented() -> list[str]:
    missing = []
    for name in _modules():
        mod = importlib.import_module(name)
        if not mod.__doc__:
            missing.append(name)
        for attr, obj in vars(mod).items():
            if attr.startswith("_") or getattr(obj, "__module__", None) != name:
                continue
            if inspect.isfunction(obj):
                if not obj.__doc__:
                    missing.append(f"{name}.{attr}")
            elif inspect.isclass(obj):
                if not _class_doc(obj):
                    missing.append(f"{name}.{attr}")
                for member, value in vars(obj).items():
                    if member.startswith("_"):
                        continue
                    func = value.fget if isinstance(value, property) else getattr(value, "__func__", value)
                    if not inspect.isfunction(func):
                        continue
                    # Overrides inherit the documented contract of the base class.
                    doc = inspect.getdoc(value) if isinstance(value, property) else inspect.getdoc(getattr(obj, member))
                    if not doc:
                        missing.append(f"{name}.{attr}.{member}")
    return missing


def test_public_api_is_documented():
    assert undocumented() == []

