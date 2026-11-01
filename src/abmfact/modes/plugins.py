"""Calibration plug-in slots for tools from the agent-based modelling community.

* ``uq-physicell``: UQ-PhysiCell (BSD-3; Rocha et al.) adds safeguards to pyABC's ABC-SMC, checking particle weights
  and transition kernels between generations, and a kernel fitted on standardised parameters so parameters on
  very different scales are all perturbed. This slot runs ABMfact's ABC-SMC with those two pieces, through any
  engine. Install with ``pip install uq-physicell``.
* ``fitmulticell``: FitMultiCell (BSD-3; Alamoudi et al.) runs pyABC on Morpheus models through its own model class,
  which calls a native Morpheus executable. ABMfact does not bridge that class yet, so this slot reports what is
  installed and points to ``abc-smc``, which runs the same pyABC algorithm through ABMfact's engines, Morpheus in
  its container included.

Further tools register a calibrator under the entry point group ``abmfact.calibrators``: a callable taking the
calibration :class:`~abmfact.modes.calibrate.Problem` and the file's ``plugin`` settings, and returning a result
with ``best``, ``best_distance``, ``interval``, ``interval_meaning``, ``diagnostics`` and ``warnings``.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from importlib import metadata
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .calibrate import Problem

SLOTS = {"uq-physicell": "uq-physicell", "fitmulticell": "fitmulticell"}


def installed(distribution: str) -> str | None:
    """The installed version of ``distribution``, or None."""
    try:
        return metadata.version(distribution)
    except metadata.PackageNotFoundError:
        return None


def status() -> list[dict[str, Any]]:
    """Each plug-in slot, whether its tool is installed here, and what the slot does."""
    return [
        {
            "method": "uq-physicell",
            "package": "uq-physicell",
            "version": installed("uq-physicell"),
            "does": "ABC-SMC with UQ-PhysiCell's weight and kernel safeguards and its standardised kernel, any engine",
        },
        {
            "method": "fitmulticell",
            "package": "fitmulticell",
            "version": installed("fitmulticell"),
            "does": "not bridged yet: FitMultiCell drives a native Morpheus executable; use abc-smc",
        },
    ]


def uq_physicell(problem: Problem, settings: Mapping[str, Any]) -> dict[str, Any]:
    """ABC-SMC with UQ-PhysiCell's ``CheckedABCSMC`` and ``ScaledMultivariateNormalTransition``.

    ``settings`` are those of ``abc_smc``, plus ``max_zero_weight_fraction`` (default 0.05): the largest share of
    particles allowed a zero weight in a generation before UQ-PhysiCell stops the run.
    """
    from .abc_smc import abc_smc
    from .calibrate import CalibrationError

    version = installed("uq-physicell")
    if version is None:
        raise CalibrationError("method uq-physicell needs UQ-PhysiCell: pip install uq-physicell")
    import warnings

    with warnings.catch_warnings():
        warnings.simplefilter("ignore")  # UQ-PhysiCell warns that its optional PyTorch features are off
        from uq_physicell.abc.safeguards import CheckedABCSMC
        from uq_physicell.abc.transitions import ScaledMultivariateNormalTransition
    own = dict(settings)
    fraction = float(own.pop("max_zero_weight_fraction", 0.05))

    def make(*args: Any, **kwargs: Any) -> Any:
        return CheckedABCSMC(*args, max_zero_weight_fraction=fraction, **kwargs)

    return abc_smc(
        problem,
        own,
        abcsmc_class=make,
        transition=ScaledMultivariateNormalTransition(),
        method_detail=f"pyABC ABC-SMC with UQ-PhysiCell {version} safeguards and scaled kernel",
    )


def fitmulticell(problem: Problem, settings: Mapping[str, Any]) -> dict[str, Any]:
    """Report what FitMultiCell needs; its native-Morpheus calibration is not bridged."""
    from .calibrate import CalibrationError

    version = installed("fitmulticell")
    if version is None:
        raise CalibrationError("method fitmulticell needs FitMultiCell: pip install fitmulticell")
    raise CalibrationError(
        f"FitMultiCell {version} is installed, but its calibration runs a native Morpheus executable through its own "
        "model class, which ABMfact does not bridge yet. Method abc-smc runs the same pyABC algorithm through "
        "ABMfact's engines, Morpheus in its container included."
    )


def builtin_plugins() -> dict[str, Callable[[Problem, Mapping[str, Any]], dict[str, Any]]]:
    """The built-in plug-in slots by method name."""
    return {"uq-physicell": uq_physicell, "fitmulticell": fitmulticell}
