"""The interactive terminal viewer: `kilix-graphs tui FILE`.

A drawing you can only regenerate from the command line is a drawing you
compare by memory. This keeps the file open, redraws it when it changes on
disk, and puts every setting on one key — so "what does this look like
left-to-right, force-directed, with straight edges?" is three keystrokes
rather than three command lines and three scrollbacks.

It draws through the same cell renderer the CLI's text backend uses, so what
you see here is exactly what `--renderer text` writes. Colour comes from the
scene rather than from ANSI escapes: curses owns the attributes, and handing
it an escape sequence prints the escape.

Nothing here needs `soft-raster`. Exporting pixels does, and says so.
"""

from __future__ import annotations

import curses
import time
from pathlib import Path

from .render import ASCII, UNICODE, TextOptions, render_cells
from .view import Session, Settings

__all__ = ["run"]

#: How often to stat the file, in milliseconds. Long enough that an idle
#: viewer is genuinely idle, short enough that a save feels immediate.
POLL_MS = 400

HELP = [
    ("arrows / hjkl", "pan"),
    ("g / G", "top / bottom"),
    ("0", "recentre"),
    ("e / E", "next / previous engine"),
    ("d", "direction (layered and tree)"),
    ("t", "theme"),
    ("c", "curved or straight edges"),
    ("u", "unicode or ascii"),
    ("+ / -", "font scale"),
    ("r", "reload now"),
    ("w", "write beside the source"),
    ("?", "this help"),
    ("q", "quit"),
]


def _xterm256(rgb: int) -> int:
    """Nearest xterm-256 index for a 24-bit colour.

    curses addresses colours by index, and the scene carries 24-bit values.
    The 6x6x6 cube plus the grey ramp is close enough that the drawing keeps
    its identity: a cluster's blue stays blue and stays distinct from the
    orange beside it, which is the whole job of the palette.
    """
    r, g, b = (rgb >> 16) & 255, (rgb >> 8) & 255, rgb & 255
    if abs(r - g) < 12 and abs(g - b) < 12:  # grey enough for the grey ramp
        if r < 8:
            return 16
        if r > 248:
            return 231
        return 232 + (r - 8) * 24 // 247
    level = lambda v: 0 if v < 48 else 1 if v < 115 else (v - 35) // 40  # noqa: E731
    return 16 + 36 * level(r) + 6 * level(g) + level(b)


class _Palette:
    """Lazily allocated curses colour pairs, keyed by 24-bit colour."""

    def __init__(self) -> None:
        self.enabled = False
        self._pairs: dict[int, int] = {}
        self._next = 1
        try:
            curses.start_color()
            curses.use_default_colors()
            self.enabled = curses.has_colors() and curses.COLORS >= 256
        except curses.error:
            self.enabled = False

    def attr(self, rgb: int | None) -> int:
        if rgb is None or not self.enabled:
            return curses.A_NORMAL
        if rgb not in self._pairs:
            if self._next >= min(curses.COLOR_PAIRS, 256):
                return curses.A_NORMAL  # out of pairs: draw it plain
            try:
                curses.init_pair(self._next, _xterm256(rgb), -1)
            except curses.error:
                return curses.A_NORMAL
            self._pairs[rgb] = self._next
            self._next += 1
        return curses.color_pair(self._pairs[rgb])


