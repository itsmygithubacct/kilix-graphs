"""Layout engines, selected by name.

Every engine takes a graph and returns the same graph with an ``(x, y)`` on
each node and a route on each edge. Nothing in this package imports ``scene``
or ``render``: layout is a geometry problem, and keeping the import direction
one-way is what would let the package lift into ``kilix-modules`` the day a
second consumer appears. ``tests/test_boundaries.py`` enforces it.
"""

from __future__ import annotations

from typing import Callable

from ..model import Graph
from .force import ForceOptions, force
from .layered import LayeredOptions, layered
from .simple import CircularOptions, GridOptions, TreeOptions, circular, grid, tree

__all__ = [
    "CircularOptions",
    "ENGINES",
    "ForceOptions",
    "GridOptions",
    "LayeredOptions",
    "TreeOptions",
    "circular",
    "force",
    "grid",
    "layered",
    "run",
    "tree",
]

ENGINES: dict[str, Callable[..., Graph]] = {
    "layered": layered,
    "force": force,
    "tree": tree,
    "circular": circular,
    "grid": grid,
}

_OPTIONS: dict[str, type] = {
    "layered": LayeredOptions,
    "force": ForceOptions,
    "tree": TreeOptions,
    "circular": CircularOptions,
    "grid": GridOptions,
}


def run(graph: Graph, engine: str = "layered", **options: object) -> Graph:
    """Lay ``graph`` out with the named engine.

    Unknown option names are rejected rather than ignored, because a silently
    dropped ``ranksep`` looks exactly like an engine that does not respect it.
    """
    try:
        function = ENGINES[engine]
    except KeyError:
        known = ", ".join(sorted(ENGINES))
        raise ValueError(f"unknown layout engine {engine!r}; known: {known}") from None

    option_type = _OPTIONS[engine]
    fields = set(getattr(option_type, "__dataclass_fields__", {}))
    unknown = set(options) - fields
    if unknown:
        raise ValueError(
            f"{engine} does not take {', '.join(sorted(unknown))}; "
            f"it takes {', '.join(sorted(fields))}"
        )
    return function(graph, option_type(**options))  # type: ignore[arg-type]
