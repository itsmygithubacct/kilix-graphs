"""A graph open in a viewer: the file, the settings, and the last drawing.

The CLI runs the pipeline once and exits. An interactive viewer runs it again
on every keystroke, and the difference is not cosmetic: laying out an
800-node graph takes most of a second, so re-running the whole pipeline
because someone changed the *theme* makes the interface feel broken.

`Session` is what both the TUI and the GUI drive. It knows which settings
affect which stage and recomputes only from there down:

    source  ──parse──▶  graph  ──layout──▶  positions  ──compose──▶  scene
              ^                   ^                        ^
              |                   |                        |
        file changed        engine, direction,      theme, curved,
                            ranker, separation      font scale

Neither viewer imports the other, and neither of them repeats the pipeline.
A parse error is held rather than raised, because a viewer must survive the
half-typed file its own live reload just read.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field, replace
from pathlib import Path

from . import layout, parse
from .compose import ComposeOptions, compose
from .model import Graph
from .scene import Scene
from .theme import Theme, resolve

__all__ = ["Session", "Settings"]

#: Cycled by the interactive viewers, in the order a person would try them.
ENGINES = ("layered", "force", "tree", "circular", "grid")
DIRECTIONS = ("TB", "LR", "BT", "RL")
THEMES = ("dark", "light")


@dataclass(frozen=True)
class Settings:
    """Everything a viewer can change, and nothing it cannot."""

    engine: str = "layered"
    direction: str = "TB"
    theme: str = "dark"
    ranker: str = "network-simplex"
    nodesep: float | None = None
    ranksep: float | None = None
    curved: bool = True
    font_scale: int = 1
    #: Pixels per scene unit, for the raster backend.
    scale: float = 1.0

    #: Changing any of these means laying out again.
    LAYOUT_FIELDS = ("engine", "direction", "ranker", "nodesep", "ranksep")
    #: Changing any of these means only composing again.
    SCENE_FIELDS = ("theme", "curved", "font_scale")

    def layout_options(self) -> dict[str, object]:
        options: dict[str, object] = {}
        if self.engine in ("layered", "tree"):
            options["direction"] = self.direction
            if self.nodesep is not None:
                options["nodesep"] = self.nodesep
            if self.ranksep is not None:
                options["ranksep"] = self.ranksep
        if self.engine == "layered":
            options["ranker"] = self.ranker
        return options

    def cycle(self, field_name: str, step: int = 1) -> "Settings":
        """Advance one cyclic setting. Used by every viewer's key handling."""
        choices = {
            "engine": ENGINES,
            "direction": DIRECTIONS,
            "theme": THEMES,
        }[field_name]
        current = getattr(self, field_name)
        index = (choices.index(current) + step) % len(choices)
        return replace(self, **{field_name: choices[index]})


@dataclass
class Stats:
    nodes: int = 0
    edges: int = 0
    clusters: int = 0
    #: Seconds the last layout took. Viewers show it; it is the number that
    #: explains why an engine felt slow.
    layout_seconds: float = 0.0
    width: float = 0.0
    height: float = 0.0


