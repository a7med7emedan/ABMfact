"""ABMfact: run agent-based models across simulators and check their conclusions.

Run a model in several simulators through one engine contract, translate it through ABM-IR with a
fidelity report, and audit its conclusions by rerunning it under changes that a correct implementation
must not care about. ABMfact reports which conclusions survive: are they findings or artefacts?
"""

from __future__ import annotations

__version__ = "0.2.0.dev0"

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
