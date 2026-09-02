"""The scene: an ordered list of resolution-independent draw operations.

This is the seam that makes the rest of the design work.

- A node-link drawing and a chart both emit scene operations, and neither
  knows the other exists.
- The raster backend and the text backend both consume scene operations. The
  text backend is a second renderer, not a downsampled raster.
- Tests assert on the scene. A layout regression is a diff in a list of
  tuples, not an image comparison.

Coordinates are graph units with y increasing downward, which is what every
raster target wants and what saves a flip in the common path.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Iterable, Iterator, Sequence

__all__ = [
    "Anchor",
    "Ellipse",
    "Op",
    "Polygon",
    "Polyline",
    "Rect",
    "Scene",
    "Style",
    "Text",
]


class Anchor:
    START = "start"
    MIDDLE = "middle"
    END = "end"


@dataclass(frozen=True)
class Style:
    """How an operation is painted.

    ``fill`` and ``stroke`` are 0xRRGGBB or None. A shape with neither is not
    drawn, which makes "invisible" expressible without a special case.
    """

    fill: int | None = None
    stroke: int | None = None
    width: float = 1.0
    alpha: float = 1.0
    dash: tuple[int, int] = (0, 0)
    #: Integer font multiplier for Text; ignored elsewhere.
    scale: int = 1

    def with_(self, **kwargs: object) -> "Style":
        return replace(self, **kwargs)  # type: ignore[arg-type]


@dataclass(frozen=True)
class Op:
    """Base for every draw operation. ``layer`` orders drawing, low first."""

    style: Style = field(default=Style())
    layer: int = 0


@dataclass(frozen=True)
class Rect(Op):
    x: float = 0.0
    y: float = 0.0
    w: float = 0.0
    h: float = 0.0
    #: Corner radius. Zero is a plain rectangle.
    radius: float = 0.0


@dataclass(frozen=True)
class Ellipse(Op):
    cx: float = 0.0
    cy: float = 0.0
    rx: float = 0.0
    ry: float = 0.0


@dataclass(frozen=True)
class Polyline(Op):
    """An open chain of points, stroked as one shape."""

    points: tuple[tuple[float, float], ...] = ()


@dataclass(frozen=True)
class Polygon(Op):
    """A closed outline, filled and/or stroked."""

    points: tuple[tuple[float, float], ...] = ()


@dataclass(frozen=True)
class Text(Op):
    x: float = 0.0
    y: float = 0.0
    value: str = ""
    anchor: str = Anchor.MIDDLE
    #: When set, the renderer paints this behind the text so a label stays
    #: readable where it crosses an edge.
    halo: int | None = None
    #: True for a label with no space of its own -- an edge or cluster label,
    #: which floats over whatever the drawing put there. A cell renderer moves
    #: these out of the way of everything else; a label that owns its space,
    #: like a node's, is placed first and stays put.
    avoid: bool = False


class Scene:
    """An ordered, mutable list of operations with a declared extent.

    ``width`` and ``height`` are the drawing's own size in graph units. A
    renderer decides how that maps onto pixels or cells; the scene does not
    care and carries no device state.
    """

    __slots__ = ("ops", "width", "height", "background")

    def __init__(
        self,
        width: float = 0.0,
        height: float = 0.0,
        background: int | None = None,
    ) -> None:
        self.ops: list[Op] = []
        self.width = width
        self.height = height
        self.background = background

    def add(self, op: Op) -> Op:
        self.ops.append(op)
        return op

    def extend(self, ops: Iterable[Op]) -> None:
        self.ops.extend(ops)

    def __len__(self) -> int:
        return len(self.ops)

    def __iter__(self) -> Iterator[Op]:
        return iter(self.ops)

    def ordered(self) -> list[Op]:
        """Operations in paint order: by layer, then by insertion."""
        return sorted(self.ops, key=lambda op: op.layer)

    def bounds(self) -> tuple[float, float, float, float]:
        xs: list[float] = []
        ys: list[float] = []

        def span(x0: float, y0: float, x1: float, y1: float) -> None:
            xs.extend((x0, x1))
            ys.extend((y0, y1))

        for op in self.ops:
            if isinstance(op, Rect):
                span(op.x, op.y, op.x + op.w, op.y + op.h)
            elif isinstance(op, Ellipse):
                span(op.cx - op.rx, op.cy - op.ry, op.cx + op.rx, op.cy + op.ry)
            elif isinstance(op, (Polyline, Polygon)):
                for px, py in op.points:
                    span(px, py, px, py)
            elif isinstance(op, Text):
                span(op.x, op.y, op.x, op.y)
        if not xs:
            return (0.0, 0.0, 0.0, 0.0)
        return (min(xs), min(ys), max(xs), max(ys))

    def fit(self, margin: float = 0.0) -> "Scene":
        """Set width/height from the operations actually present."""
        _, _, max_x, max_y = self.bounds()
        self.width = max_x + margin
        self.height = max_y + margin
        return self


def polyline_points(points: Sequence[tuple[float, float]]) -> tuple[tuple[float, float], ...]:
    """Drop consecutive duplicates, which every routing pass can produce."""
    out: list[tuple[float, float]] = []
    for point in points:
        if not out or out[-1] != point:
            out.append(point)
    return tuple(out)
