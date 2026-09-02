"""Turn a laid-out graph into a scene.

The layers are fixed and matter: clusters sit under edges, edges under nodes,
labels over everything. Drawing an edge over a node makes the node look
punctured; drawing a label under an edge makes it unreadable. Both are
one-line mistakes that a fixed layer order prevents.
"""

from __future__ import annotations

from dataclasses import dataclass

from .model import CLUSTER_LABEL_BAND, FONT_HEIGHT, Graph, Shape
from .route import arrow_head, catmull_rom, trim_for_arrow
from .scene import Anchor, Ellipse, Polygon, Polyline, Rect, Scene, Style, Text
from .theme import DARK, Theme, parse_colour

__all__ = ["ComposeOptions", "LAYER_CLUSTER", "LAYER_EDGE", "LAYER_LABEL", "LAYER_NODE", "compose"]

LAYER_CLUSTER = 0
LAYER_EDGE = 10
LAYER_NODE = 20
LAYER_LABEL = 30


@dataclass
class ComposeOptions:
    theme: Theme = DARK
    margin: float = 24.0
    #: Smooth routed edges through their own points.
    curved: bool = True
    arrow_size: float = 11.0
    node_stroke: float = 1.5
    edge_width: float = 1.5
    corner_radius: float = 8.0
    #: Colour nodes by cluster, so a grouped drawing reads without reading.
    colour_by_cluster: bool = True
    font_scale: int = 1


def compose(graph: Graph, opts: ComposeOptions | None = None) -> Scene:
    """Build the scene for a graph that has already been laid out."""
    options = opts or ComposeOptions()
    theme = options.theme
    scene = Scene(background=theme.surface)

    cluster_colour = _cluster_colours(graph, theme, options)

    for cluster in graph.clusters.values():
        if cluster.w <= 0 or cluster.h <= 0:
            continue
        accent = cluster_colour.get(cluster.id, theme.cluster_stroke)
        scene.add(
            Rect(
                x=cluster.x,
                y=cluster.y,
                w=cluster.w,
                h=cluster.h,
                radius=options.corner_radius,
                layer=LAYER_CLUSTER,
                style=Style(
                    fill=theme.cluster_fill,
                    stroke=accent,
                    width=1.0,
                    alpha=0.9,
                    dash=(6, 4),
                ),
            )
        )
        if cluster.label:
            scene.add(
                Text(
                    x=cluster.x + 10,
                    # Centred in the band layout reserved for it. Below the
                    # outline, because at the outline a cell renderer has to
                    # choose between the label and the border and either
                    # choice loses something -- and above the contents,
                    # because layout kept that space empty.
                    y=cluster.y + CLUSTER_LABEL_BAND / 2,
                    value=cluster.label,
                    anchor=Anchor.START,
                    layer=LAYER_LABEL,
                    halo=theme.halo,
                    avoid=True,
                    style=Style(fill=accent, scale=options.font_scale),
                )
            )

    # Edges that share a pair of endpoints get their labels at different
    # points along the route. Two anti-parallel edges both label their
    # midpoint, and the two labels land on the same spot -- "pause" and
    # "resume" came out as "pauresume".
    spread: dict[int, tuple[int, int, bool]] = {}
    groups: dict[tuple[str, str], list[int]] = {}
    for index, edge in enumerate(graph.edges):
        groups.setdefault(tuple(sorted((edge.tail, edge.head))), []).append(index)
    for key, members in groups.items():
        for position, index in enumerate(members):
            # An anti-parallel pair runs its route the other way, so the same
            # fraction is the same *place* on one and the opposite place on
            # the other. Measure both from the same end of the pair.
            reverse = graph.edges[index].tail != key[0]
            spread[index] = (position, len(members), reverse)

    for index, edge in enumerate(graph.edges):
        _compose_edge(scene, graph, edge, options, spread.get(index, (0, 1, False)))

    for node in graph.nodes.values():
        _compose_node(scene, node, cluster_colour, options)

    # Put the whole drawing -- labels included -- inside the margin. Layout
    # normalises the *geometry*, and a label that starts left of every node
    # then sits at a negative coordinate and gets clipped.
    min_x, min_y, _, _ = scene.bounds()
    scene.translate(options.margin - min_x, options.margin - min_y)
    _, _, max_x, max_y = scene.bounds()
    scene.width = max_x + options.margin
    scene.height = max_y + options.margin
    return scene