class Session:
    """One open graph, laid out and composed on demand."""

    def __init__(
        self,
        path: str | Path | None = None,
        source: str | None = None,
        settings: Settings | None = None,
        fmt: str | None = None,
    ) -> None:
        if path is None and source is None:
            raise ValueError("a session needs a path or a source")
        self.path = Path(path) if path is not None else None
        self.format = fmt
        self.settings = settings or Settings()
        self.error: str | None = None
        self.stats = Stats()

        self._source: str = source or ""
        self._stamp: tuple[int, int, int] | None = None
        self._graph: Graph | None = None
        self._scene: Scene | None = None
        self._laid_out_with: Settings | None = None
        self._composed_with: Settings | None = None
        if self.path is not None:
            self._read()

    # ----------------------------------------------------------- the source

    def _read(self) -> bool:
        """Re-read the file. Returns True when the *text* changed."""
        assert self.path is not None
        try:
            text = self.path.read_text(encoding="utf-8")
            stat = self.path.stat()
        except OSError as error:
            self.error = f"{self.path}: {error.strerror or error}"
            return False
        self._stamp = (stat.st_mtime_ns, stat.st_size, stat.st_ino)
        if text == self._source and self._graph is not None:
            return False
        self._source = text
        self._invalidate()
        return True

    def _invalidate(self) -> None:
        """Drop everything derived from the source.

        Clearing the graph alone is not enough, and the way it fails is quiet:
        `scene()` returns its cache when the *settings* are unchanged, and a
        reload does not change the settings. A viewer that calls `scene()`
        directly -- which the GUI does -- then redraws the previous file after
        a successful reload, and the reload reports success.
        """
        self._graph = None
        self._scene = None
        self._laid_out_with = None
        self._composed_with = None

    #: How long after a write to keep re-reading regardless of the stamp.
    #: Two writes in quick succession can land on the same st_mtime_ns --
    #: measured on this filesystem as a delta of exactly 0 for back-to-back
    #: writes -- so a stamp comparison alone silently misses the second save.
    #: A viewer that ignores your save is worse than one that reads a small
    #: file a few times for nothing.
    SETTLE_SECONDS = 2.0

    def reload(self, force: bool = False) -> bool:
        """Re-read if the file changed on disk. Returns True when it did.

        Viewers poll this several times a second. The stamp comparison is the
        optimisation that keeps an idle viewer idle; the text comparison in
        `_read` is what decides the answer, so a redundant read costs a file
        read and returns False.
        """
        if self.path is None:
            return False
        try:
            stat = self.path.stat()
        except OSError:
            return False
        stamp = (stat.st_mtime_ns, stat.st_size, stat.st_ino)
        recent = (time.time_ns() - stat.st_mtime_ns) < self.SETTLE_SECONDS * 1e9
        if not force and stamp == self._stamp and not recent:
            return False
        return self._read()

    @property
    def source(self) -> str:
        return self._source

    # ------------------------------------------------------------- pipeline

    def _parse(self) -> Graph | None:
        try:
            graph = parse.loads(
                self._source,
                self.format,
                filename=str(self.path) if self.path else None,
            )
        except ValueError as error:
            self.error = str(error)
            return None
        if not graph.nodes:
            self.error = "the input has no nodes"
            return None
        return graph

    def graph(self) -> Graph | None:
        """The laid-out graph, recomputing only when it has to."""
        if self._graph is not None and self._laid_out_with == self.settings:
            return self._graph
        if self._graph is None or not self._same(
            self._laid_out_with, Settings.LAYOUT_FIELDS
        ):
            parsed = self._parse()
            if parsed is None:
                return None
            self.error = None
            started = time.perf_counter()
            try:
                layout.run(parsed, self.settings.engine, **self.settings.layout_options())
            except ValueError as error:
                self.error = str(error)
                return None
            self.stats.layout_seconds = time.perf_counter() - started
            self._graph = parsed
            self._scene = None
        self._laid_out_with = self.settings
        graph = self._graph
        assert graph is not None
        self.stats.nodes = len(graph.nodes)
        self.stats.edges = len(graph.edges)
        self.stats.clusters = sum(1 for c in graph.clusters.values() if c.w > 0)
        return graph

    def _same(self, other: Settings | None, fields: tuple[str, ...]) -> bool:
        if other is None:
            return False
        return all(getattr(other, f) == getattr(self.settings, f) for f in fields)

    def scene(self) -> Scene | None:
        """The composed scene, recomputing only when it has to."""
        if (
            self._scene is not None
            and self._composed_with is not None
            and self._same(self._composed_with, Settings.SCENE_FIELDS)
            and self._same(self._composed_with, Settings.LAYOUT_FIELDS)
        ):
            return self._scene
        graph = self.graph()
        if graph is None:
            return None
        self._scene = compose(
            graph,
            ComposeOptions(
                theme=self.theme,
                curved=self.settings.curved,
                font_scale=self.settings.font_scale,
            ),
        )
        self._composed_with = self.settings
        self.stats.width = self._scene.width
        self.stats.height = self._scene.height
        return self._scene

    @property
    def theme(self) -> Theme:
        return resolve(self.settings.theme)

    # -------------------------------------------------------------- changing

    def update(self, **changes: object) -> None:
        """Change settings. Only the affected stages are invalidated."""
        unknown = set(changes) - set(Settings.__dataclass_fields__)
        if unknown:
            raise ValueError(f"unknown setting(s): {', '.join(sorted(unknown))}")
        self.settings = replace(self.settings, **changes)  # type: ignore[arg-type]

    def cycle(self, field_name: str, step: int = 1) -> None:
        self.settings = self.settings.cycle(field_name, step)

    # ------------------------------------------------------------- rendering

    def text(self, charset: object | None = None, colour: bool = False) -> str:
        """The drawing as characters. Never raises: a viewer shows the error."""
        from .render import UNICODE, TextOptions, render_text  # noqa: PLC0415

        scene = self.scene()
        if scene is None:
            return f"[{self.error or 'nothing to draw'}]"
        return render_text(
            scene,
            TextOptions(
                charset=charset or UNICODE,  # type: ignore[arg-type]
                colour=colour,
                max_columns=4000,
                max_rows=4000,
            ),
        )

    def raster(self):  # -> soft_raster.Canvas
        """The drawing as pixels. Raises RasterUnavailable without soft-raster."""
        from .render import raster as raster_backend  # noqa: PLC0415

        scene = self.scene()
        if scene is None:
            raise ValueError(self.error or "nothing to draw")
        return raster_backend.render_raster(
            scene, raster_backend.RasterOptions(scale=self.settings.scale)
        )

    def svg(self) -> str:
        from .render import render_svg  # noqa: PLC0415

        scene = self.scene()
        if scene is None:
            raise ValueError(self.error or "nothing to draw")
        return render_svg(scene)

    def summary(self) -> str:
        """One line naming the settings, for a status bar."""
        parts = [
            self.settings.engine,
            self.settings.direction
            if self.settings.engine in ("layered", "tree")
            else "-",
            self.settings.theme,
        ]
        if self.stats.layout_seconds:
            parts.append(f"{self.stats.layout_seconds * 1000:.0f}ms")
        return "  ".join(parts)
