"""Bypassing caches when a run must really execute again.

The determinism check reruns seeds that have already run, with the same request. Any cache keyed on the
request (the run store, or a study's own cache) would hand back the first result and make the check pass
whatever the model does. The audit runs its reruns inside :func:`bypass_cache`; caches consult
:func:`cache_bypassed` and then neither read nor write.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar

_BYPASS: ContextVar[bool] = ContextVar("abmfact_cache_bypass", default=False)


def cache_bypassed() -> bool:
    """True while a run must execute again rather than be served from a cache."""
    return _BYPASS.get()


@contextmanager
def bypass_cache() -> Iterator[None]:
    """A context manager in which :func:`cache_bypassed` is true, so caches neither read nor write."""
    token = _BYPASS.set(True)
    try:
        yield
    finally:
        _BYPASS.reset(token)
