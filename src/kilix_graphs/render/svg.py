"""Scene to SVG.

Exporting is a small exporter, not a graphics stack: every scene operation has
a one-to-one SVG element. It exists because a drawing that can only be seen in
a terminal cannot be put in a document or a pull request, and because an SVG
diff is a readable answer to "what did the layout change?".

Importing SVG is explicitly not in scope. That is a parser for a large
specification, and none of the inputs anyone actually has are SVG.
"""

from __future__ import annotations

from xml.sax.saxutils import escape

from ..model import FONT_ADVANCE, FONT_HEIGHT
from ..scene import Anchor, Ellipse, Op, Polygon, Polyline, Rect, Scene, Text

__all__ = ["render_svg"]

_ANCHORS = {
    Anchor.START: "start",
    Anchor.MIDDLE: "middle",
    Anchor.END: "end",
}


def _hex(value: int) -> str:
    return f"#{value & 0xFFFFFF:06x}"


def _paint(op: Op) -> str:
    style = op.style
    parts = [f'fill="{_hex(style.fill)}"' if style.fill is not None else 'fill="none"']
    if style.stroke is not None:
        parts.append(f'stroke="{_hex(style.stroke)}"')
        parts.append(f'stroke-width="{style.width:g}"')
        parts.append('stroke-linejoin="round"')
        parts.append('stroke-linecap="round"')
        if style.dash != (0, 0):
            parts.append(f'stroke-dasharray="{style.dash[0]:g} {style.dash[1]:g}"')
    if style.alpha < 1.0:
        parts.append(f'opacity="{style.alpha:g}"')
    return " ".join(parts)


def render_svg(scene: Scene, *, font: str = "ui-monospace, monospace") -> str:
    """Render a scene as a standalone SVG document."""
    width = max(1.0, scene.width)
    height = max(1.0, scene.height)
    out: list[str] = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width:g}" '
        f'height="{height:g}" viewBox="0 0 {width:g} {height:g}">',
    ]
    if scene.background is not None:
        out.append(f'<rect width="{width:g}" height="{height:g}" fill="{_hex(scene.background)}"/>')

    for op in scene.ordered():
        if isinstance(op, Rect):
            radius = f' rx="{op.radius:g}"' if op.radius > 0 else ""
            out.append(
                f'<rect x="{op.x:g}" y="{op.y:g}" width="{op.w:g}" '
                f'height="{op.h:g}"{radius} {_paint(op)}/>'
            )
        elif isinstance(op, Ellipse):
            out.append(
                f'<ellipse cx="{op.cx:g}" cy="{op.cy:g}" rx="{op.rx:g}" '
                f'ry="{op.ry:g}" {_paint(op)}/>'
            )
        elif isinstance(op, (Polyline, Polygon)):
            points = " ".join(f"{x:g},{y:g}" for x, y in op.points)
            tag = "polyline" if isinstance(op, Polyline) else "polygon"
            out.append(f'<{tag} points="{points}" {_paint(op)}/>')
        elif isinstance(op, Text):
            out.extend(_text(op, font))

    out.append("</svg>")
    return "\n".join(out)


def _text(op: Text, font: str) -> list[str]:
    lines = op.value.split("\n")
    size = FONT_HEIGHT * op.style.scale
    # The scene's y for a text run is its vertical centre, which is what every
    # other operation means by a centre; SVG's y is the baseline.
    top = op.y - (len(lines) * size) / 2
    anchor = _ANCHORS.get(op.anchor, "middle")
    fill = _hex(op.style.fill if op.style.fill is not None else 0x000000)

    out: list[str] = []
    for index, line in enumerate(lines):
        baseline = top + size * (index + 0.78)
        if op.halo is not None:
            out.append(
                f'<text x="{op.x:g}" y="{baseline:g}" text-anchor="{anchor}" '
                f'font-family="{font}" font-size="{size:g}" '
                f'stroke="{_hex(op.halo)}" stroke-width="3" fill="none" '
                f'paint-order="stroke">{escape(line)}</text>'
            )
        out.append(
            f'<text x="{op.x:g}" y="{baseline:g}" text-anchor="{anchor}" '
            f'font-family="{font}" font-size="{size:g}" '
            f'fill="{fill}">{escape(line)}</text>'
        )
    return out


def text_advance(text: str, scale: int = 1) -> float:
    """Width of a string in scene units, matching the embedded font."""
    widest = max((len(line) for line in text.split("\n")), default=0)
    return widest * FONT_ADVANCE * scale
