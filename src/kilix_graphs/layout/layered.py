"""Layered (Sugiyama) layout: the layout a directed graph wants.

Five phases, each resting on a published algorithm:

1. **Acyclic** — greedy feedback-arc set (Eades, Lin and Smyth 1993). Reversed
   edges are marked and put back at the end, so the drawing still points the
   way the source said.
2. **Rank** — longest-path layering, then a coordinate-descent slack pass that
   moves a node to the feasible rank minimising its weighted edge length. Each
   move strictly lowers a non-negative integer objective, so it terminates.
   The published upgrade is network simplex (Gansner et al. 1993 §2); this is
   deliberately the simpler thing, and `total_edge_length()` makes the
   difference measurable when someone wants to make that trade.
3. **Normalise** — an edge spanning more than one rank becomes a chain of
   dummy nodes, one per crossed rank. Edge routing then falls out for free:
   the dummy positions *are* the route, and it provably misses every node.
4. **Order** — barycentre sweeps, alternating down and up, keeping the best
   result by weighted crossing count. The count is Barth, Junger and Mutzel's
   "Bilayer Cross Counting" accumulator tree, O(|E| log |V|) rather than the
   obvious O(|E|^2).
5. **Position** — Brandes and Kopf, "Fast and Simple Horizontal Coordinate
   Assignment" (GD 2001): four alignments, each block compacted to its
   leftmost feasible position, then aligned to the narrowest and averaged.

Clusters are not part of the compaction. Their bounding boxes are computed
from their members after positioning, which keeps this phase free of the
compound-graph machinery and is honest about what it does: nested clusters can
overlap where the layout had no reason to separate them.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

from ..model import Graph

__all__ = ["LayeredOptions", "layered", "total_edge_length"]


@dataclass
class LayeredOptions:
    #: "TB", "BT", "LR" or "RL".
    direction: str = "TB"
    #: Space between adjacent nodes on a rank.
    nodesep: float = 40.0
    #: Space between adjacent dummy nodes (edge routes) on a rank.
    edgesep: float = 16.0
    #: Space between ranks.
    ranksep: float = 60.0
    #: Ordering sweeps. Each sweep is one pass over every rank.
    sweeps: int = 8
    #: Slack-reduction rounds during ranking.
    rank_rounds: int = 8


@dataclass
class _N:
    """A node in the working graph: real, dummy, or a self-loop placeholder."""

    id: str
    w: float
    h: float
    dummy: bool = False
    #: For a dummy, the index of the edge it belongs to.
    edge: int = -1
    rank: int = 0
    order: int = 0
    x: float = 0.0
    y: float = 0.0


@dataclass
class _W:
    """The working graph. Separate from the model so the model stays clean."""

    nodes: dict[str, _N] = field(default_factory=dict)
    #: (tail, head, weight, minlen)
    edges: list[tuple[str, str, float, int]] = field(default_factory=list)
    out: dict[str, list[int]] = field(default_factory=dict)
    inc: dict[str, list[int]] = field(default_factory=dict)

    def index(self) -> None:
        self.out = {v: [] for v in self.nodes}
        self.inc = {v: [] for v in self.nodes}
        for i, (u, v, _, _) in enumerate(self.edges):
            self.out[u].append(i)
            self.inc[v].append(i)


# ---------------------------------------------------------------- 1. acyclic


def _greedy_fas(nodes: list[str], arcs: list[tuple[str, str, float]]) -> set[int]:
    """Return the indices of arcs to reverse, by the greedy sink/source rule.

    Sinks are peeled to the right and sources to the left; when neither
    exists, the vertex with the largest weighted out-minus-in degree goes
    left. Every arc that then runs backwards in the resulting sequence is in
    the feedback set. Reversing them cannot leave a cycle, because the
    sequence is a topological order of what remains.
    """
    outdeg: dict[str, float] = {v: 0.0 for v in nodes}
    indeg: dict[str, float] = {v: 0.0 for v in nodes}
    out_arcs: dict[str, list[int]] = {v: [] for v in nodes}
    in_arcs: dict[str, list[int]] = {v: [] for v in nodes}
    for i, (u, v, weight) in enumerate(arcs):
        if u == v:
            continue
        outdeg[u] += weight
        indeg[v] += weight
        out_arcs[u].append(i)
        in_arcs[v].append(i)

    live = set(nodes)
    left: list[str] = []
    right: list[str] = []

    def remove(v: str) -> None:
        live.discard(v)
        for i in out_arcs[v]:
            head = arcs[i][1]
            if head in live:
                indeg[head] -= arcs[i][2]
        for i in in_arcs[v]:
            tail = arcs[i][0]
            if tail in live:
                outdeg[tail] -= arcs[i][2]

    while live:
        moved = True
        while moved:
            moved = False
            for v in [v for v in live if outdeg[v] <= 0.0]:
                remove(v)
                right.append(v)
                moved = True
            for v in [v for v in live if indeg[v] <= 0.0]:
                remove(v)
                left.append(v)
                moved = True
        if live:
            pick = max(live, key=lambda v: (outdeg[v] - indeg[v], v))
            remove(pick)
            left.append(pick)

    order = {v: i for i, v in enumerate(left + list(reversed(right)))}
    return {
        i
        for i, (u, v, _) in enumerate(arcs)
        if u != v and order[u] > order[v]
    }


# ------------------------------------------------------------------ 2. rank


def _longest_path(work: _W) -> None:
    """Rank every node one past its deepest predecessor."""
    incoming = {v: len(work.inc[v]) for v in work.nodes}
    ready = [v for v, n in incoming.items() if n == 0]
    for v in work.nodes:
        work.nodes[v].rank = 0
    seen = 0
    while ready:
        v = ready.pop()
        seen += 1
        for i in work.out[v]:
            _, head, _, minlen = work.edges[i]
            candidate = work.nodes[v].rank + minlen
            if candidate > work.nodes[head].rank:
                work.nodes[head].rank = candidate
            incoming[head] -= 1
            if incoming[head] == 0:
                ready.append(head)
    if seen != len(work.nodes):  # pragma: no cover - acyclic() guarantees this
        raise ValueError("ranking saw a cycle; the acyclic phase failed")


def _reduce_slack(work: _W, rounds: int) -> None:
    """Move nodes to the feasible rank minimising their weighted edge length.

    A node's feasible range is bounded by its predecessors and successors and
    their minlen. Within it the objective is piecewise linear in the rank, so
    the weighted median of the neighbour ranks is a minimiser; clamping it to
    the feasible range keeps it a legal move. The objective is a non-negative
    integer that strictly decreases on every accepted move, so this stops.
    """
    for _ in range(max(0, rounds)):
        improved = False
        for v, node in work.nodes.items():
            lo = -math.inf
            hi = math.inf
            weighted: list[tuple[int, float]] = []
            for i in work.inc[v]:
                tail, _, weight, minlen = work.edges[i]
                lo = max(lo, work.nodes[tail].rank + minlen)
                weighted.append((work.nodes[tail].rank, weight))
            for i in work.out[v]:
                _, head, weight, minlen = work.edges[i]
                hi = min(hi, work.nodes[head].rank - minlen)
                weighted.append((work.nodes[head].rank, weight))
            if not weighted:
                continue
            if lo == -math.inf:
                lo = min(rank for rank, _ in weighted)
            if hi == math.inf:
                hi = max(rank for rank, _ in weighted)
            if lo > hi:
                continue

            best = node.rank
            best_cost = _node_cost(node.rank, weighted)
            for candidate in _candidates(int(lo), int(hi), weighted):
                cost = _node_cost(candidate, weighted)
                if cost < best_cost:
                    best, best_cost = candidate, cost
            if best != node.rank:
                node.rank = best
                improved = True
        if not improved:
            break

    lowest = min(node.rank for node in work.nodes.values())
    for node in work.nodes.values():
        node.rank -= lowest


def _candidates(lo: int, hi: int, weighted: list[tuple[int, float]]) -> list[int]:
    """Ranks worth trying: the feasible neighbour ranks, plus the bounds.

    The objective's minimum over an interval is attained at a breakpoint, and
    every breakpoint is a neighbour's rank, so a full scan of the range is
    wasted work on a wide one.
    """
    inside = {rank for rank, _ in weighted if lo <= rank <= hi}
    inside.update((lo, hi))
    return sorted(inside)


def _node_cost(rank: int, weighted: list[tuple[int, float]]) -> float:
    return sum(weight * abs(rank - other) for other, weight in weighted)


def total_edge_length(work_or_graph: object) -> float:
    """Weighted sum of rank spans. The objective ranking is minimising."""
    if isinstance(work_or_graph, _W):
        return sum(
            weight * abs(work_or_graph.nodes[v].rank - work_or_graph.nodes[u].rank)
            for u, v, weight, _ in work_or_graph.edges
        )
    raise TypeError("total_edge_length wants the working graph")


# ------------------------------------------------------------- 3. normalise


def _normalise(work: _W, opts: LayeredOptions) -> dict[int, list[str]]:
    """Replace every edge spanning >1 rank with a chain of dummy nodes."""
    chains: dict[int, list[str]] = {}
    replacement: list[tuple[str, str, float, int]] = []
    for index, (u, v, weight, minlen) in enumerate(work.edges):
        span = work.nodes[v].rank - work.nodes[u].rank
        if span <= 1:
            replacement.append((u, v, weight, minlen))
            continue
        previous = u
        chain: list[str] = []
        for rank in range(work.nodes[u].rank + 1, work.nodes[v].rank):
            name = f"\x00d{index}:{rank}"
            work.nodes[name] = _N(
                id=name, w=opts.edgesep, h=0.0, dummy=True, edge=index, rank=rank
            )
            chain.append(name)
            replacement.append((previous, name, weight, 1))
            previous = name
        replacement.append((previous, v, weight, 1))
        chains[index] = chain
    work.edges = replacement
    work.index()
    return chains


# ----------------------------------------------------------------- 4. order


def _layering(work: _W) -> list[list[str]]:
    depth = max((n.rank for n in work.nodes.values()), default=0)
    layers: list[list[str]] = [[] for _ in range(depth + 1)]
    for name, node in work.nodes.items():
        layers[node.rank].append(name)
    for layer in layers:
        layer.sort(key=lambda name: work.nodes[name].order)
    return layers


def _init_order(work: _W) -> None:
    """Seed the order by breadth-first discovery from each rank-0 node.

    Discovery order beats insertion order because it puts a node next to the
    things it connects to before any sweep runs, which is where the sweeps
    start from.
    """
    counters: dict[int, int] = {}
    seen: set[str] = set()
    roots = sorted(
        work.nodes, key=lambda name: (work.nodes[name].rank, name)
    )
    for root in roots:
        if root in seen:
            continue
        queue = [root]
        seen.add(root)
        while queue:
            v = queue.pop(0)
            rank = work.nodes[v].rank
            work.nodes[v].order = counters.get(rank, 0)
            counters[rank] = counters.get(rank, 0) + 1
            adjacent = [work.edges[i][1] for i in work.out[v]]
            adjacent += [work.edges[i][0] for i in work.inc[v]]
            for other in adjacent:
                if other not in seen:
                    seen.add(other)
                    queue.append(other)


def cross_count(layers: list[list[str]], work: _W) -> float:
    """Weighted crossings, by Barth, Junger and Mutzel's accumulator tree."""
    total = 0.0
    for i in range(1, len(layers)):
        total += _bilayer_crossings(layers[i - 1], layers[i], work)
    return total


