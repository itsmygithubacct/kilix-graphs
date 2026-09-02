"""JSON Graph Format: the machine-readable input and output.

JGF is a small, boring, complete interchange schema (Apache-2.0), which is
exactly what a machine format should be. It is adopted here rather than
invented, so anything that already speaks JGF can drive `kilix-graphs` and
`kilix-graphs` can feed anything that reads it.

Both the single-graph (``{"graph": {...}}``) and multi-graph
(``{"graphs": [...]}``) envelopes are read; the first graph is used. Writing
always produces the single-graph form.
"""

from __future__ import annotations

import json
from typing import Any

from ..model import Graph, Shape

__all__ = ["dumps", "loads"]

_SHAPES = {name: name for name in Shape.ALL}


def loads(source: str) -> Graph:
    """Parse JSON Graph Format into a graph."""
    try:
        document = json.loads(source)
    except json.JSONDecodeError as error:
        raise ValueError(f"not valid JSON: {error}") from error
    if not isinstance(document, dict):
        raise ValueError("a JGF document is an object")

    body = document.get("graph")
    if body is None:
        graphs = document.get("graphs")
        if isinstance(graphs, list) and graphs:
            body = graphs[0]
    if body is None:
        # A bare {nodes, edges} object is not JGF, but it is what everyone
        # writes by hand, and refusing it helps nobody.
        body = document
    if not isinstance(body, dict):
        raise ValueError("the graph must be an object")

    graph = Graph(
        name=str(body.get("label") or body.get("id") or ""),
        directed=bool(body.get("directed", True)),
    )
    metadata = body.get("metadata")
    if isinstance(metadata, dict):
        graph.attrs.update({k: str(v) for k, v in metadata.items()})

    nodes = body.get("nodes")
    if isinstance(nodes, dict):
        pairs: list[tuple[str, Any]] = list(nodes.items())
    elif isinstance(nodes, list):
        pairs = [(str(item.get("id", index)), item) for index, item in enumerate(nodes)]
    else:
        pairs = []

    for name, payload in pairs:
        attrs: dict[str, str] = {}
        label = ""
        shape = Shape.ROUND
        if isinstance(payload, dict):
            label = str(payload.get("label", "") or "")
            meta = payload.get("metadata")
            if isinstance(meta, dict):
                attrs = {k: str(v) for k, v in meta.items()}
                shape = _SHAPES.get(str(meta.get("shape", "")), Shape.ROUND)
        graph.node(name, label=label, shape=shape, attrs=attrs)

    for item in body.get("edges") or []:
        if not isinstance(item, dict):
            continue
        tail = item.get("source")
        head = item.get("target")
        if tail is None or head is None:
            continue
        attrs = {}
        meta = item.get("metadata")
        if isinstance(meta, dict):
            attrs = {k: str(v) for k, v in meta.items()}
        if item.get("directed") is False:
            attrs.setdefault("dir", "none")
        graph.edge(
            str(tail),
            str(head),
            label=str(item.get("label", "") or item.get("relation", "") or ""),
            attrs=attrs,
        )

    return graph


def dumps(graph: Graph, *, indent: int | None = 2, positions: bool = False) -> str:
    """Serialise a graph as JSON Graph Format.

    With ``positions`` the laid-out coordinates and routes go into each
    element's ``metadata``, which makes the layout itself inspectable and
    diffable without a renderer in the way.
    """
    nodes: dict[str, Any] = {}
    for node in graph.nodes.values():
        metadata: dict[str, Any] = dict(node.attrs)
        metadata["shape"] = node.shape
        if node.cluster:
            metadata["cluster"] = node.cluster
        if positions:
            metadata.update(
                {"x": node.x, "y": node.y, "w": node.w, "h": node.h}
            )
        entry: dict[str, Any] = {"metadata": metadata}
        if node.label:
            entry["label"] = node.label
        nodes[node.id] = entry

    edges: list[dict[str, Any]] = []
    for edge in graph.edges:
        metadata = dict(edge.attrs)
        if positions and edge.points:
            metadata["points"] = [list(point) for point in edge.points]
        entry = {"source": edge.tail, "target": edge.head}
        if edge.label:
            entry["label"] = edge.label
        if edge.attrs.get("dir") == "none":
            entry["directed"] = False
        if metadata:
            entry["metadata"] = metadata
        edges.append(entry)

    document = {
        "graph": {
            "label": graph.name,
            "directed": graph.directed,
            "metadata": dict(graph.attrs),
            "nodes": nodes,
            "edges": edges,
        }
    }
    return json.dumps(document, indent=indent, sort_keys=False)
