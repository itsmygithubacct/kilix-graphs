"""Charts: axes and marks, emitting the same scene the graph drawings do.

The decomposition is Vega-Lite's — mark, encoding, scale, axis as separable
things — without its generality. A `Chart` holds series; `compose()` turns
them into scene operations; nothing here knows what a renderer is.

The chrome rules are not preferences:

- **One y-axis.** A second scale on the opposite side is the most common way a
  chart lies, because the crossing point of the two series is an artefact of
  where the axes were put. Two measures of different scale are two charts.
- **Grid and axis recede.** They are reference, not data; a gridline as dark
  as a series competes with it.
- **A legend whenever there are two or more series**, so identity is never
  carried by colour alone. One series needs none: the title names it.
- **Colour follows the series, not its rank.** A `Series` keeps the slot it
  was given, so hiding one does not repaint the others.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Sequence

from ..scene import Anchor, Ellipse, Polyline, Rect, Scene, Style, Text
from ..theme import DARK, Theme
from .scale import BandScale, LinearScale, PointScale, format_tick, nice_domain

__all__ = ["Axis", "Chart", "MARKS", "Series", "compose_chart"]

MARKS = ("line", "bar", "scatter", "area")

#: Mark geometry, from the shared spec: thin marks, markers big enough to hit.
LINE_WIDTH = 2.0
MARKER_RADIUS = 4.0
BAR_RADIUS = 4.0
#: Gap between adjacent filled marks, so two bars never share an edge.
BAR_GAP = 2.0


@dataclass
class Series:
    label: str
    values: Sequence[float]
    mark: str = "line"
    #: The categorical slot this series owns. None takes the next one at
    #: compose time; setting it explicitly is what pins a colour to an entity
    #: across charts.
    slot: int | None = None

    def __post_init__(self) -> None:
        if self.mark not in MARKS:
            raise ValueError(f"unknown mark {self.mark!r}; known: {', '.join(MARKS)}")


@dataclass
class Axis:
    label: str = ""
    #: None derives the domain from the data.
    domain: tuple[float, float] | None = None
    ticks: int = 6
    grid: bool = True
    #: Start a value axis at zero. None decides from the marks: a bar or an
    #: area encodes its value as a *length* from the baseline, so leaving zero
    #: out misstates every comparison on it and the choice is not offered;
    #: a line or a dot encodes position, and forcing zero on a series that
    #: varies in a narrow band flattens it into a straight line -- measured on
    #: [1.00001, 1.00002], which came out as one horizontal stroke at the top
    #: of an axis running 0 to 1. True and False override the inference.
    zero: bool | None = None


@dataclass
class Chart:
    title: str = ""
    categories: Sequence[str] = field(default_factory=list)
    series: list[Series] = field(default_factory=list)
    x: Axis = field(default_factory=Axis)
    y: Axis = field(default_factory=Axis)
    width: float = 720.0
    height: float = 420.0
    theme: Theme = DARK

    def add(self, label: str, values: Sequence[float], mark: str = "line", slot: int | None = None) -> Series:
        entry = Series(label=label, values=values, mark=mark, slot=slot)
        self.series.append(entry)
        return entry


# Room for the axis furniture. Measured in scene units, which are also the
# font's units, so these are "three characters" and "one line" rather than
# numbers picked to look right at one size.
_PAD_LEFT = 68.0
_PAD_RIGHT = 24.0
_PAD_TOP = 56.0
_PAD_BOTTOM = 56.0
_LEGEND_H = 26.0


def compose_chart(chart: Chart) -> Scene:
    """Build the scene for a chart."""
    theme = chart.theme
    scene = Scene(chart.width, chart.height, background=theme.surface)

    if not chart.series:
        scene.add(
            Text(
                x=chart.width / 2,
                y=chart.height / 2,
                value="no data",
                style=Style(fill=theme.ink_muted),
            )
        )
        return scene

    legend = len(chart.series) >= 2
    plot_top = _PAD_TOP
    plot_bottom = chart.height - _PAD_BOTTOM - (_LEGEND_H if legend else 0.0)
    plot_left = _PAD_LEFT
    plot_right = chart.width - _PAD_RIGHT

    categories = list(chart.categories) or [
        str(i) for i in range(max(len(s.values) for s in chart.series))
    ]

    has_bar = any(s.mark == "bar" for s in chart.series)
    x_scale: BandScale | PointScale = (
        BandScale(categories, (plot_left, plot_right), padding=0.24)
        if has_bar
        else PointScale(categories, (plot_left, plot_right))
    )

    lo, hi = _value_domain(chart)
    y_scale = LinearScale(domain=(lo, hi), range=(plot_bottom, plot_top)).nice(chart.y.ticks)

    _axes(scene, chart, x_scale, y_scale, plot_left, plot_right, plot_top, plot_bottom, categories)

    bars = [s for s in chart.series if s.mark == "bar"]
    for index, series in enumerate(chart.series):
        colour = theme.categorical(series.slot if series.slot is not None else index)
        if series.mark == "bar":
            _bars(scene, series, bars.index(series), len(bars), categories, x_scale, y_scale, colour, plot_bottom)
        elif series.mark == "area":
            _area(scene, series, categories, x_scale, y_scale, colour, plot_bottom)
            _line(scene, series, categories, x_scale, y_scale, colour)
        elif series.mark == "scatter":
            _scatter(scene, series, categories, x_scale, y_scale, colour)
        else:
            _line(scene, series, categories, x_scale, y_scale, colour)

    if chart.title:
        scene.add(
            Text(
                x=plot_left,
                y=18.0,
                value=chart.title,
                anchor=Anchor.START,
                layer=40,
                style=Style(fill=theme.ink),
            )
        )
    if legend:
        _legend(scene, chart, plot_left, chart.height - _LEGEND_H / 2)
    return scene


def _value_domain(chart: Chart) -> tuple[float, float]:
    values = [v for series in chart.series for v in series.values]
    if not values:
        return (0.0, 1.0)
    lo, hi = min(values), max(values)
    axis = chart.y
    if axis.domain is not None:
        return axis.domain
    # A bar's length is its value, so a bar chart that does not start at zero
    # misstates every comparison on it. That is not a preference.
    # A bar or an area encodes its value as a length from the baseline, so an
    # axis that leaves zero out misstates every comparison on it. That is not
    # offered as a choice: `zero=False` beside a bar is overridden.
    encodes_length = any(s.mark in ("bar", "area") for s in chart.series)
    if encodes_length or axis.zero:
        lo = min(lo, 0.0)
        hi = max(hi, 0.0)
    if lo == hi:
        return (lo - 1.0, hi + 1.0)
    return nice_domain(lo, hi, axis.ticks)


def _position(
    series: Series,
    categories: list[str],
    x_scale: BandScale | PointScale,
    y_scale: LinearScale,
) -> list[tuple[float, float]]:
    centre = x_scale.centre if isinstance(x_scale, BandScale) else x_scale
    return [
        (centre(categories[i]), y_scale(value))
        for i, value in enumerate(series.values)
        if i < len(categories)
    ]


def _axes(
    scene: Scene,
    chart: Chart,
    x_scale: BandScale | PointScale,
    y_scale: LinearScale,
    left: float,
    right: float,
    top: float,
    bottom: float,
    categories: list[str],
) -> None:
    theme = chart.theme
    grid_style = Style(stroke=theme.grid, width=1.0, role="grid")
    axis_style = Style(stroke=theme.axis, width=1.0, role="axis")
    label_style = Style(fill=theme.ink_muted)

    step = y_scale.tick_step(chart.y.ticks)
    for value in y_scale.ticks(chart.y.ticks):
        y = y_scale(value)
        if chart.y.grid:
            scene.add(Polyline(points=((left, y), (right, y)), style=grid_style, layer=1))
        scene.add(
            Text(
                x=left - 10,
                y=y,
                value=format_tick(value, step),
                anchor=Anchor.END,
                layer=2,
                style=label_style,
            )
        )

    scene.add(Polyline(points=((left, top), (left, bottom)), style=axis_style, layer=2))
    scene.add(Polyline(points=((left, bottom), (right, bottom)), style=axis_style, layer=2))

    centre = x_scale.centre if isinstance(x_scale, BandScale) else x_scale
    for label in x_scale.ticks(_x_tick_budget(right - left, categories)):
        scene.add(
            Text(
                x=centre(label),
                y=bottom + 18,
                value=label,
                anchor=Anchor.MIDDLE,
                layer=2,
                style=label_style,
            )
        )

    if chart.y.label:
        # Below the title's row, not beside it: at the same height the two
        # sat a character apart and read as one run of text.
        scene.add(
            Text(x=left - 10, y=top - 18, value=chart.y.label, anchor=Anchor.END,
                 layer=2, style=label_style)
        )
    if chart.x.label:
        scene.add(
            Text(x=(left + right) / 2, y=bottom + 40, value=chart.x.label,
                 anchor=Anchor.MIDDLE, layer=2, style=label_style)
        )


def _x_tick_budget(width: float, categories: list[str]) -> int:
    """How many category labels fit without touching.

    Overlapping labels are worse than fewer labels, and the widest label is
    what decides — sizing off the average leaves the longest one colliding.
    """
    from ..model import FONT_ADVANCE

    widest = max((len(c) for c in categories), default=1)
    per_label = (widest + 2) * FONT_ADVANCE
    return max(2, int(width // per_label))


def _line(
    scene: Scene,
    series: Series,
    categories: list[str],
    x_scale: BandScale | PointScale,
    y_scale: LinearScale,
    colour: int,
) -> None:
    points = _position(series, categories, x_scale, y_scale)
    if len(points) < 2:
        _scatter(scene, series, categories, x_scale, y_scale, colour)
        return
    scene.add(
        Polyline(
            points=tuple(points),
            layer=20,
            style=Style(stroke=colour, width=LINE_WIDTH, role="data"),
        )
    )


def _area(
    scene: Scene,
    series: Series,
    categories: list[str],
    x_scale: BandScale | PointScale,
    y_scale: LinearScale,
    colour: int,
    baseline: float,
) -> None:
    from ..scene import Polygon

    points = _position(series, categories, x_scale, y_scale)
    if len(points) < 2:
        return
    outline = [
        (points[0][0], baseline),
        *points,
        (points[-1][0], baseline),
    ]
    scene.add(
        Polygon(
            points=tuple(outline),
            layer=15,
            style=Style(fill=colour, alpha=0.22, role="data"),
        )
    )


def _scatter(
    scene: Scene,
    series: Series,
    categories: list[str],
    x_scale: BandScale | PointScale,
    y_scale: LinearScale,
    colour: int,
) -> None:
    for x, y in _position(series, categories, x_scale, y_scale):
        scene.add(
            Ellipse(
                cx=x,
                cy=y,
                rx=MARKER_RADIUS,
                ry=MARKER_RADIUS,
                layer=22,
                style=Style(fill=colour, stroke=None, role="data"),
            )
        )


def _bars(
    scene: Scene,
    series: Series,
    index: int,
    total: int,
    categories: list[str],
    x_scale: BandScale | PointScale,
    y_scale: LinearScale,
    colour: int,
    baseline: float,
) -> None:
    if not isinstance(x_scale, BandScale):
        return
    slot = (x_scale.bandwidth - BAR_GAP * (total - 1)) / max(1, total)
    zero = y_scale(0.0)
    for position, value in enumerate(series.values):
        if position >= len(categories):
            break
        x = x_scale(categories[position]) + index * (slot + BAR_GAP)
        y = y_scale(value)
        top = min(y, zero)
        height = abs(y - zero)
        if height <= 0:
            continue
        style = Style(fill=colour, stroke=None, role="data")
        # A bar is rounded at the *data* end and square where it meets the
        # baseline: rounding the baseline end lifts the bar off its own axis
        # and makes short bars look like they start above zero. The scene's
        # Rect carries one radius for all four corners, so the square end is
        # a second opaque rect over the baseline strip rather than a
        # per-corner radius the rasteriser has no primitive for.
        radius = BAR_RADIUS if height > BAR_RADIUS * 2 else 0.0
        scene.add(Rect(x=x, y=top, w=slot, h=height, radius=radius,
                       layer=20, style=style))
        if radius:
            square_top = top if value < 0 else top + height - radius
            scene.add(
                Rect(x=x, y=square_top, w=slot, h=radius, radius=0.0,
                     layer=20, style=style)
            )


def _legend(scene: Scene, chart: Chart, left: float, y: float) -> None:
    from ..model import FONT_ADVANCE

    x = left
    for index, series in enumerate(chart.series):
        colour = chart.theme.categorical(
            series.slot if series.slot is not None else index
        )
        scene.add(
            Rect(
                x=x,
                y=y - 5,
                w=10,
                h=10,
                radius=2,
                layer=40,
                # A swatch *is* the series colour, so it takes the data path:
                # in a cell grid it becomes one filled block rather than a
                # three-row outlined box beside a one-row label.
                style=Style(fill=colour, stroke=None, role="data"),
            )
        )
        scene.add(
            Text(
                x=x + 16,
                y=y,
                value=series.label,
                anchor=Anchor.START,
                layer=40,
                # Text wears ink, never the series colour: the swatch beside
                # it already carries the identity.
                style=Style(fill=chart.theme.ink_muted),
            )
        )
        x += 16 + (len(series.label) + 3) * FONT_ADVANCE