def _bilayer_crossings(north: list[str], south: list[str], work: _W) -> float:
    if not north or not south:
        return 0.0
    position = {name: i for i, name in enumerate(south)}
    entries: list[tuple[int, float]] = []
    for name in north:
        pairs = []
        for i in work.out[name]:
            _, head, weight, _ = work.edges[i]
            if head in position:
                pairs.append((position[head], weight))
        pairs.sort()
        entries.extend(pairs)
    if not entries:
        return 0.0

    first = 1
    while first < len(south):
        first <<= 1
    tree = [0.0] * (2 * first - 1)
    first -= 1

    crossings = 0.0
    for pos, weight in entries:
        index = pos + first
        tree[index] += weight
        above = 0.0
        while index > 0:
            if index % 2:
                above += tree[index + 1]
            index = (index - 1) // 2
            tree[index] += weight
        crossings += weight * above
    return crossings


def _barycentre(
    layer: list[str], fixed: list[str], work: _W, use_out: bool
) -> list[str]:
    position = {name: i for i, name in enumerate(fixed)}
    keyed: list[tuple[float, int, str]] = []
    for i, name in enumerate(layer):
        sources = work.out[name] if use_out else work.inc[name]
        places = []
        for edge_index in sources:
            other = work.edges[edge_index][1 if use_out else 0]
            if other in position:
                places.append(float(position[other]))
        # A node with no neighbour in the fixed layer keeps its place; moving
        # it can only churn the order without changing the crossing count.
        value = sum(places) / len(places) if places else float(i)
        keyed.append((value, i, name))
    keyed.sort()
    return [name for _, _, name in keyed]


