"""Import boundaries and the CLI.

The import-direction test is the one that keeps `layout` liftable into
`kilix-modules` the day a second consumer appears. It is cheap to keep true
and expensive to restore once broken.
"""

from __future__ import annotations

import ast
import io
import json
import pathlib
import subprocess
import sys
import unittest
from contextlib import redirect_stdout

from kilix_graphs import cli

SOURCE = pathlib.Path(__file__).resolve().parent.parent / "src" / "kilix_graphs"


def imports_of(path: pathlib.Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            found.add(node.module.lstrip("."))
            if node.level and not node.module:
                continue
        elif isinstance(node, ast.Import):
            found.update(alias.name for alias in node.names)
        if isinstance(node, ast.ImportFrom) and node.level:
            # A relative import: `from ..scene import X` arrives as "scene".
            found.add((node.module or "").split(".")[0])
    return {name for name in found if name}


class BoundaryTests(unittest.TestCase):
    def test_layout_and_model_do_not_know_about_rendering(self) -> None:
        forbidden = {"scene", "render", "compose", "theme", "chart", "cli"}
        for path in [SOURCE / "model.py", *(SOURCE / "layout").glob("*.py")]:
            with self.subTest(module=path.name):
                self.assertEqual(imports_of(path) & forbidden, set())

    def test_the_chart_surface_does_not_know_about_renderers(self) -> None:
        for path in (SOURCE / "chart").glob("*.py"):
            with self.subTest(module=path.name):
                self.assertNotIn("render", imports_of(path))

    def test_nothing_outside_the_raster_backend_imports_soft_raster(self) -> None:
        allowed = {SOURCE / "render" / "raster.py", SOURCE / "cli.py"}
        for path in SOURCE.rglob("*.py"):
            if path in allowed:
                continue
            with self.subTest(module=str(path.relative_to(SOURCE))):
                self.assertNotIn("soft_raster", imports_of(path))

    def test_the_package_imports_with_no_third_party_dependency(self) -> None:
        """Everything but the raster backend must run on a bare interpreter."""
        result = subprocess.run(
            [sys.executable, "-S", "-c",
             "import sys; sys.path.insert(0, 'src'); "
             "import kilix_graphs; "
             "g = kilix_graphs.parse.loads('a -> b'); "
             "kilix_graphs.layout.run(g); "
             "print(len(kilix_graphs.render_text(kilix_graphs.compose(g))))"],
            capture_output=True,
            text=True,
            cwd=SOURCE.parent.parent,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertGreater(int(result.stdout.strip()), 0)


class CliTests(unittest.TestCase):
    def run_cli(self, *argv: str) -> tuple[int, str]:
        buffer = io.StringIO()
        with redirect_stdout(buffer):
            code = cli.main(list(argv))
        return code, buffer.getvalue()

    def _write(self, name: str, text: str) -> str:
        import tempfile

        directory = tempfile.mkdtemp()
        path = pathlib.Path(directory) / name
        path.write_text(text, encoding="utf-8")
        return str(path)

    def test_draw_to_text(self) -> None:
        path = self._write("g.kg", "a -> b\n")
        code, out = self.run_cli("draw", path, "--renderer", "text", "--no-colour")
        self.assertEqual(code, 0)
        self.assertIn("a", out)
        self.assertIn("b", out)

    def test_draw_to_svg(self) -> None:
        path = self._write("g.kg", "a -> b\n")
        code, out = self.run_cli("draw", path, "--renderer", "svg")
        self.assertEqual(code, 0)
        self.assertIn("<svg", out)

    def test_layout_emits_positions(self) -> None:
        path = self._write("g.kg", "a -> b\n")
        code, out = self.run_cli("layout", path)
        self.assertEqual(code, 0)
        document = json.loads(out)
        self.assertIn("x", document["graph"]["nodes"]["a"]["metadata"])

    def test_convert_round_trips_through_dot(self) -> None:
        path = self._write("g.kg", "a -> b: x\n")
        code, dot_text = self.run_cli("convert", path, "--to", "dot")
        self.assertEqual(code, 0)
        from kilix_graphs.parse import dot as dot_parser

        graph = dot_parser.loads(dot_text)
        self.assertEqual([(e.tail, e.head, e.label) for e in graph.edges], [("a", "b", "x")])

    def test_chart_from_csv(self) -> None:
        path = self._write("d.csv", "day,one,two\nMon,1,4\nTue,2,5\n")
        code, out = self.run_cli("chart", path, "--renderer", "svg", "--title", "T")
        self.assertEqual(code, 0)
        self.assertIn(">T<", out)
        self.assertIn(">Mon<", out)

    def test_chart_rejects_a_csv_with_no_data(self) -> None:
        path = self._write("d.csv", "day,one\n")
        code, _ = self.run_cli("chart", path, "--renderer", "svg")
        self.assertEqual(code, 1)

    def test_an_empty_graph_is_reported_rather_than_drawn(self) -> None:
        path = self._write("g.kg", "# nothing here\n")
        code, _ = self.run_cli("draw", path, "--renderer", "text")
        self.assertEqual(code, 1)

    def test_a_missing_file_is_reported(self) -> None:
        self.assertEqual(cli.main(["draw", "/nonexistent/x.kg"]), 2)

    def test_doctor_reports_without_raising(self) -> None:
        code, out = self.run_cli("doctor")
        self.assertEqual(code, 0)
        self.assertIn("chosen renderer", out)


if __name__ == "__main__":
    unittest.main()
