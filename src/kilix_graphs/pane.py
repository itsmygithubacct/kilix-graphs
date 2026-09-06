"""The Kilix-native viewer: pixels and a mouse, inside a terminal pane.

This is what a graphical application looks like on this stack. Of the 42
entries in the Kilix content catalog, 39 launch as a terminal pane and exactly
one opens an X window; sixteen declare `kitty-graphics`, and three of those
also take `kitty-mouse`. A desktop toolkit is the exception here, not the rule,
and the pieces for the rule are already modules:

    soft-raster           draws the pixels
    kitty-frame-presenter puts them in the pane, damage-aware
    kitty-input           decodes the keyboard and the mouse

`kitty-input` is C with no Python binding, so the small, well-specified subset
this needs -- SGR 1006 mouse reports and ordinary keys -- is decoded here. A
binding belongs in that module rather than in this app, the way `soft-raster`
carries its own; until there is one, this is the seam.

What the mouse buys over the cell viewer is not decoration. Hovering
highlights the node under the pointer and clicking selects it, which needs a
hit test against real geometry -- something a character grid cannot do, and
the reason this is a GUI rather than a prettier TUI.
"""

from __future__ import annotations

import os
import re
import select
import sys
import termios
import tty
from dataclasses import dataclass
from pathlib import Path

from .model import Node
from .view import Session, Settings

__all__ = ["available", "run"]

#: Button-event tracking (1002) reports motion while a button is held, which
#: is what makes drag-to-pan possible; 1006 asks for the SGR encoding, which
#: is the only one that survives past column 223.
MOUSE_ON = "\x1b[?1000h\x1b[?1002h\x1b[?1006h"
MOUSE_OFF = "\x1b[?1006l\x1b[?1002l\x1b[?1000l"
ALT_SCREEN_ON = "\x1b[?1049h\x1b[?25l"
ALT_SCREEN_OFF = "\x1b[?25h\x1b[?1049l"

_SGR = re.compile(r"\x1b\[<(\d+);(\d+);(\d+)([Mm])")

HELP = [
    "drag  pan            wheel  zoom",
    "click select a node  e/E  engine   d  direction",
    "t theme   c curves   +/-  zoom     r reload",
    "w write beside the source           ? help   q quit",
]


def _ascii(text: str) -> str:
    """Drop what the embedded font cannot draw, rather than showing '?'."""
    return "".join(c if 32 <= ord(c) <= 126 else "?" for c in text)


@dataclass
class _Mouse:
    button: int
    column: int
    row: int
    pressed: bool
    motion: bool
    wheel: int  # -1 up, +1 down, 0 otherwise


def available() -> bool:
    """Whether this terminal can host the pane viewer."""
    if not (sys.stdin.isatty() and sys.stdout.isatty()):
        return False
    from .render import supports_graphics  # noqa: PLC0415

    return supports_graphics()