def _order(work: _W, opts: LayeredOptions) -> list[list[str]]:
    _init_order(work)
    layers = _layering(work)
    best = [list(layer) for layer in layers]
    best_crossings = cross_count(layers, work)

    for sweep in range(max(1, opts.sweeps)):
        downward = sweep % 2 == 0
        indices = range(1, len(layers)) if downward else range(len(layers) - 2, -1, -1)
        for i in indices:
            fixed = layers[i - 1] if downward else layers[i + 1]
            layers[i] = _barycentre(layers[i], fixed, work, use_out=not downward)
        for layer in layers:
            for order, name in enumerate(layer):
                work.nodes[name].order = order
        crossings = cross_count(layers, work)
        if crossings < best_crossings:
            best_crossings = crossings
            best = [list(layer) for layer in layers]
        if best_crossings == 0:
            break

    for layer in best:
        for order, name in enumerate(layer):
            work.nodes[name].order = order
    return best


# -------------------------------------------------------------- 5. position


def _separation(work: _W, a: str, b: str, opts: LayeredOptions) -> float:
    left = work.nodes[a]
    right = work.nodes[b]
    gap = opts.edgesep if (left.dummy and right.dummy) else opts.nodesep
    return left.w / 2 + gap + right.w / 2


def _type1_conflicts(layers: list[list[str]], work: _W) -> set[tuple[str, str]]:
    """Mark crossings of a non-inner segment with an inner one.

    An inner segment joins two dummies: it is the straight middle of a long
    edge, and Brandes-Kopf keeps those straight by refusing to align anything
    that would cross one.
    """
    conflicts: set[tuple[str, str]] = set()
    for i in range(1, len(layers)):
        previous, layer = layers[i - 1], layers[i]
        if not layer:
            continue
        prev_pos = {name: j for j, name in enumerate(previous)}
        k0 = 0
        scan = 0
        for order, v in enumerate(layer):
            inner = _inner_partner(v, work, prev_pos)
            if inner is None and order != len(layer) - 1:
                continue
            k1 = len(previous) - 1 if inner is None else prev_pos[inner]
            while scan <= order:
                for u in _predecessors(layer[scan], work, prev_pos):
                    if prev_pos[u] < k0 or prev_pos[u] > k1:
                        conflicts.add((u, layer[scan]))
                        conflicts.add((layer[scan], u))
                scan += 1
            k0 = k1
    return conflicts


