"""Implementation choices that an audit varies, and what a model declares it supports."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import asdict, dataclass, field, replace
from typing import Any

import numpy as np

#: Salt mixed into seeds when a model does not manage its own streams.
RNG_SALT = 0x0ABF_AC75

KNOB_NAMES = (
    "update_order",
    "agent_order",
    "rng_streams",
    "grid_offset",
    "grid_rotation",
    "dt_scale",
    "pop_scale",
    "dx_scale",
)


@dataclass(frozen=True)
class Knobs:
    """Settings passed to every model run.

    ``None`` for ``update_order`` means "use the model's own default". All other defaults are the
    identity: they leave a correct model unchanged. ``dx_scale`` multiplies the spatial resolution of
    a model's fields (the voxel size); the convergence check refines it together with ``dt_scale``.
    """

    update_order: str | None = None
    agent_order: str = "default"
    agent_perm_seed: int | None = None
    rng_streams: str = "default"
    grid_offset: tuple[float, float] = (0.0, 0.0)
    grid_rotation: int = 0
    dt_scale: float = 1.0
    pop_scale: float = 1.0
    dx_scale: float = 1.0

    def with_(self, **changes: Any) -> Knobs:
        """Return a copy with the given fields changed."""
        return replace(self, **changes)

    def to_dict(self) -> dict[str, Any]:
        """The knobs as a plain dict, with ``grid_offset`` as a list."""
        d = asdict(self)
        d["grid_offset"] = list(self.grid_offset)
        return d

    @classmethod
    def from_dict(cls, d: Mapping[str, Any]) -> Knobs:
        """Build knobs from a dict, such as one written by :meth:`to_dict`."""
        d = dict(d)
        if "grid_offset" in d and d["grid_offset"] is not None:
            d["grid_offset"] = tuple(float(x) for x in d["grid_offset"])
        return cls(**d)

    def changed(self) -> dict[str, Any]:
        """Only the knobs that differ from the defaults, for labels and reproducers."""
        base = Knobs().to_dict()
        return {k: v for k, v in self.to_dict().items() if v != base[k]}


@dataclass(frozen=True)
class Support:
    """What a model honours.

    ``update_order`` lists the activation orders the model implements and which one is its default.
    The other fields say whether the model reads the corresponding knob.
    """

    update_order: tuple[str, ...] = ()
    update_order_default: str | None = None
    agent_order: bool = False
    rng_streams: bool = False
    grid: bool = False
    dt_scale: bool = False
    pop_scale: bool = False
    dx_scale: bool = False
    extra: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_mapping(cls, m: Mapping[str, Any] | None) -> Support:
        """Build support from a model's ``supports`` mapping; an empty or missing mapping supports nothing.

        ``update_order`` is a list whose first entry is the default, or a mapping with ``values`` and
        ``default``. Unknown keys are kept in ``extra``.
        """
        if not m:
            return cls()
        m = dict(m)
        uo = m.pop("update_order", None)
        values: tuple[str, ...] = ()
        default: str | None = None
        if isinstance(uo, Mapping):
            values = tuple(str(v) for v in uo.get("values", ()))
            default = uo.get("default")
            if default is None and values:
                default = values[0]
        elif isinstance(uo, list | tuple):
            values = tuple(str(v) for v in uo)
            default = values[0] if values else None
        known = {k: bool(m.pop(k)) for k in list(m) if k in cls.__dataclass_fields__}
        return cls(update_order=values, update_order_default=default, extra=m, **known)

    def to_dict(self) -> dict[str, Any]:
        """The support as a plain dict for the audit record; ``dx_scale`` appears only when declared."""
        d: dict[str, Any] = {
            "update_order": {"values": list(self.update_order), "default": self.update_order_default},
            "agent_order": self.agent_order,
            "rng_streams": self.rng_streams,
            "grid": self.grid,
            "dt_scale": self.dt_scale,
            "pop_scale": self.pop_scale,
        }
        # Knobs added after v0.1 appear only when declared, so v0.1 audit records stay byte-identical.
        if self.dx_scale:
            d["dx_scale"] = True
        return d


def derived_seed(seed: int, salt: int = RNG_SALT) -> int:
    """An independent seed derived from ``seed``. Used when a model does not manage streams."""
    state = np.random.SeedSequence([int(seed) & 0xFFFF_FFFF, salt]).generate_state(1)[0]
    return int(state) & 0x7FFF_FFFF


def perm_seed(seed: int) -> int:
    """Seed for the agent relabelling permutation, independent of the model's own streams."""
    return derived_seed(seed, salt=RNG_SALT ^ 0x5EED)
