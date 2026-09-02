"""The raster backend. Skipped where soft-raster is not installed."""

from __future__ import annotations

import unittest

from kilix_graphs import compose, layout, parse
from kilix_graphs.chart import Chart
from kilix_graphs.chart import compose_chart
from kilix_graphs.render import raster
from kilix_graphs.scene import Polygon, Polyline, Rect, Scene, Style


class RasterTests(unittest.TestCase):
    def setUp(self) -> None:
        if not raster.available():
            self.skipTest("soft-raster 0.5 is not available")

    def test_a_drawing_produces_a_canvas_of_the_scene_size(self) -> None:
        graph = parse.loads("a -> b -> c")
        layout.run(graph, "layered")
        scene = compose(graph)
        canvas = raster.render_raster(scene)
        try:
            self.assertEqual(canvas.width, round(scene.width))
            self.assertEqual(canvas.height, round(scene.height))
        finally:
            canvas.close()

    def test_scale_multiplies_the_pixels_not_the_scene(self) -> None:
        scene = Scene(100, 50, background=0x000000)
        scene.add(Rect(x=10, y=10, w=20, h=20, style=Style(fill=0xFFFFFF)))
        canvas = raster.render_raster(scene, raster.RasterOptions(scale=3.0))
        try:
            self.assertEqual((canvas.width, canvas.height), (300, 150))
        finally:
            canvas.close()

    def test_a_polyline_joint_is_blended_once(self) -> None:
        """The reason sr_polyline exists, asserted through this backend."""
        scene = Scene(120, 60, background=0x000000)
        scene.add(
            Polyline(
                points=((10.0, 30.0), (60.0, 30.0), (110.0, 30.0)),
                style=Style(stroke=0xFFFFFF, width=6.0, alpha=0.5),
            )
        )
        canvas = raster.render_raster(scene)
        try:
            self.assertEqual(canvas[60, 30] & 0xFF, canvas[30, 30] & 0xFF)
        finally:
            canvas.close()

    def test_an_arrowhead_is_anti_aliased(self) -> None:
        scene = Scene(64, 64, background=0x000000)
        scene.add(
            Polygon(
                points=((8.0, 8.0), (56.0, 30.0), (8.0, 52.0)),
                style=Style(fill=0xFFFFFF),
            )
        )
        canvas = raster.render_raster(scene)
        try:
            values = {canvas[x, 14] & 0xFF for x in range(64)}
            self.assertTrue(any(0 < value < 255 for value in values))
        finally:
            canvas.close()

    def test_the_background_reaches_the_corners(self) -> None:
        scene = Scene(40, 40, background=0x123456)
        canvas = raster.render_raster(scene)
        try:
            self.assertEqual(canvas[0, 0] & 0xFFFFFF, 0x123456)
            self.assertEqual(canvas[39, 39] & 0xFFFFFF, 0x123456)
        finally:
            canvas.close()

    def test_a_chart_renders_through_the_same_path(self) -> None:
        chart = Chart(title="t", categories=["a", "b"], width=200, height=140)
        chart.add("s", [1.0, 2.0], "bar")
        canvas = raster.render_raster(compose_chart(chart))
        try:
            self.assertEqual((canvas.width, canvas.height), (200, 140))
        finally:
            canvas.close()

    def test_the_canvas_edge_is_bounded(self) -> None:
        scene = Scene(100000, 100000, background=0x000000)
        canvas = raster.render_raster(scene, raster.RasterOptions(max_edge=256))
        try:
            self.assertEqual((canvas.width, canvas.height), (256, 256))
        finally:
            canvas.close()


class UnavailableTests(unittest.TestCase):
    def test_the_error_says_what_to_do(self) -> None:
        if raster.available():
            self.skipTest("soft-raster is present, so the failure path is unreachable")
        with self.assertRaises(raster.RasterUnavailable) as caught:
            raster.render_raster(Scene(10, 10))
        self.assertIn("--renderer text", str(caught.exception))


if __name__ == "__main__":
    unittest.main()
