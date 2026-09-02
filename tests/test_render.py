"""The scene, the text backend and the SVG exporter."""

from __future__ import annotations

import unittest

from kilix_graphs import compose, layout, parse, render
from kilix_graphs.compose import ComposeOptions
from kilix_graphs.render import ASCII, UNICODE, TextOptions, render_svg, render_text
from kilix_graphs.scene import Polygon, Polyline, Rect, Scene, Style, Text
from kilix_graphs.theme import DARK, LIGHT


def drawn(source: str, **kwargs: object) -> str:
    graph = parse.loads(source)
    layout.run(graph, "layered")
    scene = compose(graph, ComposeOptions(curved=False))
    return render_text(scene, TextOptions(**kwargs))  # type: ignore[arg-type]


class SceneTests(unittest.TestCase):
    def test_paint_order_is_by_layer_then_insertion(self) -> None:
        scene = Scene()
        first = scene.add(Rect(layer=10, x=1))
        second = scene.add(Rect(layer=0, x=2))
        third = scene.add(Rect(layer=10, x=3))
        self.assertEqual(scene.ordered(), [second, first, third])

    def test_bounds_cover_every_kind_of_operation(self) -> None:
        scene = Scene()
        scene.add(Rect(x=0, y=0, w=10, h=10))
        scene.add(Polyline(points=((-5.0, 20.0), (30.0, 25.0))))
        self.assertEqual(scene.bounds(), (-5.0, 0.0, 30.0, 25.0))


class ComposeTests(unittest.TestCase):
    def test_layers_put_clusters_under_edges_under_nodes_under_labels(self) -> None:
        graph = parse.loads("group g: G\n    a -> b: x\n")
        layout.run(graph, "layered")
        scene = compose(graph)
        layers = {type(op).__name__: op.layer for op in scene}
        self.assertLess(layers["Polyline"], layers["Rect"])
        self.assertLess(layers["Rect"], layers["Text"])

    def test_a_directed_edge_gets_an_arrowhead_and_an_undirected_one_does_not(self) -> None:
        directed = parse.loads("a -> b")
        layout.run(directed, "layered")
        self.assertTrue(any(isinstance(op, Polygon) for op in compose(directed)))

        plain = parse.loads("graph\na -- b")
        layout.run(plain, "layered")
        self.assertFalse(any(isinstance(op, Polygon) for op in compose(plain)))

    def test_the_theme_reaches_the_scene(self) -> None:
        graph = parse.loads("a -> b")
        layout.run(graph, "layered")
        self.assertEqual(compose(graph, ComposeOptions(theme=LIGHT)).background, LIGHT.surface)
        self.assertEqual(compose(graph, ComposeOptions(theme=DARK)).background, DARK.surface)

    def test_clusters_take_distinct_colours(self) -> None:
        graph = parse.loads("group one: One\n    a\ngroup two: Two\n    b\n")
        layout.run(graph, "layered")
        outlines = {
            op.style.stroke for op in compose(graph) if isinstance(op, Rect) and op.style.stroke
        }
        self.assertGreaterEqual(len(outlines), 2)


class TextBackendTests(unittest.TestCase):
    def test_a_node_label_sits_inside_its_own_outline(self) -> None:
        lines = drawn("alpha -> beta").splitlines()
        for name in ("alpha", "beta"):
            row = next(i for i, line in enumerate(lines) if name in line)
            self.assertIn("│", lines[row])
            self.assertTrue(lines[row - 1].strip().startswith(("╭", "│")))
            self.assertTrue(lines[row + 1].strip().startswith(("╰", "│")))

    def test_an_edge_label_never_displaces_a_node_label(self) -> None:
        drawing = drawn("alpha -> beta: relates")
        self.assertIn("alpha", drawing)
        self.assertIn("beta", drawing)
        self.assertIn("relates", drawing)

    def test_routes_are_axis_aligned(self) -> None:
        """Diagonals have no glyph that reads as a line, so there must be none."""
        drawing = drawn("a -> b\na -> c")
        self.assertNotIn("\\", drawing)
        self.assertNotIn("/", drawing)

    def test_ascii_output_carries_no_box_drawing(self) -> None:
        drawing = drawn("a -> b", charset=ASCII)
        self.assertTrue(drawing.isascii())
        self.assertIn("+", drawing)

    def test_unicode_output_uses_box_drawing(self) -> None:
        self.assertIn("╭", drawn("a -> b", charset=UNICODE))

    def test_a_directed_edge_ends_in_an_arrow(self) -> None:
        self.assertIn("▼", drawn("a -> b"))

    def test_a_point_node_is_a_dot_not_an_empty_box(self) -> None:
        drawing = drawn("start {shape=point}\nstart -> idle")
        self.assertIn("●", drawing)
        self.assertNotIn("╭─╮", drawing)  # the 3x3 outline it used to draw
        ascii_drawing = drawn("start {shape=point}\nstart -> idle", charset=ASCII)
        self.assertIn("o", ascii_drawing)

    def test_colour_emits_ansi_and_resets(self) -> None:
        drawing = drawn("a -> b", colour=True)
        self.assertIn("\x1b[38;2;", drawing)
        self.assertTrue(all("\x1b[0m" in line for line in drawing.splitlines() if "\x1b[" in line))

    def test_output_is_bounded_by_the_declared_maximum(self) -> None:
        scene = Scene(width=100000, height=100000)
        scene.add(Rect(x=0, y=0, w=99999, h=99999, style=Style(stroke=0xFFFFFF)))
        lines = render_text(scene, TextOptions(max_columns=20, max_rows=10)).splitlines()
        self.assertLessEqual(len(lines), 10)
        self.assertLessEqual(max(len(line) for line in lines), 20)


