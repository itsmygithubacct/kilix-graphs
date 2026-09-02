"""Edge routing: turning endpoints into a drawable path.

On a layered layout most of this is already done. Normalisation replaced every
long edge with a chain of dummy nodes, so the dummy positions *are* the route
and it provably misses every node. What is left is trimming the first and last
segment to the node outlines and smoothing the corners.

On a force or circular layout there are no dummies, so an edge is a straight
line clipped the same way, with parallel edges bowed apart by index and
self-loops given a lobe. Obstacle-avoiding routing is deliberately absent: the
model for it when it lands is MSAGL's visibility-graph router, which is MIT,
rather than libavoid, which is LGPL.
"""

from __future__ import annotations

import math
from typing import Protocol, Sequence

from .model import Edge, Graph, Node, Shape

__all__ = [
    "arrow_head",
    "bow_parallel_edges",
    "catmull_rom",
    "clip_to_box",
    "route_straight",
]

Point = tuple[float, float]


class _Box(Protocol):
    x: float
    y: float
    w: float
    h: float
    shape: str


def clip_to_box(outside: Point, centre: Point, box: _Box) -> Point:
    """Where the segment from ``outside`` to the node centre meets its outline.

    Returns the node centre unchanged when the two points coincide, which is
    what a zero-length edge should draw: nothing.
    """
    dx = centre[0] - outside[0]
    dy = centre[1] - outside[1]
    if dx == 0.0 and dy == 0.0:
        return centre

    shape = getattr(box, "shape", Shape.BOX)
    hw = box.w / 2
    hh = box.h / 2

    if shape in (Shape.ELLIPSE, Shape.CIRCLE, Shape.POINT):
        # Scale the direction until it lands on the ellipse.
        denominator = math.hypot(dx / hw if hw else 0.0, dy / hh if hh else 0.0)
        if denominator == 0.0:
            return centre
        return (centre[0] - dx / denominator, centre[1] - dy / denominator)

    if shape == Shape.DIAMOND:
        denominator = abs(dx) / hw + abs(dy) / hh if hw and hh else 0.0
        if denominator == 0.0:
            return centre
        return (centre[0] - dx / denominator, centre[1] - dy / denominator)

    # Box: the smaller of the two axis intersections is the one on the outline.
    scale_x = hw / abs(dx) if dx else math.inf
    scale_y = hh / abs(dy) if dy else math.inf
    scale = min(scale_x, scale_y)
    if not math.isfinite(scale):
        return centre
    return (centre[0] - dx * scale, centre[1] - dy * scale)


def route_straight(graph: Graph) -> Graph:
    """Give every unrouted edge a straight, clipped, two-point route."""
    for edge in graph.edges:
        if edge.points:
            continue
        tail = graph.nodes[edge.tail]
        head = graph.nodes[edge.head]
        if edge.tail == edge.head:
            edge.points = _self_loop(tail)
            continue
        start = clip_to_box((head.x, head.y), (tail.x, tail.y), tail)
        end = clip_to_box((tail.x, tail.y), (head.x, head.y), head)
        edge.points = [start, end]
    bow_parallel_edges(graph)
    return graph


def _self_loop(node: Node) -> list[Point]:
    reach = node.w / 2 + max(node.w, node.h) * 0.5
    top = node.y - node.h / 4
    bottom = node.y + node.h / 4
    return [
        (node.x + node.w / 2, top),
        (node.x + reach, top - node.h / 4),
        (node.x + reach, bottom + node.h / 4),
        (node.x + node.w / 2, bottom),
    ]


def bow_parallel_edges(graph: Graph, spread: float = 18.0) -> Graph:
    """Bow edges that share endpoints apart so each one is visible.

    Two straight edges between the same pair of nodes are one line drawn
    twice. Bowing them by index around the midpoint separates them without
    changing where either one attaches.
    """
    groups: dict[tuple[str, str], list[Edge]] = {}
    for edge in graph.edges:
        if edge.tail == edge.head or len(edge.points) != 2:
            continue
        key = tuple(sorted((edge.tail, edge.head)))
        groups.setdefault(key, []).append(edge)  # type: ignore[arg-type]

    for parallel in groups.values():
        if len(parallel) < 2:
            continue
        for index, edge in enumerate(parallel):
            offset = (index - (len(parallel) - 1) / 2) * spread
            if offset == 0.0:
                continue
            (x0, y0), (x1, y1) = edge.points
            length = math.hypot(x1 - x0, y1 - y0)
            if length == 0.0:
                continue
            nx, ny = -(y1 - y0) / length, (x1 - x0) / length
            mid = ((x0 + x1) / 2 + nx * offset, (y0 + y1) / 2 + ny * offset)
            edge.points = [(x0, y0), mid, (x1, y1)]
    return graph


def catmull_rom(points: Sequence[Point], tension: float = 0.5, steps: int = 8) -> list[Point]:
    """Smooth a poly-line through its own points.

    Interpolating rather than approximating matters here: a routed edge's
    interior points are dummy positions chosen precisely so the edge misses
    every node, and a curve that only approximates them can pass through one.
    """
    if len(points) < 3:
        return list(points)

    extended = [points[0], *points, points[-1]]
    out: list[Point] = [points[0]]
    for i in range(1, len(extended) - 2):
        p0, p1, p2, p3 = extended[i - 1], extended[i], extended[i + 1], extended[i + 2]
        for step in range(1, steps + 1):
            t = step / steps
            t2 = t * t
            t3 = t2 * t
            m1x = tension * (p2[0] - p0[0])
            m1y = tension * (p2[1] - p0[1])
            m2x = tension * (p3[0] - p1[0])
            m2y = tension * (p3[1] - p1[1])
            h00 = 2 * t3 - 3 * t2 + 1
            h10 = t3 - 2 * t2 + t
            h01 = -2 * t3 + 3 * t2
            h11 = t3 - t2
            out.append(
                (
                    h00 * p1[0] + h10 * m1x + h01 * p2[0] + h11 * m2x,
                    h00 * p1[1] + h10 * m1y + h01 * p2[1] + h11 * m2y,
                )
            )
    return out


def arrow_head(points: Sequence[Point], size: float = 10.0, spread: float = 0.42) -> list[Point]:
    """The triangle at the end of a route, pointing along its last segment.

    Returns an empty list when the route has no direction to point along,
    rather than a degenerate triangle at the origin.
    """
    if len(points) < 2 or size <= 0:
        return []
    (x0, y0), (x1, y1) = points[-2], points[-1]
    angle = math.atan2(y1 - y0, x1 - x0)
    if not math.isfinite(angle):
        return []
    return [
        (x1, y1),
        (x1 - size * math.cos(angle - spread), y1 - size * math.sin(angle - spread)),
        (x1 - size * math.cos(angle + spread), y1 - size * math.sin(angle + spread)),
    ]


def trim_for_arrow(points: Sequence[Point], size: float) -> list[Point]:
    """Pull the route back so a butt-capped stroke ends at the arrow's base.

    Without this the stroke runs to the tip and pokes through the arrowhead
    whenever the head is drawn in a different colour.
    """
    route = list(points)
    if len(route) < 2 or size <= 0:
        return route
    (x0, y0), (x1, y1) = route[-2], route[-1]
    length = math.hypot(x1 - x0, y1 - y0)
    if length <= size:
        # The last segment is shorter than the head; shorten it as far as it
        # goes rather than reversing its direction.
        route[-1] = (x0, y0)
        return route
    scale = (length - size * 0.85) / length
    route[-1] = (x0 + (x1 - x0) * scale, y0 + (y1 - y0) * scale)
    return route
