"""The no-clobber writer: nothing that exists is ever replaced by accident."""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

from kilix_graphs import cli
from kilix_graphs.output import OutputExists, write_new
from kilix_graphs.view import Session

GRAPH = "digraph\n    a -> b\n"


class WriteNewTests(unittest.TestCase):
    def setUp(self) -> None:
        self.dir = Path(tempfile.mkdtemp(prefix="kilix-graphs-output-"))

    def tearDown(self) -> None:
        for entry in self.dir.iterdir():
            entry.unlink()
        self.dir.rmdir()

    def test_it_creates_a_fresh_file(self) -> None:
        target = self.dir / "out.svg"
        self.assertEqual(write_new(target, "<svg/>"), target)
        self.assertEqual(target.read_text(encoding="utf-8"), "<svg/>")

    def test_an_existing_file_is_refused_and_untouched(self) -> None:
        target = self.dir / "out.svg"
        target.write_text("theirs", encoding="utf-8")
        with self.assertRaises(OutputExists):
            write_new(target, "mine")
        self.assertEqual(target.read_text(encoding="utf-8"), "theirs")

    def test_a_symlink_is_refused_even_with_force(self) -> None:
        elsewhere = self.dir / "elsewhere.txt"
        elsewhere.write_text("precious", encoding="utf-8")
        link = self.dir / "out.svg"
        link.symlink_to(elsewhere)
        with self.assertRaises(OutputExists):
            write_new(link, "mine")
        with self.assertRaises(OutputExists):
            write_new(link, "mine", force=True)
        self.assertEqual(elsewhere.read_text(encoding="utf-8"), "precious")
        self.assertTrue(link.is_symlink())

    def test_a_dangling_symlink_is_refused(self) -> None:
        link = self.dir / "out.svg"
        link.symlink_to(self.dir / "nowhere")
        with self.assertRaises(OutputExists):
            write_new(link, "mine", force=True)
        self.assertFalse((self.dir / "nowhere").exists())

    def test_force_replaces_a_regular_file_atomically(self) -> None:
        target = self.dir / "out.svg"
        target.write_text("old", encoding="utf-8")
        write_new(target, "new", force=True)
        self.assertEqual(target.read_text(encoding="utf-8"), "new")
        self.assertEqual(sorted(p.name for p in self.dir.iterdir()), ["out.svg"])

    def test_the_input_is_never_replaced(self) -> None:
        source = self.dir / "g.svg"
        source.write_text("source", encoding="utf-8")
        with self.assertRaises(OutputExists):
            write_new(source, "rendered", force=True, protect=(source,))
        self.assertEqual(source.read_text(encoding="utf-8"), "source")


class CommandLineOutputTests(unittest.TestCase):
    def setUp(self) -> None:
        self.dir = Path(tempfile.mkdtemp(prefix="kilix-graphs-cli-"))
        self.source = self.dir / "g.kg"
        self.source.write_text(GRAPH, encoding="utf-8")

    def tearDown(self) -> None:
        for entry in self.dir.iterdir():
            entry.unlink()
        self.dir.rmdir()

    def test_dash_o_refuses_an_existing_file(self) -> None:
        out = self.dir / "g.svg"
        out.write_text("theirs", encoding="utf-8")
        self.assertEqual(cli.main(["draw", str(self.source), "-o", str(out)]), 2)
        self.assertEqual(out.read_text(encoding="utf-8"), "theirs")

    def test_dash_o_with_force_replaces_a_regular_file(self) -> None:
        out = self.dir / "g.svg"
        out.write_text("theirs", encoding="utf-8")
        self.assertEqual(cli.main(["draw", str(self.source), "-o", str(out), "--force"]), 0)
        self.assertIn("<svg", out.read_text(encoding="utf-8"))

    def test_dash_o_refuses_a_symlink_even_with_force(self) -> None:
        elsewhere = self.dir / "elsewhere.txt"
        elsewhere.write_text("precious", encoding="utf-8")
        out = self.dir / "g.svg"
        out.symlink_to(elsewhere)
        self.assertEqual(cli.main(["draw", str(self.source), "-o", str(out), "--force"]), 2)
        self.assertEqual(elsewhere.read_text(encoding="utf-8"), "precious")

    def test_dash_o_refuses_the_input(self) -> None:
        self.assertEqual(
            cli.main(["draw", str(self.source), "-o", str(self.source), "--force"]), 2
        )
        self.assertEqual(self.source.read_text(encoding="utf-8"), GRAPH)

    def test_layout_and_convert_refuse_existing_outputs_too(self) -> None:
        out = self.dir / "positions.json"
        out.write_text("theirs", encoding="utf-8")
        self.assertEqual(cli.main(["layout", str(self.source), "-o", str(out)]), 2)
        self.assertEqual(cli.main(["convert", str(self.source), "--to", "jgf", "-o", str(out)]), 2)
        self.assertEqual(out.read_text(encoding="utf-8"), "theirs")


class ViewerExportTests(unittest.TestCase):
    """The `w` key writes beside the source and refuses whatever is there."""

    def setUp(self) -> None:
        self.dir = Path(tempfile.mkdtemp(prefix="kilix-graphs-viewer-"))

    def tearDown(self) -> None:
        for entry in self.dir.iterdir():
            entry.unlink()
        self.dir.rmdir()

    def _viewer(self, path: Path):
        from tests.test_interfaces import _StubScreen  # noqa: PLC0415
        from kilix_graphs import tui  # noqa: PLC0415

        return tui._Viewer(_StubScreen(), Session(path=path))

    def test_an_existing_svg_beside_the_source_is_kept(self) -> None:
        source = self.dir / "g.kg"
        source.write_text(GRAPH, encoding="utf-8")
        existing = self.dir / "g.svg"
        existing.write_text("theirs", encoding="utf-8")
        viewer = self._viewer(source)
        viewer.export()
        self.assertIn("not written", viewer.message)
        self.assertEqual(existing.read_text(encoding="utf-8"), "theirs")

    def test_a_symlink_beside_the_source_is_not_written_through(self) -> None:
        source = self.dir / "g.kg"
        source.write_text(GRAPH, encoding="utf-8")
        elsewhere = self.dir / "elsewhere.txt"
        elsewhere.write_text("precious", encoding="utf-8")
        (self.dir / "g.svg").symlink_to(elsewhere)
        viewer = self._viewer(source)
        viewer.export()
        self.assertIn("not written", viewer.message)
        self.assertEqual(elsewhere.read_text(encoding="utf-8"), "precious")

    def test_a_source_that_is_already_the_svg_is_never_overwritten(self) -> None:
        source = self.dir / "h.svg"
        source.write_text("<svg>hand drawn</svg>", encoding="utf-8")
        viewer = self._viewer(source)
        viewer.export()
        self.assertIn("not written", viewer.message)
        self.assertEqual(source.read_text(encoding="utf-8"), "<svg>hand drawn</svg>")


if __name__ == "__main__":
    unittest.main()