class _Viewer:
    def __init__(self, screen: "curses._CursesWindow", session: Session) -> None:
        self.screen = screen
        self.session = session
        self.top = 0
        self.left = 0
        self.unicode = True
        self.message = ""
        self.show_help = False
        self.palette = _Palette()
        self._cells: list[list[tuple[str, int | None]]] = []
        self._drawn_for: object = None

    # ------------------------------------------------------------- drawing

    def cells(self) -> list[list[tuple[str, int | None]]]:
        key = (self.session.settings, self.unicode, self.session.source)
        if key != self._drawn_for:
            scene = self.session.scene()
            if scene is None:
                self._cells = []
            else:
                self._cells = render_cells(
                    scene,
                    TextOptions(
                        charset=UNICODE if self.unicode else ASCII,
                        max_columns=4000,
                        max_rows=4000,
                    ),
                )
            self._drawn_for = key
        return self._cells

    def draw(self) -> None:
        self.screen.erase()
        height, width = self.screen.getmaxyx()
        body = max(1, height - 2)
        cells = self.cells()

        if self.session.error and not cells:
            self._centre(body // 2, f"⚠  {self.session.error}", curses.A_BOLD)
        else:
            for row in range(body):
                source = self.top + row
                if source >= len(cells):
                    break
                line = cells[source]
                for col in range(width - 1):
                    index = self.left + col
                    if index >= len(line):
                        break
                    char, colour = line[index]
                    if char == " ":
                        continue
                    try:
                        self.screen.addstr(row, col, char, self.palette.attr(colour))
                    except curses.error:
                        pass  # the bottom-right cell always raises; ignore it

        self._status(height, width)
        if self.show_help:
            self._help(height, width)
        self.screen.noutrefresh()

    def _centre(self, row: int, text: str, attr: int = 0) -> None:
        _, width = self.screen.getmaxyx()
        try:
            self.screen.addnstr(row, max(0, (width - len(text)) // 2), text, width - 1, attr)
        except curses.error:
            pass

    def _status(self, height: int, width: int) -> None:
        stats = self.session.stats
        name = self.session.path.name if self.session.path else "<stdin>"
        left = f" {name}  {self.session.summary()}"
        right = (
            f"{stats.nodes}n {stats.edges}e"
            + (f" {stats.clusters}g" if stats.clusters else "")
            + f"  {self.left},{self.top}  ?=help "
        )
        pad = max(0, width - len(left) - len(right) - 1)
        bar = (left + " " * pad + right)[: width - 1]
        try:
            self.screen.addstr(height - 2, 0, bar, curses.A_REVERSE)
        except curses.error:
            pass
        note = self.message or (f"⚠  {self.session.error}" if self.session.error else "")
        try:
            self.screen.addnstr(height - 1, 0, note, width - 1)
        except curses.error:
            pass

    def _help(self, height: int, width: int) -> None:
        """Draw the key list, clipped to whatever window there is.

        Relying on curses to raise for an out-of-bounds write is not clipping:
        it happens to work, it hides real mistakes in the same except, and a
        stub screen or a different curses is entitled to do something else. On
        a window too small for the whole list it shows as many keys as fit.
        """
        entries = HELP[: max(0, height - 2)]
        if not entries or width < 8:
            return
        rows = len(entries) + 2
        cols = min(max(len(k) + len(v) + 6 for k, v in entries) + 4, width)
        top = max(0, (height - rows) // 2)
        left = max(0, (width - cols) // 2)
        span = max(0, min(cols, width - left))

        for index in range(rows):
            row = top + index
            if row >= height:
                break
            self.screen.addnstr(row, left, " " * span, span, curses.A_REVERSE)
        for index, (key, what) in enumerate(entries):
            row = top + 1 + index
            if row >= height:
                break
            self.screen.addnstr(
                row, min(left + 2, width - 1),
                f"{key:<14} {what}", max(0, span - 4), curses.A_REVERSE,
            )

    # -------------------------------------------------------------- actions

    def clamp(self) -> None:
        cells = self.cells()
        height, width = self.screen.getmaxyx()
        widest = max((len(row) for row in cells), default=0)
        self.top = max(0, min(self.top, max(0, len(cells) - (height - 2))))
        self.left = max(0, min(self.left, max(0, widest - (width - 1))))

    def export(self) -> None:
        if self.session.path is None:
            self.message = "nothing to write beside: the graph came from stdin"
            return
        stem = self.session.path.with_suffix("")
        try:
            svg = Path(f"{stem}.svg")
            svg.write_text(self.session.svg(), encoding="utf-8")
            written = [svg.name]
            try:
                canvas = self.session.raster()
                try:
                    ppm = Path(f"{stem}.ppm")
                    canvas.write_ppm(str(ppm))
                    written.append(ppm.name)
                finally:
                    canvas.close()
            except Exception:  # noqa: BLE001 - pixels are a bonus, not the job
                pass
            self.message = "wrote " + ", ".join(written)
        except (OSError, ValueError) as error:
            self.message = f"could not write: {error}"

    def key(self, code: int) -> bool:
        """Handle one key. Returns False to quit."""
        self.message = ""
        height, width = self.screen.getmaxyx()
        page = max(1, height - 4)

        if code in (ord("q"), 27):
            return False
        if code == ord("?"):
            self.show_help = not self.show_help
            return True
        if self.show_help:  # any other key closes it
            self.show_help = False
            return True

        if code in (curses.KEY_DOWN, ord("j")):
            self.top += 1
        elif code in (curses.KEY_UP, ord("k")):
            self.top -= 1
        elif code in (curses.KEY_RIGHT, ord("l")):
            self.left += 2
        elif code in (curses.KEY_LEFT, ord("h")):
            self.left -= 2
        elif code == curses.KEY_NPAGE:
            self.top += page
        elif code == curses.KEY_PPAGE:
            self.top -= page
        elif code in (ord("g"), curses.KEY_HOME):
            self.top = 0
        elif code in (ord("G"), curses.KEY_END):
            self.top = len(self.cells())
        elif code == ord("0"):
            self.top = self.left = 0
        elif code == ord("e"):
            self.session.cycle("engine")
        elif code == ord("E"):
            self.session.cycle("engine", -1)
        elif code == ord("d"):
            self.session.cycle("direction")
        elif code == ord("t"):
            self.session.cycle("theme")
        elif code == ord("c"):
            self.session.update(curved=not self.session.settings.curved)
        elif code == ord("u"):
            self.unicode = not self.unicode
        elif code in (ord("+"), ord("=")):
            self.session.update(font_scale=min(4, self.session.settings.font_scale + 1))
        elif code in (ord("-"), ord("_")):
            self.session.update(font_scale=max(1, self.session.settings.font_scale - 1))
        elif code == ord("r"):
            self.message = "reloaded" if self.session.reload(force=True) else "unchanged"
        elif code == ord("w"):
            self.export()
        self.clamp()
        return True

    def loop(self) -> None:
        self.screen.timeout(POLL_MS)
        last_poll = 0.0
        while True:
            self.draw()
            # The screen refresh lives here rather than in draw(), so drawing
            # can be exercised against a stub screen: doupdate() is a global
            # curses call and needs a real initscr().
            curses.doupdate()
            code = self.screen.getch()
            if code == -1:  # the poll timeout expired
                now = time.monotonic()
                if now - last_poll >= POLL_MS / 1000:
                    last_poll = now
                    if self.session.reload():
                        self.message = "reloaded"
                        self.clamp()
                continue
            if code == curses.KEY_RESIZE:
                self.clamp()
                continue
            if not self.key(code):
                return


def run(session: Session) -> int:
    """Open the viewer. Returns a process exit status."""

    def main(screen: "curses._CursesWindow") -> None:
        curses.curs_set(0)
        screen.keypad(True)
        _Viewer(screen, session).loop()

    try:
        curses.wrapper(main)
    except curses.error as error:
        print(f"kilix-graphs: the terminal will not do this: {error}")
        return 3
    return 0


def open_file(
    path: str | Path | None,
    source: str | None = None,
    settings: Settings | None = None,
    fmt: str | None = None,
) -> int:
    return run(Session(path=path, source=source, settings=settings, fmt=fmt))
