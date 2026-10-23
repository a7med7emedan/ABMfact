"""Content digests that identify models and builds the same way on every platform.

Git on Windows usually checks text files out with CRLF line endings. A digest of the raw bytes would then
give the same model a different identity there, and a different cache key. :func:`content_bytes` turns CRLF
into LF for text files before hashing; other files are hashed as they are. Digests that certify the exact
bytes on disk (the run store's output hashes, the RO-Crate record of an audit) do not use this.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

#: Suffixes read as text. Files without a suffix are text only when named here.
TEXT_SUFFIXES = frozenset(
    {".yaml", ".yml", ".json", ".csv", ".tsv", ".txt", ".md", ".xml", ".sbml", ".bnd", ".cfg",
     ".c", ".cc", ".cpp", ".cxx", ".h", ".hh", ".hpp", ".py", ".r", ".jl", ".nlogo"}
)  # fmt: skip
TEXT_NAMES = frozenset({"Makefile", "makefile", "GNUmakefile"})


def is_text(path: Path) -> bool:
    return path.suffix.lower() in TEXT_SUFFIXES or path.name in TEXT_NAMES


def content_bytes(path: str | Path) -> bytes:
    """The file's bytes, with CRLF line endings turned into LF when it is a text file."""
    path = Path(path)
    data = path.read_bytes()
    return data.replace(b"\r\n", b"\n") if is_text(path) else data


def content_digest(path: str | Path) -> str:
    """SHA-256 of :func:`content_bytes`: the same for LF and CRLF copies of a text file."""
    return hashlib.sha256(content_bytes(path)).hexdigest()