def _cluster_colours(
    graph: Graph, theme: Theme, options: ComposeOptions
) -> dict[str, int]:
    if not options.colour_by_cluster:
        return {}
    return {
        name: theme.categorical(index)
        for index, name in enumerate(sorted(graph.clusters))
    }


def _compose_node(
    scene: Scene,
    node: object,
    cluster_colour: dict[str, int],
    options: ComposeOptions,
) -> None:
    theme = options.theme
    x = node.x - node.w / 2  # type: ignore[attr-defined]
    y = node.y - node.h / 2  # type: ignore[attr-defined]
    accent = cluster_colour.get(getattr(node, "cluster", None) or "", theme.node_stroke)
    explicit = getattr(node, "attrs", {}).get("color")
    if explicit:
        accent = parse_colour(explicit)
    fill = theme.node_fill
    explicit_fill = getattr(node, "attrs", {}).get("fillcolor")
    if explicit_fill:
        fill = parse_colour(explicit_fill)

    style = Style(fill=fill, stroke=accent, width=options.node_stroke)
    shape = getattr(node, "shape", Shape.ROUND)

    if shape == Shape.POINT:
        scene.add(
            Ellipse(
                cx=node.x,  # type: ignore[attr-defined]
                cy=node.y,  # type: ignore[attr-defined]
                rx=node.w / 2,  # type: ignore[attr-defined]
                ry=node.h / 2,  # type: ignore[attr-defined]
                layer=LAYER_NODE,
                style=Style(fill=accent, stroke=None),
            )
        )
        return

    if shape in (Shape.ELLIPSE, Shape.CIRCLE):
        scene.add(
            Ellipse(
                cx=node.x,  # type: ignore[attr-defined]
                cy=node.y,  # type: ignore[attr-defined]
                rx=node.w / 2,  # type: ignore[attr-defined]
                ry=node.h / 2,  # type: ignore[attr-defined]
                layer=LAYER_NODE,
                style=style,
            )
        )
    elif shape == Shape.DIAMOND:
        scene.add(
            Polygon(
                points=(
                    (node.x, y),  # type: ignore[attr-defined]
                    (x + node.w, node.y),  # type: ignore[attr-defined]
                    (node.x, y + node.h),  # type: ignore[attr-defined]
                    (x, node.y),  # type: ignore[attr-defined]
                ),
                layer=LAYER_NODE,
                style=style,
            )
        )
    else:
        radius = options.corner_radius if shape == Shape.ROUND else 0.0
        scene.add(
            Rect(
                x=x,
                y=y,
                w=node.w,  # type: ignore[attr-defined]
                h=node.h,  # type: ignore[attr-defined]
                radius=radius,
                layer=LAYER_NODE,
                style=style,
            )
        )

    text = getattr(node, "text", "")
    if text:
        scene.add(
            Text(
                x=node.x,  # type: ignore[attr-defined]
                y=node.y,  # type: ignore[attr-defined]
                value=text,
                anchor=Anchor.MIDDLE,
                layer=LAYER_LABEL,
                style=Style(fill=theme.ink, scale=options.font_scale),
            )
        )


def _along(
    points: list[tuple[float, float]], fraction: float
) -> tuple[tuple[float, float], tuple[float, float]]:
    """A point a given fraction along a route, and the direction there."""
    spans = [
        ((points[i + 1][0] - points[i][0]) ** 2
         + (points[i + 1][1] - points[i][1]) ** 2) ** 0.5
        for i in range(len(points) - 1)
    ]
    total = sum(spans)
    if total <= 0.0:
        return (points[0], (1.0, 0.0))
    target = total * min(max(fraction, 0.0), 1.0)
    for index, span in enumerate(spans):
        if target <= span or index == len(spans) - 1:
            t = target / span if span else 0.0
            a, b = points[index], points[index + 1]
            here = (a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t)
            return (here, ((b[0] - a[0]) / (span or 1.0), (b[1] - a[1]) / (span or 1.0)))
        target -= span
    return (points[-1], (1.0, 0.0))


