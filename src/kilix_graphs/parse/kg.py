"""The `.kg` syntax: what you actually want to type.

DOT is the interchange format and nobody enjoys writing it. `.kg` is a
line-oriented syntax in the mermaid and D2 tradition, where the common case —
one edge — is one line with no punctuation to remember::

    # a comment
    digraph Pipeline

    capture -> encode -> present
    encode -> disk: spill
    disk {shape=cylinder}

    group storage: Storage
        disk
        index

Rules, all of them:

- ``#`` starts a comment; blank lines are ignored.
- A first line of ``graph`` or ``digraph``, optionally followed by a name,
  sets the direction. Without one the graph is directed.
- ``a -> b`` is a directed edge, ``a -- b`` an undirected one, and either may
  chain: ``a -> b -> c``.
- A trailing ``: text`` labels what precedes it — the edge on an edge line,
  the node on a bare node line.
- ``{key=value, key=value}`` sets attributes on whatever precedes it. On a
  node line ``label`` and ``shape`` are lifted onto the node.
- ``group id: Label`` opens a cluster; lines indented under it are its
  members. Groups nest by indentation.
- An identifier is anything without whitespace, ``:``, ``{`` or an arrow; put
  it in double quotes if it needs one of those.
"""

from __future__ import annotations

import re

from ..model import Attrs, Graph, Shape

__all__ = ["KgSyntaxError", "loads"]


class KgSyntaxError(ValueError):
    def __init__(self, message: str, line: int) -> None:
        super().__init__(f"line {line}: {message}")
        self.line = line


_ARROW = re.compile(r"\s*(->|--)\s*")
_ATTRS = re.compile(r"\{([^}]*)\}\s*$")
_QUOTED = re.compile(r'"((?:[^"\\]|\\.)*)"')

_SHAPES = {
    "box": Shape.BOX,
    "rect": Shape.BOX,
    "round": Shape.ROUND,
    "rounded": Shape.ROUND,
    "ellipse": Shape.ELLIPSE,
    "oval": Shape.ELLIPSE,
    "circle": Shape.CIRCLE,
    "diamond": Shape.DIAMOND,
    "point": Shape.POINT,
    "dot": Shape.POINT,
    "cylinder": Shape.ROUND,
}


def _unquote(text: str) -> str:
    text = text.strip()
    match = _QUOTED.fullmatch(text)
    if match:
        return match.group(1).replace('\\"', '"').replace("\\\\", "\\")
    return text


def _split_label(text: str) -> tuple[str, str]:
    """Split a trailing ``: label`` off, respecting quotes.

    A colon inside quotes belongs to the identifier, which is what makes
    ``"a:b" -> c`` work.
    """
    depth = 0
    in_quote = False
    escaped = False
    for index, char in enumerate(text):
        if escaped:
            escaped = False
            continue
        if char == "\\":
            escaped = True
        elif char == '"':
            in_quote = not in_quote
        elif in_quote:
            continue
        elif char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
        elif char == ":" and depth == 0:
            return text[:index].strip(), text[index + 1 :].strip()
    return text.strip(), ""


def _parse_attrs(text: str, line: int) -> tuple[str, Attrs]:
    match = _ATTRS.search(text)
    if not match:
        return text.strip(), {}
    attrs: Attrs = {}
    body = match.group(1)
    for piece in body.split(","):
        piece = piece.strip()
        if not piece:
            continue
        if "=" not in piece:
            raise KgSyntaxError(f"attribute {piece!r} needs a value", line)
        key, value = piece.split("=", 1)
        attrs[key.strip()] = _unquote(value)
    return text[: match.start()].strip(), attrs


def loads(source: str) -> Graph:
    """Parse `.kg` source into a graph."""
    graph = Graph()
    directed_seen = False
    # (indent column, cluster id)
    stack: list[tuple[int, str]] = []
    group_number = 0

    for number, raw in enumerate(source.splitlines(), start=1):
        without_comment = raw.split("#", 1)[0] if not _in_quotes_hash(raw) else raw
        stripped = without_comment.strip()
        if not stripped:
            continue
        indent = len(without_comment) - len(without_comment.lstrip())

        while stack and indent <= stack[-1][0]:
            stack.pop()

        head = stripped.split(None, 1)
        keyword = head[0].lower()

        if not directed_seen and keyword in ("graph", "digraph") and not stack:
            graph.directed = keyword == "digraph"
            if len(head) > 1:
                graph.name = _unquote(head[1])
            directed_seen = True
            continue

        if keyword == "group":
            rest = head[1] if len(head) > 1 else ""
            name, label = _split_label(rest)
            name = _unquote(name)
            if not name:
                group_number += 1
                name = f"group{group_number}"
            parent = stack[-1][1] if stack else None
            graph.cluster(name, label=label or name, parent=parent)
            stack.append((indent, name))
            continue

        directed_seen = True
        body, label = _split_label(stripped)
        body, attrs = _parse_attrs(body, number)
        if not body:
            raise KgSyntaxError("nothing before the label", number)

        parts = _ARROW.split(body)
        if len(parts) == 1:
            name = _unquote(parts[0])
            node = graph.node(name, attrs=attrs)
            if label:
                node.label = label
            if "label" in attrs:
                node.label = attrs["label"]
            if "shape" in attrs:
                node.shape = _SHAPES.get(attrs["shape"], Shape.BOX)
            if stack:
                node.cluster = stack[-1][1]
            continue

        names = [_unquote(part) for part in parts[0::2]]
        operators = parts[1::2]
        if any(not name for name in names):
            raise KgSyntaxError("an arrow is missing an endpoint", number)
        for name in names:
            node = graph.node(name)
            if stack and node.cluster is None:
                node.cluster = stack[-1][1]
        for index, operator in enumerate(operators):
            merged = dict(attrs)
            if operator == "--":
                merged.setdefault("dir", "none")
            edge = graph.edge(names[index], names[index + 1], attrs=merged)
            edge.label = label or merged.get("label", "")

    return graph


def _in_quotes_hash(line: str) -> bool:
    """True when every ``#`` in the line sits inside quotes."""
    if "#" not in line:
        return False
    in_quote = False
    escaped = False
    for char in line:
        if escaped:
            escaped = False
            continue
        if char == "\\":
            escaped = True
        elif char == '"':
            in_quote = not in_quote
        elif char == "#" and not in_quote:
            return False
    return True
