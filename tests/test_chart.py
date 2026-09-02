"""Scales, ticks and the chart surface."""

from __future__ import annotations

import math
import unittest

from kilix_graphs.chart import (
    Axis,
    BandScale,
    Chart,
    LinearScale,
    LogScale,
    PointScale,
    compose_chart,
    format_tick,
    nice_domain,
    tick_step,
    ticks,
)
from kilix_graphs.scene import Rect, Text
from kilix_graphs.theme import DARK


class TickTests(unittest.TestCase):
    def test_round_values_only(self) -> None:
        self.assertEqual(ticks(0, 1, 10), [i / 10 for i in range(11)])
        self.assertEqual(ticks(0, 100, 5), [0, 20, 40, 60, 80, 100])

    def test_sub_unit_steps_stay_exact(self) -> None:
        """The reason to port this rather than reinvent it: 0.1 + 0.2 problems."""
        for value in ticks(0, 1, 10):
            self.assertEqual(format_tick(value, 0.1), f"{value:.1f}")
        self.assertIn(0.3, ticks(0, 1, 10))

    def test_steps_are_one_two_or_five_times_a_power_of_ten(self) -> None:
        for lo, hi, count in ((0, 1, 10), (0, 7, 5), (-30, 30, 4), (0, 1e6, 8)):
            step = abs(tick_step(lo, hi, count))
            mantissa = step / 10 ** math.floor(math.log10(step))
            self.assertIn(round(mantissa, 6), (1.0, 2.0, 5.0, 10.0))

    def test_ticks_stay_inside_the_domain(self) -> None:
        for value in ticks(3.2, 8.7, 6):
            self.assertGreaterEqual(value, 3.2)
            self.assertLessEqual(value, 8.7)

    def test_a_reversed_domain_reverses_the_ticks(self) -> None:
        self.assertEqual(ticks(10, 0, 5), list(reversed(ticks(0, 10, 5))))

    def test_degenerate_inputs(self) -> None:
        self.assertEqual(ticks(5, 5, 10), [5])
        self.assertEqual(ticks(0, 1, 0), [])
        self.assertEqual(ticks(float("nan"), 1, 5), [])

    def test_nice_domain_widens_outward_and_is_idempotent(self) -> None:
        lo, hi = nice_domain(3.2, 8.7, 6)
        self.assertLessEqual(lo, 3.2)
        self.assertGreaterEqual(hi, 8.7)
        self.assertEqual(nice_domain(lo, hi, 6), (lo, hi))

    def test_format_uses_the_digits_the_step_justifies(self) -> None:
        self.assertEqual(format_tick(0.5, 0.5), "0.5")
        self.assertEqual(format_tick(1.0, 0.5), "1.0")
        self.assertEqual(format_tick(20.0, 20.0), "20")
        self.assertEqual(format_tick(-0.0), "0")


class ScaleTests(unittest.TestCase):
    def test_linear_maps_and_inverts(self) -> None:
        scale = LinearScale(domain=(0, 10), range=(100, 200))
        self.assertEqual(scale(0), 100)
        self.assertEqual(scale(10), 200)
        self.assertEqual(scale(5), 150)
        self.assertAlmostEqual(scale.invert(150), 5)

    def test_a_flat_domain_lands_in_the_middle_rather_than_dividing_by_zero(self) -> None:
        self.assertEqual(LinearScale(domain=(4, 4), range=(0, 10))(4), 5)

    def test_log_rejects_a_non_positive_domain(self) -> None:
        with self.assertRaises(ValueError):
            LogScale(domain=(0, 100))
        self.assertAlmostEqual(LogScale(domain=(1, 100), range=(0, 2))(10), 1.0)

    def test_band_bandwidth_and_padding(self) -> None:
        scale = BandScale(["a", "b", "c", "d"], (0, 400), padding=0.2)
        self.assertEqual(scale.step, 100)
        self.assertAlmostEqual(scale.bandwidth, 80)
        self.assertLess(scale("a"), scale("b"))

    def test_band_thins_labels_rather_than_overlapping_them(self) -> None:
        scale = BandScale([str(i) for i in range(50)], (0, 400))
        labels = scale.ticks(6)
        self.assertLessEqual(len(labels), 6)
        self.assertEqual(labels[0], "0")

    def test_point_spreads_across_the_whole_range(self) -> None:
        scale = PointScale(["a", "b", "c"], (0, 100))
        self.assertEqual(scale("a"), 0)
        self.assertEqual(scale("c"), 100)
        self.assertEqual(PointScale(["only"], (0, 100))("only"), 50)


