"""Tree, circular and grid layouts: the cases with a closed-form answer.

None of these iterates or searches. Where a graph has the shape they assume,
they give the right drawing immediately and a force layout only approximates
it; where it does not, they are the wrong tool and `layered` or `force` is the
answer.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from ..model import Graph
from ..route import route_straight
from .clusters import place_clusters, resolve_overlap

__all__ = ["CircularOptions", "GridOptions", "TreeOptions", "circular", "grid", "tree"]


@dataclass
class TreeOptions:
    nodesep: float = 32.0
    ranksep: float = 70.0
    direction: str = "TB"


@dataclass
class CircularOptions:
    #: Minimum gap along the ring between adjacent node boxes.
    gap: float = 24.0


@dataclass
class GridOptions:
    columns: int = 0  # 0 picks a near-square grid
    gap_x: float = 40.0
    gap_y: float = 40.0


def _roots(graph: Graph) -> list[str]:
    """Nodes with no parent, or the whole graph when every node has one."""
    has_parent = {edge.head for edge in graph.edges if edge.head != edge.tail}
    found = [name for name in graph.nodes if name not in has_parent]
    return found or list(graph.nodes)


def tree(graph: Graph, opts: TreeOptions | None = None) -> Graph:
    """Reingold-Tilford tidy trees, in the two-pass contour-free form.

    First pass: give every leaf the next free slot, and every internal node
    the midpoint of its children. Second pass: shift each subtree right by
    however much it would otherwise overlap the one before it. On a real tree
    this is the classic tidy drawing; on a graph with cycles the traversal
    visits each node once, so extra edges are drawn but do not move anything.
    """
    options = opts or TreeOptions()
    graph.measure()
    if not graph.nodes:
        return graph

    children: dict[str, list[str]] = {name: [] for name in graph.nodes}
    seen: set[str] = set()
    order: list[str] = []

    def walk(name: str, depth: int, depths: dict[str, int]) -> None:
        seen.add(name)
        depths[name] = depth
        order.append(name)
        for edge in graph.edges:
            if edge.tail != name or edge.head == name:
                continue
            if edge.head in seen:
                continue
            children[name].append(edge.head)
            walk(edge.head, depth + 1, depths)

    depths: dict[str, int] = {}
    for root in _roots(graph):
        if root not in seen:
            walk(root, 0, depths)
    for name in graph.nodes:  # anything a cycle hid from the walk
        if name not in seen:
            walk(name, 0, depths)

    horizontal = options.direction in ("LR", "RL")
    cursor = 0.0
    xs: dict[str, float] = {}

    def place(name: str) -> float:
        nonlocal cursor
        kids = children[name]
        extent = graph.nodes[name].h if horizontal else graph.nodes[name].w
        if not kids:
            xs[name] = cursor + extent / 2
            cursor += extent + options.nodesep
            return xs[name]
        positions = [place(kid) for kid in kids]
        centre = (positions[0] + positions[-1]) / 2
        xs[name] = centre
        return centre

    for root in [name for name in order if depths[name] == 0]:
        place(root)

    depth_extent: dict[int, float] = {}
    for name, depth in depths.items():
        size = graph.nodes[name].w if horizontal else graph.nodes[name].h
        depth_extent[depth] = max(depth_extent.get(depth, 0.0), size)

    offsets: dict[int, float] = {}
    running = 0.0
    for depth in sorted(depth_extent):
        offsets[depth] = running + depth_extent[depth] / 2
        running += depth_extent[depth] + options.ranksep

    for name, node in graph.nodes.items():
        along = xs.get(name, 0.0)
        across = offsets.get(depths.get(name, 0), 0.0)
        node.x, node.y = (across, along) if horizontal else (along, across)

    if options.direction in ("BT", "RL"):
        _, _, max_x, max_y = graph.bounds()
        for node in graph.nodes.values():
            if options.direction == "BT":
                node.y = max_y - node.y
            else:
                node.x = max_x - node.x

    resolve_overlap(graph)
    route_straight(graph)
    graph.normalise()
    return graph


def circular(graph: Graph, opts: CircularOptions | None = None) -> Graph:
    """Place every node on one ring, ordered to keep neighbours together.

    The order is a breadth-first walk rather than insertion order: adjacent
    nodes land next to each other on the ring, which is what keeps the chords
    short. The radius is chosen so the largest node still fits its arc, so a
    long label widens the ring instead of overlapping its neighbour.
    """
    options = opts or CircularOptions()
    graph.measure()
    count = len(graph.nodes)
    if count == 0:
        return graph
    if count == 1:
        only = next(iter(graph.nodes.values()))
        only.x = only.y = 0.0
        route_straight(graph)
        return graph.normalise()

    order: list[str] = []
    seen: set[str] = set()
    for start in graph.nodes:
        if start in seen:
            continue
        queue = [start]
        seen.add(start)
        while queue:
            name = queue.pop(0)
            order.append(name)
            for other in graph.neighbours(name):
                if other not in seen:
                    seen.add(other)
                    queue.append(other)

    widest = max(
        math.hypot(node.w, node.h) for node in graph.nodes.values()
    )
    step = 2 * math.pi / count
    radius = max((widest + options.gap) / (2 * math.sin(step / 2)), widest)

    for index, name in enumerate(order):
        angle = index * step - math.pi / 2
        node = graph.nodes[name]
        node.x = radius * math.cos(angle)
        node.y = radius * math.sin(angle)

    resolve_overlap(graph)
    route_straight(graph)
    graph.normalise()
    return graph


def grid(graph: Graph, opts: GridOptions | None = None) -> Graph:
    """Rows and columns. Not a layout so much as a catalogue."""
    options = opts or GridOptions()
    graph.measure()
    names = list(graph.nodes)
    if not names:
        return graph

    columns = options.columns or max(1, round(math.sqrt(len(names))))
    rows = math.ceil(len(names) / columns)
    col_width = [0.0] * columns
    row_height = [0.0] * rows
    for index, name in enumerate(names):
        node = graph.nodes[name]
        col_width[index % columns] = max(col_width[index % columns], node.w)
        row_height[index // columns] = max(row_height[index // columns], node.h)

    x_at = []
    running = 0.0
    for width in col_width:
        x_at.append(running + width / 2)
        running += width + options.gap_x
    y_at = []
    running = 0.0
    for height in row_height:
        y_at.append(running + height / 2)
        running += height + options.gap_y

    for index, name in enumerate(names):
        node = graph.nodes[name]
        node.x = x_at[index % columns]
        node.y = y_at[index // columns]

    resolve_overlap(graph)
    route_straight(graph)
    graph.normalise()
    return graph