def _predecessors(v: str, work: _W, prev_pos: dict[str, int]) -> list[str]:
    return [
        work.edges[i][0]
        for i in work.inc[v]
        if work.edges[i][0] in prev_pos
    ]


def _inner_partner(v: str, work: _W, prev_pos: dict[str, int]) -> str | None:
    if not work.nodes[v].dummy:
        return None
    for u in _predecessors(v, work, prev_pos):
        if work.nodes[u].dummy:
            return u
    return None


def _vertical_alignment(
    layers: list[list[str]],
    work: _W,
    conflicts: set[tuple[str, str]],
    upward: bool,
) -> tuple[dict[str, str], dict[str, str]]:
    root = {v: v for layer in layers for v in layer}
    align = {v: v for layer in layers for v in layer}
    order = [pos for pos, _ in enumerate(layers)]
    sequence = order if upward else list(reversed(order))

    for index in sequence:
        layer = layers[index]
        neighbour_layer = layers[index - 1] if upward else (
            layers[index + 1] if index + 1 < len(layers) else []
        )
        if upward and index == 0:
            continue
        if not upward and index + 1 >= len(layers):
            continue
        position = {name: i for i, name in enumerate(neighbour_layer)}
        previous = -1
        for v in layer:
            candidates = (
                _predecessors(v, work, position)
                if upward
                else [
                    work.edges[i][1]
                    for i in work.out[v]
                    if work.edges[i][1] in position
                ]
            )
            if not candidates:
                continue
            candidates.sort(key=lambda name: position[name])
            middle = (len(candidates) - 1) / 2
            for pick in range(math.floor(middle), math.ceil(middle) + 1):
                w = candidates[pick]
                if (
                    align[v] == v
                    and previous < position[w]
                    and (v, w) not in conflicts
                ):
                    align[w] = v
                    root[v] = root[w]
                    align[v] = root[v]
                    previous = position[w]
    return root, align


