"""Cluster boxes, shared by every engine.

A `.kg` file that declares groups declares them for whatever engine runs.
Boxing them only in the layered engine meant `-e force` silently dropped every
group from the drawing -- the same input, the same request, and the grouping
just gone.

The boxes are computed innermost-first, so a parent encloses its children's
*boxes* rather than only the nodes inside them, and a labelled cluster reserves
a band above its contents for its own name. Whether the boxes can then overlap
is the engine's business: the layered engine separates them, and the others
place nodes without any notion of a group, so a box there is a summary of where
the members landed rather than a region the layout respected.
"""

from __future__ import annotations

from ..model import CLUSTER_LABEL_BAND, Graph

__all__ = [
    "CLUSTER_PAD",
    "ancestry",
    "descendants",
    "nesting_depth",
    "place_clusters",
    "resolve_overlap",
    "top_pad",
]

CLUSTER_PAD = 20.0


def descendants(graph: Graph, cluster_id: str) -> set[str]:
    """A cluster's own id plus every cluster nested inside it."""
    ids = {cluster_id}
    changed = True
    while changed:
        changed = False
        for cluster in graph.clusters.values():
            if cluster.parent in ids and cluster.id not in ids:
                ids.add(cluster.id)
                changed = True
    return ids


def nesting_depth(graph: Graph, cluster_id: str) -> int:
    depth = 0
    current = graph.clusters[cluster_id].parent
    seen = {cluster_id}
    while current in graph.clusters and current not in seen:
        seen.add(current)
        depth += 1
        current = graph.clusters[current].parent
    return depth


def top_pad(graph: Graph, cluster_id: str) -> float:
    """Space a cluster needs above its contents: padding, plus its label."""
    label = graph.clusters[cluster_id].label
    return CLUSTER_PAD + (CLUSTER_LABEL_BAND if label else 0.0)


def ancestry(graph: Graph, cluster_id: str | None) -> list[str]:
    """A cluster and every cluster it sits inside, innermost first."""
    chain: list[str] = []
    current = cluster_id
    while current in graph.clusters and current not in chain:
        chain.append(current)
        current = graph.clusters[current].parent
    return chain


def place_clusters(graph: Graph) -> None:
    """Box each cluster around its members, innermost first.

    Taking the union of member nodes alone gives a parent whose outline lands
    exactly on its child's wherever the two hold the same column, and two
    coincident outlines do not read as nesting. A parent whose members all sit
    in child clusters has no node naming it directly, and got no box at all.
    """
    for cluster_id in sorted(
        graph.clusters, key=lambda name: nesting_depth(graph, name), reverse=True
    ):
        cluster = graph.clusters[cluster_id]
        members = [n for n in graph.nodes.values() if n.cluster == cluster_id]
        children = [
            c for c in graph.clusters.values()
            if c.parent == cluster_id and c.w > 0 and c.h > 0
        ]
        corners = [
            (n.x - n.w / 2, n.y - n.h / 2, n.x + n.w / 2, n.y + n.h / 2)
            for n in members
        ] + [(c.x, c.y, c.x + c.w, c.y + c.h) for c in children]
        if not corners:
            cluster.w = cluster.h = 0.0
            continue
        x0 = min(c[0] for c in corners) - CLUSTER_PAD
        y0 = min(c[1] for c in corners) - top_pad(graph, cluster_id)
        x1 = max(c[2] for c in corners) + CLUSTER_PAD
        y1 = max(c[3] for c in corners) + CLUSTER_PAD
        cluster.x, cluster.y = x0, y0
        cluster.w, cluster.h = x1 - x0, y1 - y0


def _shift(graph: Graph, cluster_id: str, dx: float, dy: float) -> None:
    inside = descendants(graph, cluster_id)
    for node in graph.nodes.values():
        if node.cluster in inside:
            node.x += dx
            node.y += dy


