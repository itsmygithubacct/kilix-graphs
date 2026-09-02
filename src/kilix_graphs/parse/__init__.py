"""Three inputs, one model out.

Format detection is by extension where there is a filename, and by content
otherwise. Content sniffing is deliberately narrow — a leading ``{`` is JSON,
a ``digraph``/``graph`` header followed by a brace is DOT, everything else is
`.kg` — because a wrong guess produces a confusing parse error a long way from
the real problem.
"""

from __future__ import annotations

import re
from pathlib import Path

from ..model import Graph
from . import dot, jgf, kg

__all__ = ["FORMATS", "detect", "load", "loads"]

FORMATS = ("kg", "dot", "jgf")

_EXTENSIONS = {
    ".kg": "kg",
    ".gv": "dot",
    ".dot": "dot",
    ".json": "jgf",
    ".jgf": "jgf",
}

_DOT_HEADER = re.compile(r"^\s*(strict\s+)?(di)?graph\b[^\n{]*\{", re.IGNORECASE)


def detect(source: str, filename: str | None = None) -> str:
    """Name the format of ``source``."""
    if filename:
        suffix = Path(filename).suffix.lower()
        if suffix in _EXTENSIONS:
            return _EXTENSIONS[suffix]
    stripped = source.lstrip()
    if stripped.startswith("{") or stripped.startswith("["):
        return "jgf"
    if _DOT_HEADER.match(source):
        return "dot"
    return "kg"


def loads(source: str, fmt: str | None = None, filename: str | None = None) -> Graph:
    """Parse ``source``, detecting the format when one is not given."""
    chosen = fmt or detect(source, filename)
    if chosen == "dot":
        return dot.loads(source)
    if chosen == "jgf":
        return jgf.loads(source)
    if chosen == "kg":
        return kg.loads(source)
    raise ValueError(f"unknown format {chosen!r}; known: {', '.join(FORMATS)}")


def load(path: str | Path, fmt: str | None = None) -> Graph:
    """Parse a file."""
    text = Path(path).read_text(encoding="utf-8")
    return loads(text, fmt, filename=str(path))