def _compact(
    layers: list[list[str]],
    work: _W,
    root: dict[str, str],
    align: dict[str, str],
    opts: LayeredOptions,
) -> dict[str, float]:
    """Place each block at its leftmost feasible coordinate.

    The block graph has one node per block root and an edge between the roots
    of horizontally adjacent nodes, weighted by their required separation.
    Longest path from the left in that graph is the placement; it is walked
    with an explicit stack, because a long chain would otherwise be a deep
    recursion on a graph a user supplied.
    """
    block_edges: dict[str, list[tuple[str, float]]] = {
        root[v]: [] for layer in layers for v in layer
    }
    incoming: dict[str, list[tuple[str, float]]] = {key: [] for key in block_edges}
    for layer in layers:
        for i in range(1, len(layer)):
            left, right = layer[i - 1], layer[i]
            gap = _separation(work, left, right, opts)
            a, b = root[left], root[right]
            if a == b:
                continue
            block_edges[a].append((b, gap))
            incoming[b].append((a, gap))

    xs: dict[str, float] = {}
    visited: set[str] = set()
    for start in block_edges:
        if start in xs:
            continue
        stack = [start]
        while stack:
            current = stack[-1]
            if current in xs:
                stack.pop()
                continue
            pending = [u for u, _ in incoming[current] if u not in xs]
            if pending and current not in visited:
                visited.add(current)
                stack.extend(pending)
                continue
            stack.pop()
            xs[current] = max(
                (xs[u] + gap for u, gap in incoming[current] if u in xs),
                default=0.0,
            )

    return {v: xs[root[v]] for layer in layers for v in layer}


def _balance(assignments: list[dict[str, float]]) -> dict[str, float]:
    """Align the four candidate assignments and average the middle two."""
    if not assignments:
        return {}
    widths = []
    for xs in assignments:
        values = list(xs.values())
        widths.append((max(values) - min(values), min(values), max(values)))
    narrowest = min(range(len(widths)), key=lambda i: widths[i][0])
    target_lo, target_hi = widths[narrowest][1], widths[narrowest][2]

    aligned: list[dict[str, float]] = []
    for index, xs in enumerate(assignments):
        # The "right" alignments were mirrored, so they align by their high
        # edge; the "left" ones by their low edge. Aligning both the same way
        # would systematically bias the average toward one side.
        shift = (
            target_lo - widths[index][1]
            if index % 2 == 0
            else target_hi - widths[index][2]
        )
        aligned.append({v: x + shift for v, x in xs.items()})

    balanced: dict[str, float] = {}
    for v in assignments[0]:
        values = sorted(xs[v] for xs in aligned)
        balanced[v] = (values[1] + values[2]) / 2
    return balanced


def _position_x(layers: list[list[str]], work: _W, opts: LayeredOptions) -> dict[str, float]:
    conflicts = _type1_conflicts(layers, work)
    assignments: list[dict[str, float]] = []
    for upward in (True, False):
        for rightward in (False, True):
            used = [list(reversed(layer)) if rightward else list(layer) for layer in layers]
            root, align = _vertical_alignment(used, work, conflicts, upward)
            xs = _compact(used, work, root, align, opts)
            if rightward:
                xs = {v: -x for v, x in xs.items()}
            assignments.append(xs)
    # Order the list so mirrored assignments land on odd indices, which is
    # what _balance's alignment rule assumes.
    ordered = [assignments[0], assignments[1], assignments[2], assignments[3]]
    return _balance(ordered)


# ------------------------------------------------------------------- driver


