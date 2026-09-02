"""Renderers, and the capability probe that chooses between them.

Backend selection is a probe, not a flag. A drawing should come out as pixels
where the terminal can show pixels and as box drawing where it cannot, without
the user having to know which terminal they are in — and `--renderer` is there
for when they do.
"""

from __future__ import annotations

import os
import sys

from ..scene import Scene
from .svg import render_svg
from .text import ASCII, CHARSETS, UNICODE, Charset, TextOptions, render_text

__all__ = [
    "ASCII",
    "CHARSETS",
    "Charset",
    "TextOptions",
    "UNICODE",
    "choose",
    "render_svg",
    "render_text",
    "supports_graphics",
    "supports_unicode",
]


def supports_graphics() -> bool:
    """Whether this terminal can display an image in place.

    Kilix and Kitty both set ``TERM=xterm-kitty`` and both implement the
    graphics protocol. ``KITTY_WINDOW_ID`` covers a Kitty that was started
    under a different TERM. Everything else is assumed not to, because a
    wrong yes prints escape sequences at the user and a wrong no prints a
    perfectly readable diagram.
    """
    if not sys.stdout.isatty():
        return False
    if os.environ.get("KILIX_GRAPHICS") == "0":
        return False
    if os.environ.get("KITTY_WINDOW_ID"):
        return True
    return os.environ.get("TERM", "") in ("xterm-kitty", "kitty")


def supports_unicode() -> bool:
    encoding = (getattr(sys.stdout, "encoding", None) or "").lower()
    return "utf" in encoding


def choose(preferred: str | None = None) -> str:
    """Name the renderer to use: ``raster``, ``text`` or ``svg``."""
    if preferred and preferred != "auto":
        return preferred
    if supports_graphics():
        from . import raster  # noqa: PLC0415 - only when it might be used

        if raster.available():
            return "raster"
    return "text"


def default_text_options(colour: bool | None = None) -> TextOptions:
    return TextOptions(
        charset=UNICODE if supports_unicode() else ASCII,
        colour=sys.stdout.isatty() if colour is None else colour,
    )


def render(scene: Scene, backend: str = "auto", **kwargs: object) -> object:
    """Render with the named backend, or the one the terminal supports."""
    chosen = choose(backend)
    if chosen == "text":
        options = kwargs.get("text_options") or default_text_options()
        return render_text(scene, options)  # type: ignore[arg-type]
    if chosen == "svg":
        return render_svg(scene)
    if chosen == "raster":
        from . import raster  # noqa: PLC0415

        return raster.render_raster(scene, kwargs.get("raster_options"))  # type: ignore[arg-type]
    raise ValueError(f"unknown renderer {chosen!r}")