def _label_anchor(
    points: list[tuple[float, float]],
    position: int,
    count: int,
    reverse: bool = False,
) -> tuple[tuple[float, float], tuple[float, float]]:
    """Where a label goes on a route, and the offset that lifts it clear.

    A label centred on the line sits on top of it. The halo keeps it legible
    but the line still runs through the letters, so it is pushed perpendicular
    by most of a line height -- upward wherever the edge is not vertical,
    because a label above a line reads as attached to it.

    Edges sharing a pair of endpoints take different points *along* the route
    rather than all taking the midpoint. Perpendicular offset alone cannot
    separate two anti-parallel edges: their normals point the same way and
    both labels land on the same spot.
    """
    from .model import FONT_HEIGHT

    fraction = 0.5 if count <= 1 else 0.3 + 0.4 * position / (count - 1)
    if reverse:
        fraction = 1.0 - fraction
    here, (dx, dy) = _along(points, fraction)
    lift = FONT_HEIGHT * 0.7
    if dx == 0.0 and dy == 0.0:
        return (here, (0.0, -lift))
    nx, ny = -dy, dx
    if ny > 0:
        nx, ny = -nx, -ny
    return (here, (nx * lift, ny * lift))


def _compose_edge(
    scene: Scene,
    graph: Graph,
    edge: object,
    options: ComposeOptions,
    spread: tuple[int, int, bool] = (0, 1, False),
) -> None:
    theme = options.theme
    points = list(getattr(edge, "points", []))
    if len(points) < 2:
        return

    attrs = getattr(edge, "attrs", {})
    # A self-loop is a lobe in pixels and a tangle in cells: four points inside
    # five columns, orthogonalised into a knot of junctions across the node it
    # belongs to. Naming it lets the cell renderer draw one glyph instead.
    role = "loop" if edge.tail == edge.head else ""  # type: ignore[attr-defined]
    colour = parse_colour(attrs["color"]) if attrs.get("color") else theme.edge
    dashed = attrs.get("style") in ("dashed", "dotted")
    dash = (2, 4) if attrs.get("style") == "dotted" else (7, 5) if dashed else (0, 0)
    directed = graph.directed and attrs.get("dir") != "none"

    if options.curved and len(points) > 2:
        points = catmull_rom(points)

    head = arrow_head(points, options.arrow_size) if directed else []
    if head:
        points = trim_for_arrow(points, options.arrow_size)

    scene.add(
        Polyline(
            points=tuple(points),
            layer=LAYER_EDGE,
            style=Style(
                stroke=colour, width=options.edge_width, dash=dash, role=role
            ),
        )
    )
    if head:
        scene.add(
            Polygon(
                points=tuple(head),
                layer=LAYER_EDGE,
                style=Style(fill=colour, stroke=None, role=role),
            )
        )

    label = getattr(edge, "label", "")
    if label:
        if edge.tail == edge.head:  # type: ignore[attr-defined]
            # A self-loop's midpoint is the far side of its own lobe, and the
            # perpendicular there points back into the node. Put the label
            # outside the lobe instead, reading away from the node.
            far = max(points, key=lambda point: point[0])
            anchor_x, anchor_y = far[0] + 6.0, far[1]
            align = Anchor.START
        else:
            middle, offset = _label_anchor(points, *spread)
            anchor_x, anchor_y = middle[0] + offset[0], middle[1] + offset[1]
            align = Anchor.MIDDLE
        scene.add(
            Text(
                x=anchor_x,
                y=anchor_y,
                value=label,
                anchor=align,
                layer=LAYER_LABEL,
                halo=theme.halo,
                avoid=True,
                style=Style(fill=theme.ink_muted, scale=options.font_scale),
            )
        )