def layered(graph: Graph, opts: LayeredOptions | None = None) -> Graph:
    """Lay a graph out in ranks. Returns the same graph, positioned."""
    options = opts or LayeredOptions()
    graph.measure()
    if not graph.nodes:
        return graph

    work = _W()
    horizontal = options.direction in ("LR", "RL")
    for node in graph.nodes.values():
        # Layout always works top-to-bottom; a left-to-right drawing is the
        # same layout with the axes exchanged, so the sizes are exchanged
        # going in and the coordinates exchanged coming out. One code path.
        w, h = (node.h, node.w) if horizontal else (node.w, node.h)
        work.nodes[node.id] = _N(id=node.id, w=w, h=h)

    self_loops: list[int] = []
    arcs: list[tuple[str, str, float]] = []
    live: list[int] = []
    for index, edge in enumerate(graph.edges):
        if edge.tail == edge.head:
            self_loops.append(index)
            continue
        weight = float(edge.attrs.get("weight", 1.0))
        arcs.append((edge.tail, edge.head, weight))
        live.append(index)

    reversed_arcs = _greedy_fas(list(work.nodes), arcs)
    for position, (tail, head, weight) in enumerate(arcs):
        minlen = int(graph.edges[live[position]].attrs.get("minlen", 1))
        if position in reversed_arcs:
            graph.edges[live[position]].reversed = True
            work.edges.append((head, tail, weight, minlen))
        else:
            work.edges.append((tail, head, weight, minlen))
    work.index()

    _longest_path(work)
    _reduce_slack(work, options.rank_rounds)
    chains = _normalise(work, options)
    layers = _order(work, options)

    xs = _position_x(layers, work, options)
    y = 0.0
    for layer in layers:
        tallest = max((work.nodes[v].h for v in layer), default=0.0)
        for v in layer:
            work.nodes[v].x = xs.get(v, 0.0)
            work.nodes[v].y = y + tallest / 2
        y += tallest + options.ranksep

    _writeback(graph, work, chains, live, options, horizontal)
    _place_self_loops(graph, self_loops, options)
    _place_clusters(graph)
    graph.normalise()
    return graph


def _writeback(
    graph: Graph,
    work: _W,
    chains: dict[int, list[str]],
    live: list[int],
    opts: LayeredOptions,
    horizontal: bool,
) -> None:
    def out(name: str) -> tuple[float, float]:
        node = work.nodes[name]
        return (node.y, node.x) if horizontal else (node.x, node.y)

    for node in graph.nodes.values():
        node.x, node.y = out(node.id)

    for position, edge_index in enumerate(live):
        edge = graph.edges[edge_index]
        points = [out(name) for name in chains.get(position, [])]
        tail, head = graph.nodes[edge.tail], graph.nodes[edge.head]
        if edge.reversed:
            points.reverse()
        edge.points = _clip_route(
            [(tail.x, tail.y), *points, (head.x, head.y)], tail, head
        )

    if opts.direction in ("BT", "RL"):
        _, _, max_x, max_y = graph.bounds()
        for node in graph.nodes.values():
            if opts.direction == "BT":
                node.y = max_y - node.y
            else:
                node.x = max_x - node.x
        for edge in graph.edges:
            edge.points = [
                (px, max_y - py) if opts.direction == "BT" else (max_x - px, py)
                for px, py in edge.points
            ]


def _clip_route(points: list[tuple[float, float]], tail: object, head: object) -> list[tuple[float, float]]:
    """Trim the first and last segment to the node boundaries."""
    from ..route import clip_to_box  # local import: route imports the model

    if len(points) < 2:
        return points
    start = clip_to_box(points[1], points[0], tail)  # type: ignore[arg-type]
    end = clip_to_box(points[-2], points[-1], head)  # type: ignore[arg-type]
    return [start, *points[1:-1], end]


def _place_self_loops(graph: Graph, indices: list[int], opts: LayeredOptions) -> None:
    """A self-loop is a lobe on the node's right side.

    Several loops on one node are nested, so two are two visible lobes rather
    than one drawn twice.
    """
    seen: dict[str, int] = {}
    for index in indices:
        edge = graph.edges[index]
        node = graph.nodes[edge.tail]
        depth = seen.get(edge.tail, 0)
        seen[edge.tail] = depth + 1
        reach = node.w / 2 + opts.nodesep * (0.5 + depth * 0.4)
        top = node.y - node.h / 4
        bottom = node.y + node.h / 4
        edge.points = [
            (node.x + node.w / 2, top),
            (node.x + reach, top - node.h / 4),
            (node.x + reach, bottom + node.h / 4),
            (node.x + node.w / 2, bottom),
        ]


def _place_clusters(graph: Graph) -> None:
    """Box each cluster around its members, after positioning."""
    pad = 20.0
    for cluster in graph.clusters.values():
        members = [n for n in graph.nodes.values() if n.cluster == cluster.id]
        if not members:
            cluster.w = cluster.h = 0.0
            continue
        x0 = min(n.x - n.w / 2 for n in members) - pad
        y0 = min(n.y - n.h / 2 for n in members) - pad
        x1 = max(n.x + n.w / 2 for n in members) + pad
        y1 = max(n.y + n.h / 2 for n in members) + pad
        cluster.x, cluster.y = x0, y0
        cluster.w, cluster.h = x1 - x0, y1 - y0
