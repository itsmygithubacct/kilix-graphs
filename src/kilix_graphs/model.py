"""The graph model: what every parser produces and every layout consumes.

The model carries no coordinates. A node knows how big it wants to be, from
its label, before any layout runs; layout assigns positions and never invents
a size. Keeping the two apart is what lets the same graph be laid out by
several engines and compared.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable, Iterator

__all__ = [
    "Attrs",
    "Cluster",
    "Edge",
    "Graph",
    "Node",
    "Shape",
    "Size",
    "measure_label",
]

# The embedded soft-raster faces are fixed-cell: 8 pixels of advance per
# character in both, 16 or 14 pixels of cell height. Carrying the numbers here
# rather than asking the native library keeps layout runnable with no native
# library present at all, which is what makes the text backend and the tests
# work on a bare interpreter.
FONT_ADVANCE = 8
FONT_HEIGHT = 16

#: Padding between a node's label and its outline, in graph units.
LABEL_PAD_X = 12.0
LABEL_PAD_Y = 8.0

#: A node with no label still needs to be visible.
MIN_NODE_W = 24.0
MIN_NODE_H = 24.0

#: Headroom a labelled cluster reserves above its contents for its own name.
#: Layout adds it to the cluster's top padding and the composer centres the
#: label in it. One number, because two would drift and the symptom is a
#: cluster name written across the first thing inside the cluster.
CLUSTER_LABEL_BAND = FONT_HEIGHT + 8

Attrs = dict[str, str]


class Shape:
    """Node outlines the renderers know how to draw."""

    BOX = "box"
    ROUND = "round"
    ELLIPSE = "ellipse"
    CIRCLE = "circle"
    DIAMOND = "diamond"
    POINT = "point"

    ALL = (BOX, ROUND, ELLIPSE, CIRCLE, DIAMOND, POINT)


@dataclass(frozen=True)
class Size:
    w: float
    h: float


def measure_label(label: str, scale: int = 1) -> Size:
    """Size a node from its label.

    Multi-line labels are split on ``\\n``; the widest line sets the width.
    An empty label still yields the minimum node box, because a node with no
    text is a node, not nothing.
    """
    lines = label.split("\n") if label else [""]
    widest = max((len(line) for line in lines), default=0)
    w = widest * FONT_ADVANCE * scale + 2 * LABEL_PAD_X
    h = len(lines) * FONT_HEIGHT * scale + 2 * LABEL_PAD_Y
    return Size(max(w, MIN_NODE_W), max(h, MIN_NODE_H))


@dataclass
class Node:
    id: str
    label: str = ""
    shape: str = Shape.ROUND
    attrs: Attrs = field(default_factory=dict)
    cluster: str | None = None

    # Filled in by measure(); layout reads these and never changes them.
    w: float = 0.0
    h: float = 0.0

    # Filled in by a layout engine. The centre of the node.
    x: float = 0.0
    y: float = 0.0

    def measure(self, scale: int = 1) -> None:
        if self.shape == Shape.POINT:
            self.w = self.h = 8.0
            return
        size = measure_label(self.label or self.id, scale)
        self.w, self.h = size.w, size.h
        if self.shape == Shape.CIRCLE:
            self.w = self.h = max(size.w, size.h)

    @property
    def text(self) -> str:
        return self.label or self.id


@dataclass
class Edge:
    tail: str
    head: str
    label: str = ""
    attrs: Attrs = field(default_factory=dict)

    #: Set by layout/routing: the poly-line through which the edge is drawn,
    #: from the tail boundary to the head boundary.
    points: list[tuple[float, float]] = field(default_factory=list)
    #: True when the layout reversed this edge to break a cycle. The drawing
    #: still points the way the source said.
    reversed: bool = False

    @property
    def key(self) -> tuple[str, str]:
        return (self.tail, self.head)


@dataclass
class Cluster:
    id: str
    label: str = ""
    parent: str | None = None
    attrs: Attrs = field(default_factory=dict)

    # Filled in by layout: the bounding box enclosing the cluster's nodes.
    x: float = 0.0
    y: float = 0.0
    w: float = 0.0
    h: float = 0.0


@dataclass
class Graph:
    """A directed or undirected graph, optionally with nested clusters."""

    name: str = ""
    directed: bool = True
    attrs: Attrs = field(default_factory=dict)
    nodes: dict[str, Node] = field(default_factory=dict)
    edges: list[Edge] = field(default_factory=list)
    clusters: dict[str, Cluster] = field(default_factory=dict)

    # ------------------------------------------------------------ building

    def node(self, node_id: str, **kwargs: object) -> Node:
        """Get or create a node, updating any attributes given."""
        existing = self.nodes.get(node_id)
        if existing is None:
            existing = Node(id=node_id)
            self.nodes[node_id] = existing
        for name, value in kwargs.items():
            if value is None:
                continue
            if name == "attrs" and isinstance(value, dict):
                existing.attrs.update(value)
            else:
                setattr(existing, name, value)
        return existing

    def edge(self, tail: str, head: str, **kwargs: object) -> Edge:
        """Add an edge, creating either endpoint if it is new.

        Parallel edges are kept: two calls with the same endpoints make two
        edges, because a multigraph is a thing people draw.
        """
        self.node(tail)
        self.node(head)
        attrs = kwargs.pop("attrs", None)
        link = Edge(tail=tail, head=head, **kwargs)  # type: ignore[arg-type]
        if isinstance(attrs, dict):
            link.attrs.update(attrs)
        self.edges.append(link)
        return link

    def cluster(self, cluster_id: str, **kwargs: object) -> Cluster:
        existing = self.clusters.get(cluster_id)
        if existing is None:
            existing = Cluster(id=cluster_id)
            self.clusters[cluster_id] = existing
        for name, value in kwargs.items():
            if value is not None:
                setattr(existing, name, value)
        return existing

    # ------------------------------------------------------------ querying

    def __len__(self) -> int:
        return len(self.nodes)

    def __iter__(self) -> Iterator[Node]:
        return iter(self.nodes.values())

    def __contains__(self, node_id: object) -> bool:
        return node_id in self.nodes

    def successors(self, node_id: str) -> list[str]:
        return [e.head for e in self.edges if e.tail == node_id]

    def predecessors(self, node_id: str) -> list[str]:
        return [e.tail for e in self.edges if e.head == node_id]

    def neighbours(self, node_id: str) -> list[str]:
        out: list[str] = []
        for e in self.edges:
            if e.tail == node_id:
                out.append(e.head)
            elif e.head == node_id:
                out.append(e.tail)
        return out

    def degree(self, node_id: str) -> int:
        return sum(
            (1 if e.tail == node_id else 0) + (1 if e.head == node_id else 0)
            for e in self.edges
        )

    def measure(self, scale: int = 1) -> "Graph":
        """Give every node its intrinsic size. Idempotent."""
        for node in self.nodes.values():
            node.measure(scale)
        return self

    def bounds(self) -> tuple[float, float, float, float]:
        """(min_x, min_y, max_x, max_y) over nodes, routes and clusters."""
        xs: list[float] = []
        ys: list[float] = []
        for node in self.nodes.values():
            xs += [node.x - node.w / 2, node.x + node.w / 2]
            ys += [node.y - node.h / 2, node.y + node.h / 2]
        for edge in self.edges:
            for px, py in edge.points:
                xs.append(px)
                ys.append(py)
        for group in self.clusters.values():
            xs += [group.x, group.x + group.w]
            ys += [group.y, group.y + group.h]
        if not xs:
            return (0.0, 0.0, 0.0, 0.0)
        return (min(xs), min(ys), max(xs), max(ys))

    def translate(self, dx: float, dy: float) -> "Graph":
        for node in self.nodes.values():
            node.x += dx
            node.y += dy
        for edge in self.edges:
            edge.points = [(px + dx, py + dy) for px, py in edge.points]
        for group in self.clusters.values():
            group.x += dx
            group.y += dy
        return self

    def normalise(self, margin: float = 16.0) -> "Graph":
        """Move the drawing so its top-left sits at (margin, margin)."""
        min_x, min_y, _, _ = self.bounds()
        return self.translate(margin - min_x, margin - min_y)

    def copy(self) -> "Graph":
        clone = Graph(
            name=self.name, directed=self.directed, attrs=dict(self.attrs)
        )
        for node in self.nodes.values():
            clone.nodes[node.id] = Node(
                id=node.id,
                label=node.label,
                shape=node.shape,
                attrs=dict(node.attrs),
                cluster=node.cluster,
                w=node.w,
                h=node.h,
                x=node.x,
                y=node.y,
            )
        for edge in self.edges:
            clone.edges.append(
                Edge(
                    tail=edge.tail,
                    head=edge.head,
                    label=edge.label,
                    attrs=dict(edge.attrs),
                    points=list(edge.points),
                    reversed=edge.reversed,
                )
            )
        for group in self.clusters.values():
            clone.clusters[group.id] = Cluster(
                id=group.id,
                label=group.label,
                parent=group.parent,
                attrs=dict(group.attrs),
                x=group.x,
                y=group.y,
                w=group.w,
                h=group.h,
            )
        return clone


def from_edges(
    pairs: Iterable[tuple[str, str]], *, directed: bool = True, name: str = ""
) -> Graph:
    """Build a graph from an iterable of endpoint pairs."""
    graph = Graph(name=name, directed=directed)
    for tail, head in pairs:
        graph.edge(tail, head)
    return graph
