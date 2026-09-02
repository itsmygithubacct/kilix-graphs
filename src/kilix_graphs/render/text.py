"""The cell-grid renderer: a second renderer, not a downsampled raster.

Every tool that renders diagrams to text well — D2's `d2ascii`, `graph-easy` —
lays out on a cell grid and resolves each cell's glyph from the directions
entering it. Rasterising and then downsampling cannot produce box drawing,
because a corner is not a dark pixel, it is a *junction*.

The mapping from scene units to cells is exact rather than approximate, and
that is on purpose. Node sizes come from the same 8x16 font metrics the raster
backend uses, so one cell is 8 units wide and 16 tall and a node sized to fit
its label in the scene is sized to fit the same label in cells. The two
backends agree on text size by construction rather than by tuning.

Two charsets: Unicode box drawing, and pure ASCII for a terminal or a pipe
that cannot carry it.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from ..model import FONT_ADVANCE, FONT_HEIGHT, display_width
from ..scene import Anchor, Ellipse, Polygon, Polyline, Rect, Scene, Text

__all__ = ["ASCII", "Charset", "TextOptions", "UNICODE", "render_text"]

N, S, E, W = 1, 2, 4, 8


@dataclass(frozen=True)
class Charset:
    name: str
    #: Indexed by the N|S|E|W bitmask of the directions leaving a cell.
    junction: tuple[str, ...]
    arrow_n: str
    arrow_s: str
    arrow_e: str
    arrow_w: str
    corner_tl: str
    corner_tr: str
    corner_bl: str
    corner_br: str
    #: A shape smaller than a cell. A point node drawn as a box is a 3x3
    #: outline around nothing, which reads as an empty node rather than a dot.
    dot: str
    #: A self-loop. Routed as a poly-line it is a knot of junctions across the
    #: node it belongs to; one glyph beside the node says the same thing.
    loop: str


def _junctions(
    h: str, v: str, tl: str, tr: str, bl: str, br: str,
    td: str, tu: str, tr_: str, tl_: str, cross: str,
) -> tuple[str, ...]:
    """Build the 16-entry mask table once, so no call site indexes it wrong."""
    table = [" "] * 16
    table[0] = " "
    table[N] = v
    table[S] = v
    table[E] = h
    table[W] = h
    table[N | S] = v
    table[E | W] = h
    table[S | E] = tl
    table[S | W] = tr
    table[N | E] = bl
    table[N | W] = br
    table[N | S | E] = tr_
    table[N | S | W] = tl_
    table[S | E | W] = td
    table[N | E | W] = tu
    table[N | S | E | W] = cross
    return tuple(table)


UNICODE = Charset(
    name="unicode",
    junction=_junctions("─", "│", "┌", "┐", "└", "┘", "┬", "┴", "├", "┤", "┼"),
    arrow_n="▲",
    arrow_s="▼",
    arrow_e="▶",
    arrow_w="◀",
    corner_tl="╭",
    corner_tr="╮",
    corner_bl="╰",
    corner_br="╯",
    dot="●",
    loop="↺",
)

ASCII = Charset(
    name="ascii",
    junction=_junctions("-", "|", "+", "+", "+", "+", "+", "+", "+", "+", "+"),
    arrow_n="^",
    arrow_s="v",
    arrow_e=">",
    arrow_w="<",
    corner_tl="+",
    corner_tr="+",
    corner_bl="+",
    corner_br="+",
    dot="o",
    loop="@",
)

CHARSETS = {"unicode": UNICODE, "ascii": ASCII}


@dataclass
class TextOptions:
    charset: Charset = UNICODE
    #: Scene units per cell. The defaults match the embedded font's cell.
    cell_w: float = float(FONT_ADVANCE)
    cell_h: float = float(FONT_HEIGHT)
    #: Emit 24-bit ANSI colour from the scene's styles.
    colour: bool = False
    max_columns: int = 400
    max_rows: int = 200


#: Written into the cell a wide glyph spills into. It emits nothing, because
#: the glyph itself already covers that column -- a space there would push the
#: rest of the row one column right.
CONTINUATION = "\0"


class _Grid:
    """Characters, per-cell line directions, and optional colour."""

    def __init__(self, columns: int, rows: int) -> None:
        self.columns = columns
        self.rows = rows
        self.chars = [[" "] * columns for _ in range(rows)]
        self.lines = [[0] * columns for _ in range(rows)]
        # Cells painted by a shape or a label rather than resolved from line
        # directions. An arrowhead may take back the last cell of its own
        # line; it must never eat a node outline or a letter.
        self.solid = [[False] * columns for _ in range(rows)]
        self.colour: list[list[int | None]] = [[None] * columns for _ in range(rows)]

    def inside(self, col: int, row: int) -> bool:
        return 0 <= col < self.columns and 0 <= row < self.rows

    def put(self, col: int, row: int, char: str, colour: int | None = None) -> None:
        if not self.inside(col, row) or not char:
            return
        self.chars[row][col] = char
        self.solid[row][col] = True
        self.colour[row][col] = colour

    def link(self, col: int, row: int, mask: int, colour: int | None) -> None:
        if not self.inside(col, row):
            return
        self.lines[row][col] |= mask
        if self.colour[row][col] is None:
            self.colour[row][col] = colour


def render_text(scene: Scene, opts: TextOptions | None = None) -> str:
    """Render a scene as a grid of characters."""
    options = opts or TextOptions()
    charset = options.charset

    columns = min(options.max_columns, max(1, _cell(scene.width, options.cell_w) + 1))
    rows = min(options.max_rows, max(1, _cell(scene.height, options.cell_h) + 1))
    grid = _Grid(columns, rows)

    def to_cell(x: float, y: float) -> tuple[int, int]:
        return (_cell(x, options.cell_w), _cell(y, options.cell_h))

    arrows: list[tuple[tuple[tuple[float, float], ...], int | None]] = []
    fixed: list[Text] = []
    floating: list[Text] = []
    dots = _Braille(columns, rows) if charset is UNICODE else None

    for op in scene.ordered():
        colour = op.style.stroke if op.style.stroke is not None else op.style.fill
        # A cell grid has one weight of line. A gridline drawn in it is as loud
        # as the data crossing it, and the chart becomes unreadable -- measured:
        # a seven-point two-series line chart came out as a solid block of box
        # drawing with no data visible in it. Chrome that exists to recede is
        # dropped rather than drawn.
        if op.style.role == "grid":
            continue
        if op.style.role == "loop":
            # One glyph, just past the node's right edge. The arrowhead that
            # comes with the loop is dropped: the glyph already says "returns
            # here", and in cells the head lands on the outline.
            if isinstance(op, Polyline) and op.points:
                far = max(op.points, key=lambda point: point[0])
                grid.put(*to_cell(far[0], far[1]), charset.loop, colour)
            continue
        if op.style.role == "data":
            _data(grid, dots, charset, op, to_cell, options, colour)
            continue
        if isinstance(op, Rect):
            if op.w <= options.cell_w and op.h <= options.cell_h:
                grid.put(*to_cell(op.x + op.w / 2, op.y + op.h / 2), charset.dot, colour)
            else:
                _box(grid, charset,
                     *_centred(op.x + op.w / 2, op.y + op.h / 2, op.w, op.h, options),
                     colour, rounded=op.radius > 0)
        elif isinstance(op, Ellipse):
            if op.rx * 2 <= options.cell_w and op.ry * 2 <= options.cell_h:
                # A point node is 8 units across -- one cell. Boxing it gives a
                # 3x3 outline around nothing, which reads as an empty node.
                grid.put(*to_cell(op.cx, op.cy), charset.dot, colour)
            else:
                _box(grid, charset,
                     *_centred(op.cx, op.cy, op.rx * 2, op.ry * 2, options),
                     colour, rounded=True)
        elif isinstance(op, Polyline):
            _polyline(grid, _orthogonal([to_cell(x, y) for x, y in op.points]), colour)
        elif isinstance(op, Polygon):
            arrows.append((op.points, colour))
        elif isinstance(op, Text):
            # Labels that own their space go down now; floating ones wait, so
            # they dodge the node labels rather than displacing them. Without
            # the split an edge label landed inside a node box first and the
            # node's own name was pushed out of its outline.
            (floating if op.avoid else fixed).append(op)

    for op in fixed:
        _text(grid, op, to_cell, options)
    for op in floating:
        _text(grid, op, to_cell, options)

    if dots is not None:
        dots.flush(grid)

    # Line cells resolve last: a junction glyph depends on every segment that
    # reached that cell, so it cannot be chosen while segments are still
    # arriving.
    for row in range(rows):
        for col in range(columns):
            mask = grid.lines[row][col]
            if mask and grid.chars[row][col] == " ":
                grid.chars[row][col] = charset.junction[mask]

    # Arrowheads last of all. In pixels an arrow tip touches the node outline;
    # in cells the tip *is* the outline cell, so drawing it in scene order
    # puts it under the node box and it disappears. Placing it afterwards, in
    # the free cell before the node, is what an arrow into a box looks like
    # when the box is made of characters.
    for points, colour in arrows:
        _arrow(grid, charset, points, to_cell, colour)

    return _emit(grid, options)


def _cell(value: float, size: float) -> int:
    """Scene units to a cell index, rounding halves up.

    Not `round()`. Python rounds halves to even, so a node box spanning rows
    2 to 4 has its centre computed as row 2 -- the top border -- and the label
    lands on the outline instead of inside it. Measured: round(1.5) is 2,
    round(2.5) is 2, round(3.5) is 4. Rounding halves consistently upward puts
    the three back in step.
    """
    return math.floor(value / size + 0.5) if size else 0


def _centred(
    cx: float, cy: float, w: float, h: float, options: TextOptions
) -> tuple[tuple[int, int], tuple[int, int]]:
    """Cell corners of a box, measured out from its centre cell.

    Rounding the two corners independently makes the box straddle its centre
    unevenly whenever the scene coordinate is not a whole number of cells --
    a 9-cell box lands on columns k..k+9, whose true centre is k+4.5, and the
    label then sits one cell left of where the box looks centred. Measuring
    the half-extents out from the centre cell makes the box symmetric about
    it by construction, so a centred label is centred.
    """
    col = _cell(cx, options.cell_w)
    row = _cell(cy, options.cell_h)
    # Half-extents round up, so a box is never drawn too small to hold the
    # label it was sized for; the cost is that a box whose half-width lands on
    # a half-cell comes out one cell wider than the minimum.
    half_w = max(1, _cell(w / 2, options.cell_w))
    half_h = max(1, _cell(h / 2, options.cell_h))
    return ((col - half_w, row - half_h), (col + half_w, row + half_h))


def _orthogonal(cells: list[tuple[int, int]]) -> list[tuple[int, int]]:
    """Rewrite a path as axis-aligned legs.

    A diagonal in a cell grid has no glyph that reads as a line -- it comes out
    as a staircase of corners, which is why every tool that renders diagrams to
    text well routes orthogonally rather than rasterising a diagonal. Each leg
    leaves along its dominant axis, crosses at the midpoint, and arrives along
    the same axis, which is the Z shape a layered drawing wants.
    """
    if len(cells) < 2:
        return cells
    # Curved routes carry many nearly-collinear points; keeping them would put
    # a Z between every pair. Reduce to the corners first.
    simplified = [cells[0]]
    for point in cells[1:]:
        if point != simplified[-1]:
            simplified.append(point)
    if len(simplified) > 2:
        thinned = [simplified[0]]
        for index in range(1, len(simplified) - 1):
            before, here, after = simplified[index - 1], simplified[index], simplified[index + 1]
            if _towards(here, before) | _towards(here, after) != _towards(before, after) | _towards(after, before):
                thinned.append(here)
        thinned.append(simplified[-1])
        simplified = thinned

    out: list[tuple[int, int]] = [simplified[0]]
    for index in range(len(simplified) - 1):
        ax, ay = simplified[index]
        bx, by = simplified[index + 1]
        if ax == bx or ay == by:
            out.append((bx, by))
            continue
        if abs(by - ay) >= abs(bx - ax):
            mid = ay + (by - ay) // 2
            out.extend([(ax, mid), (bx, mid), (bx, by)])
        else:
            mid = ax + (bx - ax) // 2
            out.extend([(mid, ay), (mid, by), (bx, by)])
    return out


class _Braille:
    """A 2x4 sub-cell dot grid, rendered as U+28xx.

    Chart data at whole-cell resolution is a staircase; eight dots per cell is
    what makes a line in a terminal look like a line. It is the same trick
    `plotext` uses, and it is only available in the Unicode charset -- the
    ASCII fallback plots a marker per cell instead.
    """

    __slots__ = ("columns", "rows", "bits", "colour")

    #: Braille dot values by (row within cell, column within cell).
    MASK = ((0x01, 0x08), (0x02, 0x10), (0x04, 0x20), (0x40, 0x80))

    def __init__(self, columns: int, rows: int) -> None:
        self.columns = columns
        self.rows = rows
        self.bits = [[0] * columns for _ in range(rows)]
        self.colour: list[list[int | None]] = [[None] * columns for _ in range(rows)]

    def set(self, dot_x: int, dot_y: int, colour: int | None) -> None:
        col, within_x = divmod(dot_x, 2)
        row, within_y = divmod(dot_y, 4)
        if not (0 <= col < self.columns and 0 <= row < self.rows):
            return
        self.bits[row][col] |= self.MASK[within_y][within_x]
        if self.colour[row][col] is None:
            self.colour[row][col] = colour

    def flush(self, grid: "_Grid") -> None:
        for row in range(self.rows):
            for col in range(self.columns):
                mask = self.bits[row][col]
                if mask:
                    grid.put(col, row, chr(0x2800 + mask), self.colour[row][col])


def _data(
    grid: _Grid,
    dots: "_Braille | None",
    charset: Charset,
    op: object,
    to_cell,
    options: TextOptions,
    colour: int | None,
) -> None:
    """Chart marks: sub-cell dots where the charset allows, blocks otherwise."""
    if isinstance(op, Rect):
        # A bar is a solid column. Half blocks would be better and are not
        # available in the ASCII charset, so both use whole cells.
        block = "\u2588" if charset is UNICODE else "#"
        x0, y0 = to_cell(op.x, op.y)
        x1, y1 = to_cell(op.x + op.w, op.y + op.h)
        for row in range(y0, max(y0 + 1, y1)):
            for col in range(x0, max(x0 + 1, x1)):
                grid.put(col, row, block, colour)
        return

    if isinstance(op, Ellipse):
        if dots is not None:
            dots.set(*_dot(op.cx, op.cy, options), colour)
        else:
            grid.put(*to_cell(op.cx, op.cy), "o", colour)
        return

    points = getattr(op, "points", ())
    if len(points) < 2:
        return
    if dots is None:
        for x, y in points:
            grid.put(*to_cell(x, y), "*", colour)
        return
    for index in range(len(points) - 1):
        ax, ay = _dot(points[index][0], points[index][1], options)
        bx, by = _dot(points[index + 1][0], points[index + 1][1], options)
        for dot_x, dot_y in _dot_line(ax, ay, bx, by):
            dots.set(dot_x, dot_y, colour)


def _dot(x: float, y: float, options: TextOptions) -> tuple[int, int]:
    return (
        math.floor(x / options.cell_w * 2),
        math.floor(y / options.cell_h * 4),
    )


def _dot_line(x0: int, y0: int, x1: int, y1: int) -> list[tuple[int, int]]:
    out: list[tuple[int, int]] = []
    dx, dy = abs(x1 - x0), -abs(y1 - y0)
    sx = 1 if x0 < x1 else -1
    sy = 1 if y0 < y1 else -1
    error = dx + dy
    x, y = x0, y0
    guard = dx - dy + 4
    while guard >= 0:
        out.append((x, y))
        if x == x1 and y == y1:
            break
        doubled = 2 * error
        if doubled >= dy:
            error += dy
            x += sx
        if doubled <= dx:
            error += dx
            y += sy
        guard -= 1
    return out


def _box(
    grid: _Grid,
    charset: Charset,
    top_left: tuple[int, int],
    bottom_right: tuple[int, int],
    colour: int | None,
    rounded: bool,
) -> None:
    x0, y0 = top_left
    x1, y1 = bottom_right
    if x1 <= x0:
        x1 = x0 + 1
    if y1 <= y0:
        y1 = y0 + 1
    horizontal = charset.junction[E | W]
    vertical = charset.junction[N | S]
    for col in range(x0 + 1, x1):
        grid.put(col, y0, horizontal, colour)
        grid.put(col, y1, horizontal, colour)
    for row in range(y0 + 1, y1):
        grid.put(x0, row, vertical, colour)
        grid.put(x1, row, vertical, colour)
    if rounded:
        grid.put(x0, y0, charset.corner_tl, colour)
        grid.put(x1, y0, charset.corner_tr, colour)
        grid.put(x0, y1, charset.corner_bl, colour)
        grid.put(x1, y1, charset.corner_br, colour)
    else:
        grid.put(x0, y0, charset.junction[S | E], colour)
        grid.put(x1, y0, charset.junction[S | W], colour)
        grid.put(x0, y1, charset.junction[N | E], colour)
        grid.put(x1, y1, charset.junction[N | W], colour)


def _polyline(grid: _Grid, cells: list[tuple[int, int]], colour: int | None) -> None:
    """Walk each segment, recording which way the line leaves every cell.

    Direction bits rather than glyphs: a cell two segments pass through needs
    the glyph for their union, and that is only known once both have been
    walked.
    """
    for index in range(len(cells) - 1):
        for col, row, entered, leaving in _segment(cells[index], cells[index + 1]):
            grid.link(col, row, entered | leaving, colour)


def _segment(
    start: tuple[int, int], end: tuple[int, int]
) -> list[tuple[int, int, int, int]]:
    """Bresenham, reporting the direction into and out of each cell."""
    x0, y0 = start
    x1, y1 = end
    points: list[tuple[int, int]] = []
    dx = abs(x1 - x0)
    dy = -abs(y1 - y0)
    sx = 1 if x0 < x1 else -1
    sy = 1 if y0 < y1 else -1
    error = dx + dy
    x, y = x0, y0
    guard = dx - dy + 4
    while guard >= 0:
        points.append((x, y))
        if x == x1 and y == y1:
            break
        doubled = 2 * error
        if doubled >= dy:
            error += dy
            x += sx
        if doubled <= dx:
            error += dx
            y += sy
        guard -= 1

    out: list[tuple[int, int, int, int]] = []
    for index, (cx, cy) in enumerate(points):
        entered = 0
        leaving = 0
        if index > 0:
            entered = _towards((cx, cy), points[index - 1])
        if index + 1 < len(points):
            leaving = _towards((cx, cy), points[index + 1])
        if entered == 0 and leaving == 0:
            leaving = E
        out.append((cx, cy, entered, leaving))
    return out


def _towards(origin: tuple[int, int], other: tuple[int, int]) -> int:
    dx = other[0] - origin[0]
    dy = other[1] - origin[1]
    mask = 0
    if dx > 0:
        mask |= E
    elif dx < 0:
        mask |= W
    if dy > 0:
        mask |= S
    elif dy < 0:
        mask |= N
    return mask


def _arrow(
    grid: _Grid,
    charset: Charset,
    points: tuple[tuple[float, float], ...],
    to_cell,
    colour: int | None,
) -> None:
    """An arrowhead polygon is one glyph: the tip, pointing away from its base.

    The direction comes from the scene coordinates, not from the cell-mapped
    ones. A default arrowhead is about eleven scene units across -- under a
    cell and a half -- so all three of its points map to the same cell and a
    direction taken after mapping is (0, 0). Every arrow vanished before this
    was measured from the floats.
    """
    if len(points) < 3:
        return
    dx = points[0][0] - (points[1][0] + points[2][0]) / 2
    dy = points[0][1] - (points[1][1] + points[2][1]) / 2
    if dx == 0.0 and dy == 0.0:
        return
    tip = to_cell(*points[0])
    if abs(dx) >= abs(dy):
        glyph = charset.arrow_e if dx >= 0 else charset.arrow_w
        step = (-1 if dx >= 0 else 1, 0)
    else:
        glyph = charset.arrow_s if dy >= 0 else charset.arrow_n
        step = (0, -1 if dy >= 0 else 1)

    # Back off along the incoming direction until the head has a cell of its
    # own. Two steps is enough to clear an outline and its shadow; further
    # than that the arrow no longer points at anything and is better omitted.
    col, row = tip
    for _ in range(3):
        if grid.inside(col, row) and not grid.solid[row][col] and (
            grid.chars[row][col] == " " or grid.lines[row][col] != 0
        ):
            # A free cell, or the last cell of the line the arrow terminates:
            # replacing that one is what an arrowhead *is*. Outline and label
            # cells are solid, so the loop steps over them rather than eating
            # them -- which it did, printing arrows into the top border of a
            # node before `solid` existed.
            grid.put(col, row, glyph, colour)
            return
        col += step[0]
        row += step[1]


def _text(grid: _Grid, op: Text, to_cell, options: TextOptions) -> None:
    """Write a label, preferring a row where it does not land on structure.

    A label written straight onto its nominal row eats whatever is there --
    which for an edge label near a cluster is the cluster outline, and the
    result reads as a broken box. Trying a couple of rows either side first
    costs nothing and moves most labels into clear space. When nothing is
    clear the label is still written, because a label that collides is more
    use than one that silently vanished.
    """
    lines = op.value.split("\n")
    col, row = to_cell(op.x, op.y)
    start_row = row - (len(lines) - 1) // 2

    def begin_at(line: str) -> int:
        width = display_width(line)
        if op.anchor == Anchor.MIDDLE:
            return col - width // 2
        if op.anchor == Anchor.END:
            return col - width
        return col

    def clear(shift: int) -> bool:
        for offset, line in enumerate(lines):
            r = start_row + offset + shift
            begin = begin_at(line)
            for index in range(display_width(line)):
                c = begin + index
                if grid.inside(c, r) and grid.solid[r][c]:
                    return False
        return True

    shift = next((candidate for candidate in (0, 1, -1, 2, -2, 3, -3) if clear(candidate)), 0)
    for offset, line in enumerate(lines):
        column = begin_at(line)
        row = start_row + offset + shift
        for char in line:
            width = display_width(char)
            if width == 0:
                continue
            grid.put(column, row, char, op.style.fill)
            if width > 1:
                # A wide glyph occupies the next column too. Claiming it stops
                # something else being written under the glyph's right half,
                # and the continuation emits nothing rather than a space --
                # a space there would render the row one column too wide.
                grid.put(column + 1, row, CONTINUATION, op.style.fill)
            column += width


def _emit(grid: _Grid, options: TextOptions) -> str:
    rows: list[str] = []
    for row in range(grid.rows):
        if not options.colour:
            rows.append("".join(grid.chars[row]).replace(CONTINUATION, "").rstrip())
            continue
        pieces: list[str] = []
        current: int | None = None
        for col in range(grid.columns):
            char = grid.chars[row][col]
            if char == CONTINUATION:
                continue
            want = grid.colour[row][col] if char != " " else None
            if want != current:
                pieces.append("\x1b[0m" if want is None else _ansi(want))
                current = want
            pieces.append(char)
        if current is not None:
            pieces.append("\x1b[0m")
        rows.append("".join(pieces).rstrip())
    while rows and not rows[-1]:
        rows.pop()
    return "\n".join(rows)


def _ansi(colour: int) -> str:
    return f"\x1b[38;2;{(colour >> 16) & 255};{(colour >> 8) & 255};{colour & 255}m"
