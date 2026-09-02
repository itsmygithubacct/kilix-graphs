"""A DOT parser for the subset that matters.

DOT is the only graph format with genuine ubiquity, so reading it is table
stakes. The grammar is a published specification, not code, and this is an
independent implementation of it.

Supported: ``graph``/``digraph``/``strict``, node and edge statements, chains
(``a -> b -> c``), attribute lists, the ``graph[]``/``node[]``/``edge[]``
default blocks, ``subgraph cluster_x { ... }``, quoted and numeric IDs, line
continuations inside quoted strings, ``//``, ``#`` and ``/* */`` comments, and
ports (parsed and discarded, because nothing downstream places an edge at a
named port yet).

Not supported: HTML-like ``<...>`` labels, and edges whose endpoint is a whole
subgraph (``{a b} -> c``), which expand to a cross product this model has no
compact way to hold. Both raise rather than parsing to something wrong.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ..model import Attrs, Graph, Shape

__all__ = ["DotSyntaxError", "loads"]


class DotSyntaxError(ValueError):
    """Raised with a line and column when the source does not parse."""

    def __init__(self, message: str, line: int, column: int) -> None:
        super().__init__(f"line {line}, column {column}: {message}")
        self.line = line
        self.column = column


@dataclass(frozen=True)
class _Token:
    kind: str  # "id", "punct", "edgeop", "eof"
    value: str
    line: int
    column: int


_PUNCT = set("{}[];,=:")


def _tokenize(source: str) -> list[_Token]:
    tokens: list[_Token] = []
    index = 0
    line = 1
    column = 1
    size = len(source)

    def advance(count: int) -> None:
        nonlocal index, line, column
        for _ in range(count):
            if index < size and source[index] == "\n":
                line += 1
                column = 1
            else:
                column += 1
            index += 1

    while index < size:
        char = source[index]
        if char in " \t\r\n":
            advance(1)
            continue
        if source.startswith("/*", index):
            end = source.find("*/", index + 2)
            if end < 0:
                raise DotSyntaxError("unterminated /* comment", line, column)
            advance(end + 2 - index)
            continue
        if source.startswith("//", index) or char == "#":
            end = source.find("\n", index)
            advance((size if end < 0 else end) - index)
            continue
        if source.startswith("->", index) or source.startswith("--", index):
            tokens.append(_Token("edgeop", source[index : index + 2], line, column))
            advance(2)
            continue
        if char in _PUNCT:
            tokens.append(_Token("punct", char, line, column))
            advance(1)
            continue
        if char == '"':
            start_line, start_column = line, column
            advance(1)
            parts: list[str] = []
            while True:
                if index >= size:
                    raise DotSyntaxError(
                        "unterminated quoted string", start_line, start_column
                    )
                current = source[index]
                if current == "\\" and index + 1 < size:
                    following = source[index + 1]
                    # DOT's own escapes: a backslash-newline is a line
                    # continuation and disappears; \" is a literal quote;
                    # anything else keeps the backslash, because \l and \n are
                    # meaningful to the label, not to the lexer.
                    if following == "\n":
                        advance(2)
                        continue
                    if following == '"':
                        parts.append('"')
                        advance(2)
                        continue
                    parts.append(current)
                    advance(1)
                    continue
                if current == '"':
                    advance(1)
                    break
                parts.append(current)
                advance(1)
            tokens.append(_Token("id", "".join(parts), start_line, start_column))
            continue
        if char == "<":
            raise DotSyntaxError(
                "HTML-like labels are not supported", line, column
            )
        if char.isalnum() or char in "_.-+":
            start = index
            start_line, start_column = line, column
            while index < size and (
                source[index].isalnum() or source[index] in "_.-+"
            ):
                advance(1)
            tokens.append(
                _Token("id", source[start:index], start_line, start_column)
            )
            continue
        raise DotSyntaxError(f"unexpected character {char!r}", line, column)

    tokens.append(_Token("eof", "", line, column))
    return tokens


@dataclass
class _Defaults:
    node: Attrs = field(default_factory=dict)
    edge: Attrs = field(default_factory=dict)

    def copy(self) -> "_Defaults":
        return _Defaults(node=dict(self.node), edge=dict(self.edge))


class _Parser:
    def __init__(self, tokens: list[_Token]) -> None:
        self.tokens = tokens
        self.at = 0
        self.graph = Graph()
        self.cluster_stack: list[str] = []
        self.anonymous = 0

    # ------------------------------------------------------------- helpers

    @property
    def token(self) -> _Token:
        return self.tokens[self.at]

    def take(self) -> _Token:
        token = self.tokens[self.at]
        if token.kind != "eof":
            self.at += 1
        return token

    def accept(self, kind: str, value: str | None = None) -> _Token | None:
        token = self.token
        if token.kind != kind:
            return None
        if value is not None and token.value.lower() != value:
            return None
        return self.take()

    def expect(self, kind: str, value: str | None = None) -> _Token:
        token = self.accept(kind, value)
        if token is None:
            wanted = value or kind
            found = self.token.value or self.token.kind
            raise DotSyntaxError(
                f"expected {wanted!r}, found {found!r}",
                self.token.line,
                self.token.column,
            )
        return token

    # -------------------------------------------------------------- parsing

    def parse(self) -> Graph:
        self.accept("id", "strict")
        kind = self.take()
        if kind.kind != "id" or kind.value.lower() not in ("graph", "digraph"):
            raise DotSyntaxError(
                "expected 'graph' or 'digraph'", kind.line, kind.column
            )
        self.graph.directed = kind.value.lower() == "digraph"
        name = self.accept("id")
        if name is not None:
            self.graph.name = name.value
        self.expect("punct", "{")
        self.statements(_Defaults())
        self.expect("punct", "}")
        if self.token.kind != "eof":
            raise DotSyntaxError(
                f"trailing input {self.token.value!r}",
                self.token.line,
                self.token.column,
            )
        return self.graph

    def statements(self, defaults: _Defaults) -> None:
        while True:
            if self.token.kind == "eof" or (
                self.token.kind == "punct" and self.token.value == "}"
            ):
                return
            self.statement(defaults)
            self.accept("punct", ";")

    def statement(self, defaults: _Defaults) -> None:
        token = self.token

        if token.kind == "punct" and token.value == "{":
            self.subgraph(defaults, name=None)
            return

        if token.kind == "id" and token.value.lower() == "subgraph":
            self.take()
            label = self.accept("id")
            self.subgraph(defaults, name=label.value if label else None)
            return

        if token.kind == "id" and token.value.lower() in ("graph", "node", "edge"):
            following = self.tokens[self.at + 1]
            if following.kind == "punct" and following.value == "[":
                self.take()
                self.expect("punct", "[")   # attr_list starts inside the list
                attrs = self.attr_list()
                if token.value.lower() == "node":
                    defaults.node.update(attrs)
                elif token.value.lower() == "edge":
                    defaults.edge.update(attrs)
                else:
                    self.graph.attrs.update(attrs)
                return

        if token.kind != "id":
            raise DotSyntaxError(
                f"unexpected {token.value!r}", token.line, token.column
            )

        first = self.node_id()
        if self.accept("punct", "=") is not None:
            value = self.expect("id")
            self.graph.attrs[first] = value.value
            return

        chain = [first]
        operators: list[str] = []
        while self.token.kind == "edgeop":
            operators.append(self.take().value)
            if self.token.kind == "punct" and self.token.value == "{":
                raise DotSyntaxError(
                    "an edge to a whole subgraph is not supported",
                    self.token.line,
                    self.token.column,
                )
            chain.append(self.node_id())

        attrs = self.attr_list() if self.accept("punct", "[") else {}

        if not operators:
            self.apply_node(first, {**defaults.node, **attrs})
            return

        for name in chain:
            # Defaults apply to a node the edge *creates*, not to one that was
            # already declared: `a [shape=point]` followed by `a -> b` must
            # leave a as a point. Re-applying them here reset every explicitly
            # shaped node the moment an edge mentioned it.
            if name in self.graph.nodes:
                if self.cluster_stack and self.graph.nodes[name].cluster is None:
                    self.graph.nodes[name].cluster = self.cluster_stack[-1]
                continue
            self.apply_node(name, dict(defaults.node))
        for index in range(len(operators)):
            merged = {**defaults.edge, **attrs}
            if operators[index] == "--":
                merged.setdefault("dir", "none")
            edge = self.graph.edge(chain[index], chain[index + 1], attrs=merged)
            edge.label = merged.get("label", "")

    def subgraph(self, defaults: _Defaults, name: str | None) -> None:
        self.expect("punct", "{")
        if name is None:
            self.anonymous += 1
            name = f"_anon{self.anonymous}"
        is_cluster = name.startswith("cluster")
        if is_cluster:
            parent = self.cluster_stack[-1] if self.cluster_stack else None
            self.graph.cluster(name, parent=parent)
            self.cluster_stack.append(name)
        self.statements(defaults.copy())
        if is_cluster:
            self.cluster_stack.pop()
        self.expect("punct", "}")

    def node_id(self) -> str:
        name = self.expect("id").value
        # Ports: `a:north` and `a:north:s`. Parsed so the rest of the
        # statement still reads, then discarded.
        while self.accept("punct", ":") is not None:
            self.expect("id")
        return name

    def attr_list(self) -> Attrs:
        attrs: Attrs = {}
        while True:
            while True:
                if self.accept("punct", "]") is not None:
                    break
                key = self.expect("id").value
                value = ""
                if self.accept("punct", "=") is not None:
                    value = self.expect("id").value
                attrs[key] = value
                self.accept("punct", ",")
                self.accept("punct", ";")
            if self.accept("punct", "[") is None:
                return attrs

    def apply_node(self, name: str, attrs: Attrs) -> None:
        node = self.graph.node(name, attrs=attrs)
        if "label" in attrs:
            node.label = attrs["label"]
        shape = attrs.get("shape")
        if shape:
            node.shape = _SHAPES.get(shape, Shape.BOX)
        # DOT's own spelling for a rounded box is a style, not a shape.
        if "rounded" in attrs.get("style", "") and node.shape == Shape.BOX:
            node.shape = Shape.ROUND
        if self.cluster_stack:
            node.cluster = self.cluster_stack[-1]


#: DOT has dozens of shape names; these are the ones the renderers can draw,
#: and anything else becomes a box rather than silently vanishing.
_SHAPES = {
    "box": Shape.BOX,
    "rect": Shape.BOX,
    "rectangle": Shape.BOX,
    "square": Shape.BOX,
    # Not DOT shape names -- DOT spells this `style=rounded` on a box -- but
    # they are this project's own default and a `.kg` file converted to DOT
    # round-trips through them.
    "round": Shape.ROUND,
    "rounded": Shape.ROUND,
    "none": Shape.BOX,
    "plaintext": Shape.BOX,
    "ellipse": Shape.ELLIPSE,
    "oval": Shape.ELLIPSE,
    "circle": Shape.CIRCLE,
    "doublecircle": Shape.CIRCLE,
    "point": Shape.POINT,
    "diamond": Shape.DIAMOND,
    "Mdiamond": Shape.DIAMOND,
}


def loads(source: str) -> Graph:
    """Parse DOT source into a graph."""
    return _Parser(_tokenize(source)).parse()
