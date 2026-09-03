"""The two interactive interfaces.

The TUI is tested against a stub screen rather than a real terminal: what
matters is which keys change which setting and that nothing draws outside the
window, and a pseudo-terminal answers neither question without scraping escape
sequences. The GUI is tested against a real Tk under whatever display is
available, and skips when there is none.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from kilix_graphs.view import Session


def temp_file(text: str, name: str = "g.kg") -> Path:
    path = Path(tempfile.mkdtemp()) / name
    path.write_text(text, encoding="utf-8")
    return path


GRAPH = "digraph\ngroup g: G\n    a -> b\nb -> c: x\nc -> a\n"


# --------------------------------------------------------------------- TUI


class _StubScreen:
    """Enough curses for the viewer: a size, and a record of what was drawn."""

    def __init__(self, height: int = 24, width: int = 80) -> None:
        self.height = height
        self.width = width
        self.drawn: list[tuple[int, int, str]] = []

    def getmaxyx(self) -> tuple[int, int]:
        return (self.height, self.width)

    def erase(self) -> None:
        self.drawn.clear()

    def addstr(self, row: int, col: int, text: str, attr: int = 0) -> None:
        if not (0 <= row < self.height and 0 <= col < self.width):
            raise AssertionError(f"drew outside the window at {row},{col}")
        self.drawn.append((row, col, text))

    def addnstr(self, row: int, col: int, text: str, n: int, attr: int = 0) -> None:
        self.addstr(row, col, text[:n], attr)

    def noutrefresh(self) -> None:
        pass

    def timeout(self, ms: int) -> None:
        pass

    def keypad(self, flag: bool) -> None:
        pass


class TuiTests(unittest.TestCase):
    def viewer(self, source: str = GRAPH, **kwargs):
        from kilix_graphs import tui

        screen = _StubScreen(**kwargs)
        return tui._Viewer(screen, Session(source=source)), screen

    def test_every_documented_key_is_handled(self) -> None:
        from kilix_graphs import tui

        viewer, _ = self.viewer()
        for keys, _what in tui.HELP:
            for key in keys.split(" / "):
                if len(key) != 1 or key == "q":
                    continue
                self.assertTrue(viewer.key(ord(key)), f"{key!r} quit unexpectedly")
        # ...and the one that does quit, does.
        self.assertFalse(viewer.key(ord("q")))

    def test_keys_change_the_settings_they_claim_to(self) -> None:
        viewer, _ = self.viewer()
        viewer.key(ord("e"))
        self.assertNotEqual(viewer.session.settings.engine, "layered")
        viewer.key(ord("E"))
        self.assertEqual(viewer.session.settings.engine, "layered")
        viewer.key(ord("t"))
        self.assertEqual(viewer.session.settings.theme, "light")
        curved = viewer.session.settings.curved
        viewer.key(ord("c"))
        self.assertNotEqual(viewer.session.settings.curved, curved)

    def test_font_scale_is_clamped_at_both_ends(self) -> None:
        viewer, _ = self.viewer()
        for _ in range(10):
            viewer.key(ord("+"))
        self.assertLessEqual(viewer.session.settings.font_scale, 8)
        for _ in range(20):
            viewer.key(ord("-"))
        self.assertGreaterEqual(viewer.session.settings.font_scale, 1)

    def test_q_and_escape_quit(self) -> None:
        viewer, _ = self.viewer()
        self.assertFalse(viewer.key(ord("q")))
        viewer, _ = self.viewer()
        self.assertFalse(viewer.key(27))

    def test_help_opens_and_any_key_closes_it(self) -> None:
        viewer, _ = self.viewer()
        viewer.key(ord("?"))
        self.assertTrue(viewer.show_help)
        viewer.key(ord("j"))
        self.assertFalse(viewer.show_help)

    def test_panning_never_leaves_the_drawing(self) -> None:
        viewer, _ = self.viewer()
        for _ in range(200):
            viewer.key(ord("j"))
            viewer.key(ord("l"))
        self.assertGreaterEqual(viewer.top, 0)
        self.assertGreaterEqual(viewer.left, 0)
        viewer.key(ord("0"))
        self.assertEqual((viewer.top, viewer.left), (0, 0))

    def test_it_draws_inside_the_window_at_any_size(self) -> None:
        """_StubScreen raises if anything is drawn out of bounds."""
        for height, width in ((24, 80), (6, 20), (60, 200), (3, 8)):
            with self.subTest(size=(height, width)):
                viewer, _ = self.viewer(height=height, width=width)
                viewer.palette.enabled = False
                viewer.draw()
                viewer.key(ord("?"))
                viewer.draw()

    def test_a_broken_file_is_shown_rather_than_raised(self) -> None:
        viewer, screen = self.viewer(source="a -> \n")
        viewer.palette.enabled = False
        viewer.draw()
        self.assertTrue(any("⚠" in text for _, _, text in screen.drawn))

    def test_export_needs_somewhere_to_write(self) -> None:
        viewer, _ = self.viewer()
        viewer.export()
        self.assertIn("stdin", viewer.message)

    def test_export_writes_beside_the_source(self) -> None:
        from kilix_graphs import tui

        path = temp_file(GRAPH)
        viewer = tui._Viewer(_StubScreen(), Session(path=path))
        viewer.export()
        self.assertIn("wrote", viewer.message)
        self.assertTrue(path.with_suffix(".svg").exists())

    def test_the_xterm256_mapping_keeps_colours_apart(self) -> None:
        from kilix_graphs.tui import _xterm256

        from kilix_graphs.theme import DARK

        indexes = {_xterm256(c) for c in DARK.series}
        self.assertEqual(len(indexes), len(DARK.series), "two series share a cell colour")
        self.assertTrue(all(0 <= i <= 255 for i in indexes))


# --------------------------------------------------------------------- GUI


def _has_display() -> bool:
    return bool(os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY"))


class GuiTests(unittest.TestCase):
    def setUp(self) -> None:
        try:
            import tkinter  # noqa: PLC0415, F401
        except ImportError:
            self.skipTest("no tkinter")
        if not _has_display():
            self.skipTest("no display")

    def app(self, source: str = GRAPH, path: Path | None = None):
        from kilix_graphs.gui import _App

        session = Session(path=path) if path else Session(source=source)
        return _App(session)

    def test_the_window_opens_and_shows_the_graph(self) -> None:
        app = self.app()
        try:
            app.root.update()
            self.assertIn("nodes", app.status.cget("text"))
        finally:
            app.root.destroy()

    def test_direction_is_offered_only_where_it_means_something(self) -> None:
        app = self.app()
        try:
            app.engine.set("force")
            app.apply()
            self.assertEqual(str(app.direction_box.cget("state")), "disabled")
            app.engine.set("layered")
            app.apply()
            self.assertEqual(str(app.direction_box.cget("state")), "readonly")
        finally:
            app.root.destroy()

    def test_the_controls_reach_the_session(self) -> None:
        app = self.app()
        try:
            app.theme.set("light")
            app.apply()
            self.assertEqual(app.session.settings.theme, "light")
            app._cycle("engine", 1)
            self.assertEqual(app.engine.get(), app.session.settings.engine)
        finally:
            app.root.destroy()

    def test_a_save_on_disk_reaches_the_window(self) -> None:
        path = temp_file(GRAPH)
        app = self.app(path=path)
        try:
            app.root.update()
            before = app.status.cget("text")
            path.write_text("digraph\nonly -> two\n")
            app._poll()
            app.root.update()
            self.assertNotEqual(app.status.cget("text"), before)
            self.assertIn("2 nodes", app.status.cget("text"))
        finally:
            app.root.destroy()

    def test_fit_puts_the_whole_drawing_on_screen(self) -> None:
        from kilix_graphs.render import raster

        if not raster.available():
            self.skipTest("fit sizes the pixel canvas, which needs soft-raster")
        app = self.app()
        try:
            app.root.update()
            app.zoom.set(6.0)
            app.apply()
            app.fit()
            scene = app.session.scene()
            self.assertLessEqual(
                scene.width * app.session.settings.scale,
                app.canvas.winfo_width() + 1,
            )
            self.assertGreaterEqual(app.session.settings.scale, 0.25)
        finally:
            app.root.destroy()

    def test_export_writes_each_format(self) -> None:
        from kilix_graphs.render import raster

        app = self.app()
        try:
            directory = Path(tempfile.mkdtemp())
            app._write(directory / "out.svg")
            self.assertGreater((directory / "out.svg").stat().st_size, 100)
            if raster.available():
                for name in ("out.png", "out.ppm"):
                    app._write(directory / name)
                    self.assertGreater((directory / name).stat().st_size, 100)
        finally:
            app.root.destroy()

    def test_fit_does_nothing_against_an_unmapped_canvas(self) -> None:
        """The text fallback hides the canvas; fitting to it would set a
        nonsense zoom from a two-pixel viewport."""
        app = self.app()
        try:
            app._show_text("pretending")
            app.root.update_idletasks()
            before = app.session.settings.scale
            app.fit()
            self.assertEqual(app.session.settings.scale, before)
        finally:
            app.root.destroy()

    def test_it_falls_back_to_text_when_there_are_no_pixels(self) -> None:
        app = self.app()
        try:
            app._show_text("pretending soft-raster is missing")
            self.assertIn("cell rendering", app.status.cget("text"))
            self.assertIn("─", app.text.get("1.0", "end"))
        finally:
            app.root.destroy()


class GuiWithoutDisplayTests(unittest.TestCase):
    def test_it_says_what_to_do_instead_of_a_traceback(self) -> None:
        """The message a user gets over ssh has to name the alternative."""
        result = subprocess.run(
            [sys.executable, "-c",
             "import sys; sys.path.insert(0, 'src');"
             "from kilix_graphs import gui; from kilix_graphs.view import Session;"
             "raise SystemExit(gui.run(Session(source='a -> b')))"],
            capture_output=True, text=True,
            cwd=Path(__file__).resolve().parent.parent,
            env={"PATH": os.environ.get("PATH", "")},  # no DISPLAY
        )
        self.assertEqual(result.returncode, 3)
        self.assertIn("tui", result.stdout + result.stderr)


# --------------------------------------------------------------------- CLI


class CliInterfaceTests(unittest.TestCase):
    def run_cli(self, *argv: str) -> subprocess.CompletedProcess:
        return subprocess.run(
            [sys.executable, "-m", "kilix_graphs.cli", *argv],
            capture_output=True, text=True,
            cwd=Path(__file__).resolve().parent.parent,
            env={**os.environ, "PYTHONPATH": "src"},
        )

    def test_nonsense_numbers_are_refused_rather_than_clamped(self) -> None:
        """`--scale 0` used to exit 0 having written a one-pixel image."""
        path = temp_file("a -> b\n")
        for flag, value in (
            ("--scale", "0"), ("--scale", "-3"), ("--scale", "5000"),
            ("--font-scale", "0"), ("--nodesep", "-400"),
        ):
            with self.subTest(flag=flag, value=value):
                result = self.run_cli("draw", str(path), flag, value, "-r", "text")
                self.assertEqual(result.returncode, 2, f"{flag} {value} was accepted")

    def test_sensible_numbers_are_accepted(self) -> None:
        path = temp_file("a -> b\n")
        result = self.run_cli("draw", str(path), "--scale", "2", "--font-scale", "2",
                              "--nodesep", "60", "-r", "svg")
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_a_directory_as_output_is_a_sentence_not_an_errno(self) -> None:
        path = temp_file("a -> b\n")
        result = self.run_cli("draw", str(path), "-o", tempfile.mkdtemp())
        self.assertEqual(result.returncode, 2)
        self.assertIn("is a directory", result.stderr)

    def test_syntax_documents_the_format_it_claims_to(self) -> None:
        result = self.run_cli("syntax")
        self.assertEqual(result.returncode, 0)
        for expected in ("digraph", "group", "->", "--", "shape=", "# a comment"):
            self.assertIn(expected, result.stdout)

    def test_the_syntax_page_example_actually_parses(self) -> None:
        """A cheat sheet whose example does not work is worse than none."""
        from kilix_graphs import layout, parse

        result = self.run_cli("syntax")
        block = result.stdout.split("A worked example:", 1)[1]
        source = "\n".join(
            line[2:] for line in block.splitlines()
            if line.startswith("  ") or not line.strip()
        ).split("DOT and JSON")[0]
        graph = parse.loads(source)
        self.assertGreater(len(graph.nodes), 4)
        layout.run(graph)

    def test_tui_refuses_to_run_without_a_terminal(self) -> None:
        path = temp_file("a -> b\n")
        result = self.run_cli("tui", str(path))  # captured output: not a tty
        self.assertEqual(result.returncode, 2)
        self.assertIn("terminal", result.stderr)

    def test_every_verb_has_help(self) -> None:
        for verb in ("draw", "chart", "convert", "layout", "tui", "gui", "syntax", "doctor"):
            with self.subTest(verb=verb):
                result = self.run_cli(verb, "--help")
                self.assertEqual(result.returncode, 0)
                self.assertIn("usage:", result.stdout)


if __name__ == "__main__":
    unittest.main()
