"""Force-directed layout for graphs with no hierarchy to show.

The schedule follows d3-force, which is ISC and the clearest formulation of
this simulation anywhere:

- repulsion is n-body through a Barnes-Hut quadtree with theta^2 = 0.81, so a
  distant cluster costs one interaction instead of one per member;
- links are springs with a strength that falls off with the endpoint degrees,
  so a hub does not drag its leaves into a knot;
- the temperature decays geometrically from 1 to 0.001 over 300 ticks and
  velocity keeps 60% of itself each tick;
- initial positions are a phyllotaxis spiral, not random. That one detail
  removes a whole class of degenerate starts: no two nodes coincide, so no
  repulsion is undefined, and the layout is deterministic without a seed.

Node overlap is resolved afterwards by a bounded collision pass rather than by
constraint solving. Constrained stress majorization (WebCola's VPSC, MIT) is
the published upgrade when overlap-free is a requirement rather than a
preference.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from ..model import Graph
from ..route import route_straight

__all__ = ["ForceOptions", "force"]

_PHI = math.pi * (3 - math.sqrt(5))
_INITIAL_RADIUS = 10.0


@dataclass
class ForceOptions:
    ticks: int = 300
    #: Repulsion strength. Negative repels, which is d3's sign convention.
    charge: float = -220.0
    #: Preferred link length, before the size of the endpoints is added.
    link_distance: float = 90.0
    link_strength: float = 0.7
    #: Pull toward the centre, which keeps disconnected pieces from drifting.
    gravity: float = 0.06
    velocity_decay: float = 0.4
    theta: float = 0.9
    collide_passes: int = 4
    collide_pad: float = 8.0


@dataclass
class _P:
    x: float
    y: float
    vx: float = 0.0
    vy: float = 0.0
    w: float = 0.0
    h: float = 0.0


class _Quad:
    """A Barnes-Hut quadtree node: either four children or a list of bodies."""

    __slots__ = ("x0", "y0", "x1", "y1", "children", "bodies", "mass", "cx", "cy")

    def __init__(self, x0: float, y0: float, x1: float, y1: float) -> None:
        self.x0, self.y0, self.x1, self.y1 = x0, y0, x1, y1
        self.children: list[_Quad] | None = None
        self.bodies: list[_P] = []
        self.mass = 0.0
        self.cx = 0.0
        self.cy = 0.0

    def insert(self, body: _P, depth: int = 0) -> None:
        # Coincident bodies would subdivide forever; the depth cap turns that
        # into a shared leaf, which the repulsion pass handles with a jiggle.
        if self.children is None:
            self.bodies.append(body)
            if len(self.bodies) <= 1 or depth >= 20:
                return
            self._split(depth)
            return
        self._child_for(body).insert(body, depth + 1)

    def _split(self, depth: int) -> None:
        mx = (self.x0 + self.x1) / 2
        my = (self.y0 + self.y1) / 2
        self.children = [
            _Quad(self.x0, self.y0, mx, my),
            _Quad(mx, self.y0, self.x1, my),
            _Quad(self.x0, my, mx, self.y1),
            _Quad(mx, my, self.x1, self.y1),
        ]
        moving, self.bodies = self.bodies, []
        for body in moving:
            self._child_for(body).insert(body, depth + 1)

    def _child_for(self, body: _P) -> "_Quad":
        assert self.children is not None
        mx = (self.x0 + self.x1) / 2
        my = (self.y0 + self.y1) / 2
        index = (1 if body.x >= mx else 0) + (2 if body.y >= my else 0)
        return self.children[index]

    def accumulate(self) -> None:
        if self.children is None:
            self.mass = float(len(self.bodies))
            if self.mass:
                self.cx = sum(b.x for b in self.bodies) / self.mass
                self.cy = sum(b.y for b in self.bodies) / self.mass
            return
        mass = 0.0
        cx = cy = 0.0
        for child in self.children:
            child.accumulate()
            if child.mass:
                mass += child.mass
                cx += child.cx * child.mass
                cy += child.cy * child.mass
        self.mass = mass
        if mass:
            self.cx, self.cy = cx / mass, cy / mass


def _repel(quad: _Quad, body: _P, charge: float, theta2: float, alpha: float) -> None:
    if quad.mass == 0.0:
        return
    dx = quad.cx - body.x
    dy = quad.cy - body.y
    d2 = dx * dx + dy * dy

    width = quad.x1 - quad.x0
    if quad.children is not None and width * width / max(d2, 1e-9) > theta2:
        for child in quad.children:
            _repel(child, body, charge, theta2, alpha)
        return

    if quad.children is None and body in quad.bodies and quad.mass <= 1.0:
        return
    if d2 < 1.0:
        # Coincident or near-coincident bodies: push along a deterministic
        # direction rather than dividing by zero or picking a random one.
        dx, dy, d2 = 1.0, 0.0, 1.0
    force = charge * quad.mass * alpha / d2
    length = math.sqrt(d2)
    body.vx += dx / length * force
    body.vy += dy / length * force


def force(graph: Graph, opts: ForceOptions | None = None) -> Graph:
    options = opts or ForceOptions()
    graph.measure()
    names = list(graph.nodes)
    if not names:
        return graph

    bodies: dict[str, _P] = {}
    for index, name in enumerate(names):
        radius = _INITIAL_RADIUS * math.sqrt(0.5 + index)
        angle = index * _PHI
        node = graph.nodes[name]
        bodies[name] = _P(
            x=radius * math.cos(angle),
            y=radius * math.sin(angle),
            w=node.w,
            h=node.h,
        )

    degree = {name: 0 for name in names}
    links: list[tuple[str, str]] = []
    for edge in graph.edges:
        if edge.tail == edge.head:
            continue
        links.append((edge.tail, edge.head))
        degree[edge.tail] += 1
        degree[edge.head] += 1

    alpha = 1.0
    alpha_min = 0.001
    decay = 1 - alpha_min ** (1 / max(1, options.ticks))
    theta2 = options.theta * options.theta
    keep = 1 - options.velocity_decay

    for _ in range(options.ticks):
        if alpha < alpha_min:
            break
        alpha += (0.0 - alpha) * decay

        xs = [b.x for b in bodies.values()]
        ys = [b.y for b in bodies.values()]
        pad = 1.0
        tree = _Quad(min(xs) - pad, min(ys) - pad, max(xs) + pad, max(ys) + pad)
        for body in bodies.values():
            tree.insert(body)
        tree.accumulate()
        for body in bodies.values():
            _repel(tree, body, options.charge, theta2, alpha)

        for tail, head in links:
            a, b = bodies[tail], bodies[head]
            dx, dy = b.x - a.x, b.y - a.y
            distance = math.hypot(dx, dy) or 1e-6
            rest = options.link_distance + (a.w + b.w) / 4
            # d3's bias: the endpoint with the higher degree moves less, so a
            # hub stays put and its leaves arrange around it.
            total = degree[tail] + degree[head]
            bias = degree[tail] / total if total else 0.5
            push = (distance - rest) / distance * alpha * options.link_strength
            b.vx -= dx * push * (1 - bias)
            b.vy -= dy * push * (1 - bias)
            a.vx += dx * push * bias
            a.vy += dy * push * bias

        for body in bodies.values():
            body.vx -= body.x * options.gravity * alpha
            body.vy -= body.y * options.gravity * alpha
            body.vx *= keep
            body.vy *= keep
            body.x += body.vx
            body.y += body.vy

    _separate(bodies, options)

    for name, body in bodies.items():
        graph.nodes[name].x = body.x
        graph.nodes[name].y = body.y
    route_straight(graph)
    graph.normalise()
    return graph


def _separate(bodies: dict[str, _P], options: ForceOptions) -> None:
    """Push overlapping boxes apart, a bounded number of times.

    This is relaxation, not a solver: it makes overlap rare rather than
    impossible, and says so. A dense graph can still end with a pair touching.
    """
    names = list(bodies)
    for _ in range(max(0, options.collide_passes)):
        moved = False
        for i in range(len(names)):
            for j in range(i + 1, len(names)):
                a, b = bodies[names[i]], bodies[names[j]]
                min_x = (a.w + b.w) / 2 + options.collide_pad
                min_y = (a.h + b.h) / 2 + options.collide_pad
                dx = b.x - a.x
                dy = b.y - a.y
                overlap_x = min_x - abs(dx)
                overlap_y = min_y - abs(dy)
                if overlap_x <= 0 or overlap_y <= 0:
                    continue
                # Separate along the axis needing the smaller correction.
                if overlap_x < overlap_y:
                    shift = overlap_x / 2 * (1 if dx >= 0 else -1)
                    a.x -= shift
                    b.x += shift
                else:
                    shift = overlap_y / 2 * (1 if dy >= 0 else -1)
                    a.y -= shift
                    b.y += shift
                moved = True
        if not moved:
            break