def _cell_pixels(fd: int) -> tuple[int, int]:
    """Pixel size of one cell, from the terminal itself where it says.

    TIOCGWINSZ carries a pixel size that Kitty fills in and many terminals
    leave at zero; the embedded font's 8x16 cell is the right fallback,
    because it is what every other measurement in this package assumes.
    """
    import fcntl  # noqa: PLC0415
    import struct  # noqa: PLC0415

    try:
        packed = fcntl.ioctl(fd, termios.TIOCGWINSZ, b"\0" * 8)
        rows, columns, width, height = struct.unpack("HHHH", packed)
        if width and height and rows and columns:
            return (max(1, width // columns), max(1, height // rows))
    except OSError:
        pass
    return (8, 16)


class _Pane:
    def __init__(
        self,
        session: Session,
        size: tuple[int, int] | None = None,
        cell: tuple[int, int] | None = None,
    ) -> None:
        """`size` and `cell` are for tests: a pane without a terminal."""
        self.session = session
        self.pan_x = 0.0
        self.pan_y = 0.0
        self.selected: Node | None = None
        self.hovered: Node | None = None
        self.message = ""
        self.show_help = False
        self.dragging = False
        self.drag_from = (0, 0)

        self.fd = sys.stdin.fileno() if size is None else -1
        self.cell_w, self.cell_h = cell or _cell_pixels(
            sys.stdout.fileno() if size is None else 0
        )
        self.columns, self.rows = size or os.get_terminal_size()
        self._page = None
        self._page_key: object = None
        self._presenter = None

    # ------------------------------------------------------------- geometry

    @property
    def width(self) -> int:
        return max(1, self.columns * self.cell_w)

    @property
    def height(self) -> int:
        # One row is left for the shell's cursor, so quitting does not land on
        # top of the drawing.
        return max(1, (self.rows - 1) * self.cell_h)

    def page(self):
        """The whole drawing, rendered once per (settings, zoom)."""
        key = (self.session.settings, self.session.source)
        if key != self._page_key:
            if self._page is not None:
                self._page.close()
            self._page = self.session.raster()
            self._page_key = key
            self.clamp()
        return self._page

    def clamp(self) -> None:
        page = self._page
        if page is None:
            return
        self.pan_x = max(0.0, min(self.pan_x, max(0, page.width - self.width)))
        self.pan_y = max(0.0, min(self.pan_y, max(0, page.height - self.height)))

    def offset(self) -> tuple[int, int]:
        """Where the page sits in the viewport.

        A drawing smaller than the pane is centred rather than pinned to the
        corner, which reads as the pane being broken rather than the drawing
        being small. Larger than the pane, the pan decides.
        """
        page = self._page
        if page is None:
            return (0, 0)
        return (
            max(0, (self.width - page.width) // 2) - int(self.pan_x),
            max(0, (self.height - page.height) // 2) - int(self.pan_y),
        )

    def fit(self) -> None:
        scene = self.session.scene()
        if scene is None or scene.width <= 0 or scene.height <= 0:
            return
        wanted = min(self.width / scene.width, self.height / scene.height)
        self.session.update(scale=round(max(0.1, min(8.0, wanted)), 3))
        self.pan_x = self.pan_y = 0.0
        self._page_key = None

    def at(self, column: int, row: int) -> Node | None:
        """The node under a mouse cell, or None."""
        graph = self.session.graph()
        if graph is None:
            return None
        scale = self.session.settings.scale
        self.page()
        off_x, off_y = self.offset()
        x = ((column - 1) * self.cell_w - off_x) / scale
        y = ((row - 1) * self.cell_h - off_y) / scale
        for node in graph.nodes.values():
            if (
                abs(x - node.x) <= node.w / 2
                and abs(y - node.y) <= node.h / 2
            ):
                return node
        return None

    def cell_of(self, x: float, y: float) -> tuple[int, int]:
        """The mouse cell a scene point falls in: the inverse of `at`.

        Having both directions written once is what keeps the hit test and
        the drawing agreeing about where the page sits, which they stopped
        doing the moment a smaller drawing started being centred.
        """
        self.page()
        scale = self.session.settings.scale
        off_x, off_y = self.offset()
        return (
            int((x * scale + off_x) // self.cell_w) + 1,
            int((y * scale + off_y) // self.cell_h) + 1,
        )

    # -------------------------------------------------------------- drawing

    def frame(self):
        """Compose one viewport-sized canvas: the page, then the chrome."""
        from .render import raster  # noqa: PLC0415

        page = self.page()
        theme = self.session.theme
        view = raster.new_canvas(self.width, self.height, theme.surface)
        view.blit(page, *self.offset())
        self._chrome(view, theme)
        return view

    def _chrome(self, view, theme) -> None:
        scale = self.session.settings.scale
        # A ring around the node under the pointer, and a filled one around
        # the selection: the two states a pointer interface has to show.
        off_x, off_y = self.offset()
        for node, width in ((self.hovered, 1.5), (self.selected, 2.5)):
            if node is None:
                continue
            view.stroke_round_rect(
                node.x * scale - node.w * scale / 2 - 3 + off_x,
                node.y * scale - node.h * scale / 2 - 3 + off_y,
                node.w * scale + 6, node.h * scale + 6, 8 * scale,
                width * 2, theme.categorical(0), 1.0,
            )

        from .render import raster  # noqa: PLC0415

        # The embedded faces cover ASCII 32..126 and draw anything else as
        # '?', so the chrome is written in ASCII rather than in characters
        # that come out as punctuation holes.
        bar_h = 20
        top = self.height - bar_h
        view.fill_rect(0, top, self.width, bar_h, theme.cluster_fill, 0.94)
        stats = self.session.stats
        note = self.message or (self.session.error or "")
        if note:
            colour = theme.categorical(7) if self.session.error else theme.ink
            view.text(6, top + 2, _ascii(note), colour, 1.0, 1)
        else:
            left = (
                f"{self.session.path.name if self.session.path else '<stdin>'}"
                f"  {self.session.summary()}  x{scale:g}"
            )
            view.text(6, top + 2, _ascii(left), theme.ink_muted, 1.0, 1)
        right = f"{stats.nodes}n {stats.edges}e"
        if self.selected is not None:
            right = f"{self.selected.text} | " + right
        view.text(
            self.width - raster.text_width(_ascii(right), 1) - 6, top + 2,
            _ascii(right), theme.ink, 1.0, 1,
        )
        if self.show_help:
            box_h = len(HELP) * 18 + 14
            view.fill_round_rect(20, 20, 560, box_h, 8, theme.cluster_fill, 0.96)
            view.stroke_round_rect(20, 20, 560, box_h, 8, 2, theme.cluster_stroke, 1.0)
            for index, line in enumerate(HELP):
                view.text(34, 30 + index * 18, _ascii(line), theme.ink, 1.0, 1)

    def present(self, view) -> None:
        rgb = view.rgb_bytes()
        if self._presenter is not None:
            self._presenter.present(
                bytes(rgb), view.width, view.height, self.columns, self.rows - 1
            )
            self._presenter.flush()
            return
        self._direct(bytes(rgb), view.width, view.height)

    def _direct(self, rgb: bytes, width: int, height: int) -> None:
        """One chunked graphics escape. Enough when the presenter is absent."""
        import base64  # noqa: PLC0415
        import zlib  # noqa: PLC0415

        sys.stdout.write("\x1b[H")
        payload = base64.b64encode(zlib.compress(rgb, 1))
        first = True
        while payload:
            piece, payload = payload[:4096], payload[4096:]
            control = f"m={1 if payload else 0}"
            if first:
                control = (
                    f"a=T,f=24,o=z,s={width},v={height},"
                    f"c={self.columns},r={self.rows - 1}," + control
                )
                first = False
            sys.stdout.write(f"\x1b_G{control};{piece.decode('ascii')}\x1b\\")
        sys.stdout.flush()

    def draw(self) -> None:
        view = self.frame()
        try:
            self.present(view)
        finally:
            view.close()

    # ---------------------------------------------------------------- input

    def _decode(self, data: str) -> list[object]:
        """Split a read into mouse reports and plain keys, in wire order."""
        events: list[object] = []
        position = 0
        while position < len(data):
            match = _SGR.match(data, position)
            if match:
                code, column, row, kind = match.groups()
                code = int(code)
                events.append(
                    _Mouse(
                        button=code & 3,
                        column=int(column),
                        row=int(row),
                        pressed=kind == "M",
                        motion=bool(code & 32),
                        wheel=(-1 if code == 64 else 1 if code == 65 else 0),
                    )
                )
                position = match.end()
                continue
            events.append(data[position])
            position += 1
        return events

    def key(self, char: str) -> bool:
        self.message = ""
        settings = self.session.settings
        if char in ("q", "\x03", "\x1b"):
            return False
        if char == "?":
            self.show_help = not self.show_help
        elif char == "e":
            self.session.cycle("engine")
        elif char == "E":
            self.session.cycle("engine", -1)
        elif char == "d":
            self.session.cycle("direction")
        elif char == "t":
            self.session.cycle("theme")
        elif char == "c":
            self.session.update(curved=not settings.curved)
        elif char in ("+", "="):
            self.zoom_by(1.25, self.width // 2, self.height // 2)
        elif char in ("-", "_"):
            self.zoom_by(0.8, self.width // 2, self.height // 2)
        elif char == "f":
            self.fit()
        elif char == "0":
            self.pan_x = self.pan_y = 0.0
        elif char == "r":
            self.message = "reloaded" if self.session.reload(force=True) else "unchanged"
        elif char == "w":
            self.export()
        return True

    def zoom_by(self, factor: float, at_x: float, at_y: float) -> None:
        """Zoom about a point, so the pixel under the pointer stays put."""
        old = self.session.settings.scale
        new = round(max(0.1, min(8.0, old * factor)), 3)
        if new == old:
            return
        ratio = new / old
        self.pan_x = (self.pan_x + at_x) * ratio - at_x
        self.pan_y = (self.pan_y + at_y) * ratio - at_y
        self.session.update(scale=new)
        self.page()

    def mouse(self, event: _Mouse) -> None:
        if event.wheel:
            self.zoom_by(
                0.85 if event.wheel > 0 else 1.18,
                (event.column - 1) * self.cell_w,
                (event.row - 1) * self.cell_h,
            )
            return
        pixel = ((event.column - 1) * self.cell_w, (event.row - 1) * self.cell_h)
        if event.pressed and not event.motion:
            self.dragging = True
            self.drag_from = pixel
            self.selected = self.at(event.column, event.row)
        elif event.motion and self.dragging:
            self.pan_x -= pixel[0] - self.drag_from[0]
            self.pan_y -= pixel[1] - self.drag_from[1]
            self.drag_from = pixel
            self.clamp()
        elif not event.pressed:
            self.dragging = False
        else:
            self.hovered = self.at(event.column, event.row)

    def export(self) -> None:
        if self.session.path is None:
            self.message = "nothing to write beside: the graph came from stdin"
            return
        from .render import raster  # noqa: PLC0415

        from .output import OutputExists, write_new  # noqa: PLC0415

        stem = self.session.path.with_suffix("")
        try:
            write_new(f"{stem}.svg", self.session.svg(), protect=(self.session.path,))
            canvas = self.session.raster()
            try:
                write_new(f"{stem}.png", raster.png_bytes(canvas), protect=(self.session.path,))
            finally:
                canvas.close()
            self.message = f"wrote {Path(stem).name}.svg and .png"
        except OutputExists as error:
            self.message = f"not written: {error}"
        except (OSError, ValueError) as error:
            self.message = f"could not write: {error}"

    # ----------------------------------------------------------------- loop

    def loop(self) -> None:
        self.fit()
        while True:
            self.draw()
            ready, _, _ = select.select([self.fd], [], [], 0.4)
            if not ready:
                if self.session.reload():
                    self.message = "reloaded"
                    self._page_key = None
                    self.fit()
                continue
            data = os.read(self.fd, 4096).decode("utf-8", "replace")
            for event in self._decode(data):
                if isinstance(event, _Mouse):
                    self.mouse(event)
                elif not self.key(event):
                    return


def run(session: Session) -> int:
    """Open the pane viewer. Returns a process exit status."""
    from .render import raster  # noqa: PLC0415

    if not available():
        print(
            "kilix-graphs: this terminal has no graphics protocol. "
            "Use `kilix-graphs tui` for the cell viewer.",
            file=sys.stderr,
        )
        return 3
    if not raster.available():
        print(f"kilix-graphs: {raster.RasterUnavailable.__doc__}", file=sys.stderr)
        print("kilix-graphs: the pane viewer draws pixels and needs soft-raster 0.5. "
              "Use `kilix-graphs tui`.", file=sys.stderr)
        return 3

    pane = _Pane(session)
    try:
        from kitty_frame_presenter import FramePresenter  # noqa: PLC0415

        pane._presenter = FramePresenter(sys.stdout, image_id=23, max_fps=30)
    except ImportError:
        pane._presenter = None  # the direct escape path covers it

    fd = sys.stdin.fileno()
    saved = termios.tcgetattr(fd)
    sys.stdout.write(ALT_SCREEN_ON + MOUSE_ON)
    sys.stdout.flush()
    try:
        tty.setraw(fd)
        pane.loop()
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, saved)
        sys.stdout.write(MOUSE_OFF + ALT_SCREEN_OFF)
        sys.stdout.flush()
        if pane._page is not None:
            pane._page.close()
    return 0


def open_file(
    path: str | Path | None,
    source: str | None = None,
    settings: Settings | None = None,
    fmt: str | None = None,
) -> int:
    return run(Session(path=path, source=source, settings=settings, fmt=fmt))