class TextChartTests(unittest.TestCase):
    """A chart in a cell grid needs a different treatment from a graph."""

    def _chart_text(self, mark: str = "line", **kwargs: object) -> str:
        from kilix_graphs.chart import Chart, compose_chart

        chart = Chart(
            title="t", categories=["a", "b", "c", "d"], width=480, height=240
        )
        chart.add("one", [1.0, 3.0, 2.0, 4.0], mark)
        return render_text(compose_chart(chart), TextOptions(**kwargs))  # type: ignore[arg-type]

    def test_gridlines_are_dropped(self) -> None:
        """A gridline in a cell grid is as loud as the data crossing it."""
        drawing = self._chart_text()
        # A dropped grid leaves rows that are the axis and its label only.
        rows = [line for line in drawing.splitlines() if "│" in line]
        self.assertTrue(rows)
        self.assertFalse(any(line.count("─") > 20 and "└" not in line for line in rows))

    def test_a_line_is_plotted_at_sub_cell_resolution(self) -> None:
        drawing = self._chart_text()
        self.assertTrue(
            any(0x2800 <= ord(char) <= 0x28FF for char in drawing),
            "expected braille dots in the plotted line",
        )

    def test_ascii_falls_back_to_markers_rather_than_braille(self) -> None:
        drawing = self._chart_text(charset=ASCII)
        self.assertTrue(drawing.isascii())
        self.assertIn("*", drawing)

    def test_bars_are_solid(self) -> None:
        self.assertIn("█", self._chart_text("bar"))
        self.assertIn("#", self._chart_text("bar", charset=ASCII))

    def test_the_axis_and_its_labels_survive(self) -> None:
        drawing = self._chart_text()
        self.assertIn("└", drawing)
        for label in ("a", "b", "c", "d"):
            self.assertIn(label, drawing)


class SvgTests(unittest.TestCase):
    def test_a_document_is_well_formed_and_sized(self) -> None:
        from xml.etree import ElementTree

        graph = parse.loads("a -> b: x")
        layout.run(graph, "layered")
        scene = compose(graph)
        document = render_svg(scene)
        root = ElementTree.fromstring(document)
        self.assertTrue(root.tag.endswith("svg"))
        self.assertEqual(root.get("width"), f"{scene.width:g}")

    def test_text_is_escaped(self) -> None:
        scene = Scene(100, 100)
        scene.add(Text(x=10, y=10, value="a < b & c", style=Style(fill=0xFFFFFF)))
        document = render_svg(scene)
        self.assertIn("a &lt; b &amp; c", document)
        self.assertNotIn("<text>a < b", document)

    def test_a_dash_pattern_survives(self) -> None:
        scene = Scene(100, 100)
        scene.add(
            Polyline(points=((0.0, 0.0), (10.0, 10.0)), style=Style(stroke=0xFF0000, dash=(4, 2)))
        )
        self.assertIn('stroke-dasharray="4 2"', render_svg(scene))


class ProbeTests(unittest.TestCase):
    def test_the_probe_never_raises_and_names_a_renderer(self) -> None:
        self.assertIn(render.choose(), ("raster", "text"))
        self.assertEqual(render.choose("svg"), "svg")


if __name__ == "__main__":
    unittest.main()