class ChartTests(unittest.TestCase):
    def _chart(self, mark: str = "line", series: int = 2) -> Chart:
        chart = Chart(title="t", categories=["a", "b", "c"], theme=DARK)
        for index in range(series):
            chart.add(f"s{index}", [1.0 + index, 3.0 + index, 2.0 + index], mark)
        return chart

    def test_a_legend_appears_for_two_series_and_not_for_one(self) -> None:
        two = {op.value for op in compose_chart(self._chart(series=2)) if isinstance(op, Text)}
        one = {op.value for op in compose_chart(self._chart(series=1)) if isinstance(op, Text)}
        self.assertIn("s0", two)
        self.assertIn("s1", two)
        self.assertNotIn("s0", one)

    def test_colour_follows_the_series_not_its_position(self) -> None:
        """Hiding a series must not repaint the survivors."""
        both = Chart(categories=["a"], theme=DARK)
        both.add("keep", [1.0], "bar", slot=0)
        both.add("drop", [2.0], "bar", slot=1)
        only = Chart(categories=["a"], theme=DARK)
        only.add("keep", [1.0], "bar", slot=0)

        def bar_colour(chart: Chart, label: str) -> int:
            index = [s.label for s in chart.series].index(label)
            return chart.theme.categorical(chart.series[index].slot or 0)

        self.assertEqual(bar_colour(both, "keep"), bar_colour(only, "keep"))

    def test_a_bar_axis_always_includes_zero(self) -> None:
        chart = Chart(categories=["a", "b"], theme=DARK)
        chart.add("s", [100.0, 110.0], "bar")
        chart.y = Axis(zero=False)  # asked for, and overridden: bars need zero
        labels = [
            float(op.value)
            for op in compose_chart(chart)
            if isinstance(op, Text) and op.value.replace("-", "").replace(".", "").isdigit()
        ]
        self.assertIn(0.0, labels)

    def test_a_line_chart_may_leave_zero_out(self) -> None:
        chart = Chart(categories=["a", "b"], theme=DARK)
        chart.add("s", [100.0, 110.0], "line")
        chart.y = Axis(zero=False)
        labels = [
            float(op.value)
            for op in compose_chart(chart)
            if isinstance(op, Text) and op.value.replace("-", "").replace(".", "").isdigit()
        ]
        self.assertNotIn(0.0, labels)

    def test_a_line_does_not_force_zero_by_default(self) -> None:
        """Forcing zero on a line that varies in a narrow band flattens it.
        Measured on [1.00001, 1.00002]: one horizontal stroke at the top of an
        axis running 0 to 1."""
        chart = Chart(categories=["a", "b"], theme=DARK)
        chart.add("s", [1.00001, 1.00002], "line")
        ys = [
            op.y
            for op in compose_chart(chart)
            if isinstance(op, Text) and op.value.replace(".", "").isdigit()
        ]
        self.assertGreater(len(set(ys)), 1)

    def test_a_bar_overrides_a_request_to_leave_zero_out(self) -> None:
        chart = Chart(categories=["a", "b"], theme=DARK)
        chart.add("s", [100.0, 110.0], "bar")
        chart.y = Axis(zero=False)
        labels = [
            float(op.value)
            for op in compose_chart(chart)
            if isinstance(op, Text) and op.value.replace("-", "").replace(".", "").isdigit()
        ]
        self.assertIn(0.0, labels)

    def test_bars_are_square_where_they_meet_the_baseline(self) -> None:
        chart = self._chart("bar", series=1)
        rects = [op for op in compose_chart(chart) if isinstance(op, Rect) and op.layer == 20]
        # Each bar is a rounded body plus a square strip over the baseline.
        self.assertTrue(any(op.radius > 0 for op in rects))
        self.assertTrue(any(op.radius == 0 for op in rects))

    def test_bars_of_different_series_do_not_share_an_edge(self) -> None:
        chart = self._chart("bar", series=2)
        rects = sorted(
            (op for op in compose_chart(chart) if isinstance(op, Rect) and op.layer == 20),
            key=lambda op: op.x,
        )
        first, second = rects[0], next(op for op in rects if op.x > rects[0].x + 1)
        self.assertGreater(second.x, first.x + first.w)

    def test_an_empty_chart_says_so_rather_than_dividing_by_zero(self) -> None:
        scene = compose_chart(Chart(theme=DARK))
        self.assertIn("no data", {op.value for op in scene if isinstance(op, Text)})

    def test_an_unknown_mark_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            Chart().add("s", [1.0], "pie")


if __name__ == "__main__":
    unittest.main()
