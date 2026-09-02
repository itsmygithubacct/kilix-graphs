"""Colour, in one place, for both surfaces.

Two themes, not one theme and an inversion. A drawing in a terminal is usually
on a dark ground and a drawing exported to a file is usually on a light one,
and the same eight hues stepped for each surface is what keeps both readable.

The categorical order is fixed and is never cycled. Assigning a colour by a
series' *rank* rather than its identity means a filter that removes one series
repaints the survivors, which is the single most common way a chart lies. The
ordering itself is the colourblind-safety mechanism: the eight slots were
checked pairwise for lightness band, chroma floor, deuteranope/protanope/
tritanope separation, normal-vision separation and contrast against each
surface, and both sets pass. Re-order them and that stops being true.

Past three simultaneous series in a scatter-like form, where every pair is
adjacent on screen rather than only its neighbours, the eight cannot all stay
apart. `categorical()` will hand out all eight because a graph is not a
scatter; a chart that needs more than three unordered series should fold the
rest into one "other" instead.
"""

from __future__ import annotations

from dataclasses import dataclass, field

__all__ = ["DARK", "LIGHT", "Theme", "themes"]


@dataclass(frozen=True)
class Theme:
    name: str
    surface: int
    ink: int
    ink_muted: int
    grid: int
    axis: int
    #: Node body, node outline, edge, cluster wash, cluster outline.
    node_fill: int
    node_stroke: int
    edge: int
    cluster_fill: int
    cluster_stroke: int
    #: The fixed categorical order. Never cycled, never re-ordered.
    series: tuple[int, ...] = ()
    #: Painted behind a label where it crosses an edge.
    halo: int = 0x000000

    def categorical(self, index: int) -> int:
        """The colour for slot ``index``.

        Beyond the eighth slot the colours repeat, and that repeat is a signal
        that the drawing has more categories than colour can carry: the caller
        should be folding, faceting or labelling directly by then.
        """
        return self.series[index % len(self.series)]


LIGHT = Theme(
    name="light",
    surface=0xFCFCFB,
    ink=0x0B0B0B,
    ink_muted=0x898781,
    grid=0xE1E0D9,
    axis=0xC3C2B7,
    node_fill=0xFFFFFF,
    node_stroke=0x52514E,
    edge=0x898781,
    cluster_fill=0xF0EFEC,
    cluster_stroke=0xC3C2B7,
    halo=0xFCFCFB,
    series=(
        0x2A78D6,  # blue
        0xEB6834,  # orange
        0x1BAF7A,  # aqua
        0xEDA100,  # yellow
        0xE87BA4,  # magenta
        0x008300,  # green
        0x4A3AA7,  # violet
        0xE34948,  # red
    ),
)

DARK = Theme(
    name="dark",
    surface=0x1A1A19,
    ink=0xFFFFFF,
    ink_muted=0x898781,
    grid=0x2C2C2A,
    axis=0x383835,
    node_fill=0x2C2C2A,
    node_stroke=0x898781,
    edge=0x898781,
    cluster_fill=0x232322,
    cluster_stroke=0x383835,
    halo=0x1A1A19,
    series=(
        0x3987E5,  # blue
        0xD95926,  # orange
        0x199E70,  # aqua
        0xC98500,  # yellow
        0xD55181,  # magenta
        0x008300,  # green
        0x9085E9,  # violet
        0xE66767,  # red
    ),
)

themes: dict[str, Theme] = {"dark": DARK, "light": LIGHT}


def resolve(name: str | Theme | None) -> Theme:
    if isinstance(name, Theme):
        return name
    if name is None:
        return DARK
    try:
        return themes[name]
    except KeyError:
        known = ", ".join(sorted(themes))
        raise ValueError(f"unknown theme {name!r}; known: {known}") from None


def parse_colour(value: str | int) -> int:
    """Accept ``0xRRGGBB``, ``#rrggbb``, ``#rgb`` or a bare hex string."""
    if isinstance(value, int):
        return value & 0xFFFFFF
    text = value.strip().lstrip("#")
    if len(text) == 3:
        text = "".join(char * 2 for char in text)
    if len(text) != 6:
        raise ValueError(f"{value!r} is not a colour")
    try:
        return int(text, 16)
    except ValueError:
        raise ValueError(f"{value!r} is not a colour") from None
