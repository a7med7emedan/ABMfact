"""ABMfact: is it a finding or an artefact?

Audit the conclusions of an agent-based model by rerunning it under changes that a correct
implementation must not care about, and report which conclusions survive.
"""

from __future__ import annotations

__version__ = "0.1.0"

from .adapters import Adapter, AdapterError, CommandAdapter, MesaAdapter, NetLogoAdapter, PythonAdapter
from .audit import AuditResult, run_audit
from .claims import Claim, ClaimError, Metric
from .config import Audit, Budget, ConfigError, load_audit, parse_audit
from .knobs import Knobs, Support

__all__ = [
    "Adapter",
    "AdapterError",
    "Audit",
    "AuditResult",
    "Budget",
    "Claim",
    "ClaimError",
    "CommandAdapter",
    "ConfigError",
    "Knobs",
    "MesaAdapter",
    "Metric",
    "NetLogoAdapter",
    "PythonAdapter",
    "Support",
    "__version__",
    "load_audit",
    "parse_audit",
    "run_audit",
]