def resolve_overlap(
    graph: Graph, gap: float = 16.0, passes: int = 24, node_gap: float = 8.0
) -> None:
    """Push sibling cluster boxes apart, and foreign nodes out of them.

    The layered engine separates clusters using the rank structure it already
    has. The other engines have no such structure -- a force layout does not
    know what a group is, and a grid is a catalogue -- so their boxes are drawn
    around wherever the members happened to land. Measured on the example
    pipeline under `-e force`: three overlapping pairs and ten nodes drawn
    inside a group they are not in, which is the same lie the layered engine
    used to tell.

    This is relaxation, not a solver: each pass moves whole clusters apart
    along their smaller overlap, pushes a foreign node out by its shortest
    exit, and pushes apart any two nodes that ended up on top of each other --
    that last rule is not optional, because evicting a node from a box lands it
    somewhere, and without it a force layout came out with two nodes of the
    same cluster drawn over one another. The pass stops when nothing changes.
    On a dense graph it can reach the cap with an overlap left, so this makes
    the drawing honest far more often rather than always, which is the same
    contract the force engine's own collision pass has.
    """
    everything = list(graph.clusters.values())
    if not everything:
        return
    # Separation is per level: two clusters constrain each other only when they
    # are siblings, because a child is *supposed* to sit inside its parent.
    levels: dict[str | None, list] = {}
    for cluster in everything:
        levels.setdefault(cluster.parent, []).append(cluster)

    for _ in range(max(1, passes)):
        place_clusters(graph)
        moved = False

        for siblings in levels.values():
            for index, first in enumerate(siblings):
                for second in siblings[index + 1 :]:
                    if first.w <= 0 or second.w <= 0:
                        continue
                    across = (
                        min(first.x + first.w, second.x + second.w)
                        - max(first.x, second.x) + gap
                    )
                    down = (
                        min(first.y + first.h, second.y + second.h)
                        - max(first.y, second.y) + gap
                    )
                    if across <= 0 or down <= 0:
                        continue
                    # Move along whichever axis needs the smaller correction:
                    # the other would drag the drawing further than the overlap.
                    if across < down:
                        step = across / 2 * (1 if first.x < second.x else -1)
                        _shift(graph, first.id, -step, 0.0)
                        _shift(graph, second.id, step, 0.0)
                    else:
                        step = down / 2 * (1 if first.y < second.y else -1)
                        _shift(graph, first.id, 0.0, -step)
                        _shift(graph, second.id, 0.0, step)
                    moved = True

        for cluster in everything:
            if cluster.w <= 0:
                continue
            inside = descendants(graph, cluster.id)
            for node in graph.nodes.values():
                # A node deeper in the same branch belongs here; one from a
                # sibling branch, or from the parent itself, does not.
                if node.cluster in inside:
                    continue
                if (
                    node.x + node.w / 2 <= cluster.x
                    or node.x - node.w / 2 >= cluster.x + cluster.w
                    or node.y + node.h / 2 <= cluster.y
                    or node.y - node.h / 2 >= cluster.y + cluster.h
                ):
                    continue
                left = node.x + node.w / 2 - cluster.x + gap
                right = cluster.x + cluster.w - (node.x - node.w / 2) + gap
                up = node.y + node.h / 2 - cluster.y + gap
                down = cluster.y + cluster.h - (node.y - node.h / 2) + gap
                shortest = min(left, right, up, down)
                if shortest == left:
                    node.x -= left
                elif shortest == right:
                    node.x += right
                elif shortest == up:
                    node.y -= up
                else:
                    node.y += down
                moved = True

        nodes = list(graph.nodes.values())
        if len(nodes) <= 400:  # the pair sweep is quadratic; a catalogue is not
            for index, first in enumerate(nodes):
                for second in nodes[index + 1 :]:
                    across = (first.w + second.w) / 2 + node_gap - abs(second.x - first.x)
                    down = (first.h + second.h) / 2 + node_gap - abs(second.y - first.y)
                    if across <= 0 or down <= 0:
                        continue
                    if across < down:
                        step = across / 2 * (1 if second.x >= first.x else -1)
                        first.x -= step
                        second.x += step
                    else:
                        step = down / 2 * (1 if second.y >= first.y else -1)
                        first.y -= step
                        second.y += step
                    moved = True

        if not moved:
            break
    place_clusters(graph)
