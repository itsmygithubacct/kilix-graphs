"""Scene to pixels, through soft-raster.

This is where the additive soft-raster 0.5 primitives earn their place. Each
scene operation maps to one call:

===================  =========================================================
`Polyline`           `Canvas.polyline` — one blend per pixel at a shared
                     vertex, and a dash phase that runs along the whole path.
                     Stroking segment by segment puts a dark bead on every
                     bend of every edge and breaks every dashed one.
`Polygon`            `Canvas.fill_polygon_aa` — an arrowhead beside a round
                     node is the exact pairing that makes hard polygon edges
                     look like a defect.
`Rect` with radius   `Canvas.fill_round_rect` / `stroke_round_rect` — the
                     default node shape, seamless at the corner arcs.
`Ellipse`, `Text`    the 0.3 primitives, unchanged.
===================  =========================================================

`soft_raster` is imported lazily and its absence is reported as an actionable
error rather than an ImportError at module load, because everything else in
this package — parsing, layout, the text backend, SVG — runs on a bare
interpreter and should not be taken down by a missing native library.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from ..scene import Anchor, Ellipse, Polygon, Polyline, Rect, Scene, Text

__all__ = [
    "RasterOptions",
    "available",
    "new_canvas",
    "png_bytes",
    "render_raster",
    "text_width",
]


class RasterUnavailable(RuntimeError):
    """Raised when the raster backend is asked for without soft-raster."""


@dataclass
class RasterOptions:
    #: Pixels per scene unit.
    scale: float = 1.0
    #: Maximum canvas edge, so a runaway layout cannot ask for 40 GB.
    max_edge: int = 8192


def _soft_raster() -> Any:
    try:
        import soft_raster  # noqa: PLC0415 - deliberately lazy
    except ImportError as error:
        raise RasterUnavailable(
            "the raster backend needs the soft-raster Python binding. "
            "Install it, or set SOFT_RASTER_LIBRARY and put soft-raster's "
            "python/src on PYTHONPATH. Use --renderer text to draw without it."
        ) from error
    return soft_raster


def available() -> bool:
    """Whether the raster backend can run here."""
    try:
        library = _soft_raster().default_library()
    except Exception:  # noqa: BLE001 - any failure means "not available"
        return False
    return bool(getattr(library, "supports_graph_primitives", False))


def new_canvas(width: int, height: int, background: int = 0x000000) -> Any:
    """A blank canvas, cleared.

    Exposed so a caller that composes its own frame -- the pane viewer blits a
    rendered page into a viewport and draws chrome over it -- does not have to
    import `soft_raster` itself. This module is the one seam to the native
    library, and a test enforces that; a second importer is a second place to
    fix when the binding moves.
    """
    canvas = _soft_raster().Canvas(max(1, width), max(1, height))
    canvas.clear(background)
    return canvas


def text_width(text: str, scale: int = 1) -> int:
    """Rendered width of a string in the embedded font, in pixels."""
    return int(_soft_raster().text_width(text, scale))


def render_raster(scene: Scene, opts: RasterOptions | None = None) -> Any:
    """Draw a scene and return an open ``soft_raster.Canvas``.

    The caller owns the canvas: present it, write it, or close it.
    """
    options = opts or RasterOptions()
    sr = _soft_raster()
    library = sr.default_library()
    if not getattr(library, "supports_graph_primitives", False):
        raise RasterUnavailable(
            f"{library.path} is older than soft-raster 0.5, which is where "
            "sr_polyline, sr_fill_polygon_aa and the rounded-rectangle calls "
            "arrived. Rebuild soft-raster, or use --renderer text."
        )

    scale = options.scale
    width = max(1, min(options.max_edge, int(round(scene.width * scale))))
    height = max(1, min(options.max_edge, int(round(scene.height * scale))))
    canvas = sr.Canvas(width, height)
    canvas.clear(scene.background if scene.background is not None else 0x000000)

    def sx(value: float) -> float:
        return value * scale

    for op in scene.ordered():
        style = op.style

        if isinstance(op, Rect):
            if op.radius > 0:
                if style.fill is not None:
                    canvas.fill_round_rect(
                        sx(op.x), sx(op.y), sx(op.w), sx(op.h),
                        sx(op.radius), style.fill, style.alpha,
                    )
                if style.stroke is not None:
                    canvas.stroke_round_rect(
                        sx(op.x), sx(op.y), sx(op.w), sx(op.h),
                        sx(op.radius), sx(style.width), style.stroke, style.alpha,
                    )
            else:
                if style.fill is not None:
                    canvas.fill_rect(
                        sx(op.x), sx(op.y), sx(op.w), sx(op.h),
                        style.fill, style.alpha,
                    )
                if style.stroke is not None:
                    canvas.stroke_rect(
                        sx(op.x), sx(op.y), sx(op.w), sx(op.h),
                        sx(style.width), style.stroke, style.alpha,
                    )

        elif isinstance(op, Ellipse):
            if style.fill is not None:
                canvas.fill_ellipse(
                    sx(op.cx), sx(op.cy), sx(op.rx), sx(op.ry),
                    style.fill, style.alpha,
                )
            if style.stroke is not None:
                # sr_ring is circular; an ellipse outline is its own polygon.
                canvas.polyline(
                    _ellipse_outline(op, scale), sx(style.width),
                    style.stroke, style.alpha,
                )

        elif isinstance(op, Polyline):
            if style.stroke is not None and len(op.points) >= 2:
                canvas.polyline(
                    [(sx(x), sx(y)) for x, y in op.points],
                    sx(style.width),
                    style.stroke,
                    style.alpha,
                    dash_on=style.dash[0],
                    dash_off=style.dash[1],
                    cap=sr.Cap.BUTT if style.dash != (0, 0) else sr.Cap.ROUND,
                )

        elif isinstance(op, Polygon):
            points = [(sx(x), sx(y)) for x, y in op.points]
            if len(points) < 3:
                continue
            if style.fill is not None:
                canvas.fill_polygon_aa(points, style.fill, style.alpha)
            if style.stroke is not None:
                canvas.polyline(
                    [*points, points[0]], sx(style.width),
                    style.stroke, style.alpha,
                )

        elif isinstance(op, Text):
            _draw_text(canvas, sr, op, scale)

    return canvas


def _ellipse_outline(op: Ellipse, scale: float, steps: int = 48) -> list[tuple[float, float]]:
    import math

    points = [
        (
            (op.cx + op.rx * math.cos(2 * math.pi * i / steps)) * scale,
            (op.cy + op.ry * math.sin(2 * math.pi * i / steps)) * scale,
        )
        for i in range(steps)
    ]
    points.append(points[0])
    return points


def _draw_text(canvas: Any, sr: Any, op: Text, scale: float) -> None:
    # The embedded font is a bitmap at integer multiples, so a fractional
    # scene scale cannot be honoured exactly. Round to the nearest usable
    # multiplier and keep the position exact, which keeps a label centred on
    # its node even when it cannot be the requested size.
    multiplier = max(1, int(round(op.style.scale * scale)))
    lines = op.value.split("\n")
    line_height = sr.FONT_HEIGHT * multiplier
    top = op.y * scale - (len(lines) * line_height) / 2
    colour = op.style.fill if op.style.fill is not None else 0xFFFFFF

    for index, line in enumerate(lines):
        y = top + index * line_height
        width = sr.text_width(line, multiplier)
        if op.anchor == Anchor.MIDDLE:
            x = op.x * scale - width / 2
        elif op.anchor == Anchor.END:
            x = op.x * scale - width
        else:
            x = op.x * scale
        if op.halo is not None:
            # A one-pixel ring in the surface colour, so a label stays legible
            # where it crosses an edge. Cheaper and more predictable than a
            # blurred shadow, and it never darkens what is behind it.
            for dx, dy in ((-1, 0), (1, 0), (0, -1), (0, 1)):
                canvas.text(x + dx, y + dy, line, op.halo, 1.0, multiplier)
        canvas.text(x, y, line, colour, op.style.alpha, multiplier)


def ppm_bytes(canvas: Any) -> bytes:
    """Encode a canvas as binary PPM (P6), so it can be written by the same
    no-clobber path as every other output instead of by the rasteriser's own
    file writer, which opens whatever name it is given."""
    return b"P6\n%d %d\n255\n" % (canvas.width, canvas.height) + bytes(canvas.rgb_bytes())


def png_bytes(canvas: Any) -> bytes:
    """Encode a canvas as a PNG, with the standard library and nothing else.

    A PNG is a fixed header, one zlib stream of filter-prefixed rows, and a
    CRC32 per chunk -- all of which `zlib` and `struct` already provide. Adding
    an image dependency to write the one format everything can open would be
    the wrong trade for a package whose whole claim is that it has none.

    Shared because two callers need the same bytes for different reasons: the
    command line writes them to a file, and the GUI hands them to Tk, which
    reads PNG from memory but not the PPM the rasteriser writes natively.
    """
    import struct  # noqa: PLC0415
    import zlib  # noqa: PLC0415

    width = canvas.width
    height = canvas.height
    rgb = canvas.rgb_bytes()
    stride = width * 3
    raw = b"".join(
        b"\x00" + bytes(rgb[y * stride : (y + 1) * stride]) for y in range(height)
    )

    def chunk(tag: bytes, payload: bytes) -> bytes:
        body = tag + payload
        return (
            struct.pack(">I", len(payload)) + body + struct.pack(">I", zlib.crc32(body))
        )

    header = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", header)
        + chunk(b"IDAT", zlib.compress(raw, 6))
        + chunk(b"IEND", b"")
    )
