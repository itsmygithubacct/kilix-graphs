"""kilix-graphs: graph creation and rendering for Kilix, Pleb and Plebian-OS.

Two surfaces over one core. Node-link drawings — the graph-drawing field
proper — and charts both build a `scene`, and both renderers consume one.

    import kilix_graphs as kg

    graph = kg.parse.loads("a -> b -> c\\nb -> d")
    kg.layout.run(graph, "layered")
    print(kg.render_text(kg.compose(graph)))
"""

from __future__ import annotations

from . import chart, layout, model, parse, render, route, scene, theme
from .compose import ComposeOptions, compose
from .model import Edge, Graph, Node, Shape
from .render import render_svg, render_text
from .scene import Scene
from .theme import DARK, LIGHT, Theme

__all__ = [
    "ComposeOptions",
    "DARK",
    "Edge",
    "Graph",
    "LIGHT",
    "Node",
    "Scene",
    "Shape",
    "Theme",
    "__version__",
    "chart",
    "compose",
    "layout",
    "model",
    "parse",
    "render",
    "render_svg",
    "render_text",
    "route",
    "scene",
    "theme",
]

__version__ = "0.1.0"
