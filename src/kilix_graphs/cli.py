"""The `kilix-graphs` command.

Eight verbs, each doing one thing:

    draw     a graph, to the terminal or a file
    chart    a chart from CSV or JSON, likewise
    tui      the interactive cell viewer
    gui      the graphical viewer: pixels and a mouse in this pane,
             or a desktop window where there is no graphics protocol
    convert  between the input formats
    layout   positions only, as JSON, with no renderer in the way
    syntax   what to write in a .kg file
    doctor   what this installation can and cannot do

`draw` with no `--out` writes to the terminal and picks its renderer from what
the terminal can do: pixels through the Kitty graphics protocol where that is
available, box drawing where it is not. `--renderer` overrides the probe.
"""

from __future__ import annotations

import argparse
import csv
import io
import json
import os
import sys
from pathlib import Path

from .output import OutputExists, write_new

from . import __version__, compose, layout, parse, render
from .chart import Axis, Chart, compose_chart
from .compose import ComposeOptions
from .model import Graph
from .parse import jgf
from .render import TextOptions, render_svg, render_text
from .scene import Scene
from .theme import resolve

__all__ = ["main"]

_RENDERERS = ("auto", "raster", "text", "svg")
_IMAGE_SUFFIXES = {".ppm", ".png"}


def _bounded(name: str, low: float, high: float, kind=float):
    """An argparse type that refuses nonsense instead of clamping it.

    `--scale 0` used to exit 0 having written a one-pixel image: the request
    was impossible, the answer was garbage, and the status said success. A
    number outside its usable range is a mistake worth a message.
    """

    def parse(text: str):
        try:
            value = kind(text)
        except ValueError:
            raise argparse.ArgumentTypeError(f"{name} must be a number") from None
        if not low <= value <= high:
            raise argparse.ArgumentTypeError(
                f"{name} must be between {low:g} and {high:g}, not {value:g}"
            )
        return value

    return parse


def _read(path: str) -> str:
    if path == "-":
        return sys.stdin.read()
    return Path(path).read_text(encoding="utf-8")


def _protected(args: argparse.Namespace) -> tuple[str, ...]:
    """The inputs an output may never replace, whatever `--force` says."""
    source = getattr(args, "source", None)
    return (source,) if source and source != "-" else ()


def _write_out(args: argparse.Namespace, out: str, payload: bytes | str) -> int:
    try:
        write_new(out, payload, force=bool(getattr(args, "force", False)), protect=_protected(args))
    except OutputExists as error:
        print(f"kilix-graphs: {error}", file=sys.stderr)
        return 2
    return 0


def _emit_scene(scene: Scene, args: argparse.Namespace) -> int:
    out = getattr(args, "out", None)
    if out and Path(out).is_dir():
        print(f"kilix-graphs: {out} is a directory; -o takes a filename", file=sys.stderr)
        return 2
    suffix = Path(out).suffix.lower() if out else ""

    backend = args.renderer
    if backend == "auto" and out:
        backend = "svg" if suffix == ".svg" else "raster" if suffix in _IMAGE_SUFFIXES else "text"
    if backend == "auto":
        backend = render.choose()

    if backend == "svg":
        payload = render_svg(scene)
        if out:
            return _write_out(args, out, payload)
        sys.stdout.write(payload + "\n")
        return 0

    if backend == "text":
        options = TextOptions(
            charset=render.CHARSETS["ascii" if args.ascii else "unicode"],
            colour=args.colour if args.colour is not None else (not out and sys.stdout.isatty()),
        )
        payload = render_text(scene, options)
        if out:
            return _write_out(args, out, payload + "\n")
        sys.stdout.write(payload + "\n")
        return 0

    from .render import raster  # noqa: PLC0415 - only when pixels are wanted

    try:
        canvas = raster.render_raster(scene, raster.RasterOptions(scale=args.scale))
    except raster.RasterUnavailable as error:
        print(f"kilix-graphs: {error}", file=sys.stderr)
        return 3
    wanted = (round(scene.width * args.scale), round(scene.height * args.scale))
    if (canvas.width, canvas.height) != wanted:
        # Silently returning a smaller image than asked for is how a user ends
        # up wondering why their diagram is cropped.
        print(
            f"kilix-graphs: {wanted[0]}x{wanted[1]} exceeds the canvas limit; "
            f"drew {canvas.width}x{canvas.height}. Lower --scale.",
            file=sys.stderr,
        )
    try:
        if out and suffix == ".ppm":
            return _write_out(args, out, raster.ppm_bytes(canvas))
        elif out and suffix == ".png":
            return _write_out(args, out, raster.png_bytes(canvas))
        elif out:
            print(
                f"kilix-graphs: don't know how to write {suffix or out!r}; "
                "use .ppm, .png or .svg",
                file=sys.stderr,
            )
            return 2
        else:
            _present(canvas)
    finally:
        canvas.close()
    return 0


def _present(canvas: object) -> None:
    """Put a canvas on the terminal through the Kitty graphics protocol."""
    try:
        from kitty_frame_presenter import FramePresenter  # noqa: PLC0415
    except ImportError:
        FramePresenter = None  # type: ignore[assignment]

    width = canvas.width  # type: ignore[attr-defined]
    height = canvas.height  # type: ignore[attr-defined]
    rgb = canvas.rgb_bytes()  # type: ignore[attr-defined]

    if FramePresenter is not None:
        columns, rows = _cells(width, height)
        presenter = FramePresenter(sys.stdout, image_id=17, max_fps=0)
        presenter.present(bytes(rgb), width, height, columns, rows)
        presenter.flush()
        sys.stdout.write("\n" * rows)
        sys.stdout.flush()
        return

    # No presenter installed: one direct, chunked graphics escape. Enough for
    # a still image, which is all this command produces; the presenter is what
    # a moving one would need.
    import base64  # noqa: PLC0415
    import zlib  # noqa: PLC0415

    payload = base64.b64encode(zlib.compress(bytes(rgb)))
    first = True
    while payload:
        piece, payload = payload[:4096], payload[4096:]
        control = f"m={1 if payload else 0}"
        if first:
            control = f"a=T,f=24,o=z,s={width},v={height}," + control
            first = False
        sys.stdout.write(f"\x1b_G{control};{piece.decode('ascii')}\x1b\\")
    sys.stdout.write("\n")
    sys.stdout.flush()


def _cells(width: int, height: int) -> tuple[int, int]:
    import shutil  # noqa: PLC0415

    size = shutil.get_terminal_size((80, 24))
    columns = max(1, min(size.columns, width // 8))
    rows = max(1, min(size.lines - 1, height // 16))
    return (columns, rows)


def _load_graph(args: argparse.Namespace) -> Graph:
    source = _read(args.source)
    filename = None if args.source == "-" else args.source
    return parse.loads(source, args.format, filename=filename)


def _layout_options(args: argparse.Namespace) -> dict[str, object]:
    options: dict[str, object] = {}
    if args.engine == "layered":
        options["direction"] = args.direction
        if args.nodesep is not None:
            options["nodesep"] = args.nodesep
        if args.ranksep is not None:
            options["ranksep"] = args.ranksep
        if getattr(args, "ranker", None):
            options["ranker"] = args.ranker
    elif args.engine == "tree":
        options["direction"] = args.direction
        if args.nodesep is not None:
            options["nodesep"] = args.nodesep
        if args.ranksep is not None:
            options["ranksep"] = args.ranksep
    return options


def cmd_draw(args: argparse.Namespace) -> int:
    graph = _load_graph(args)
    if not graph.nodes:
        print("kilix-graphs: the input has no nodes", file=sys.stderr)
        return 1
    layout.run(graph, args.engine, **_layout_options(args))
    # Curves and cells disagree: a Bezier through a cell grid is a staircase.
    # The text backend straightens routes, so do not spend the smoothing.
    curved = args.curved and args.renderer != "text"
    scene = compose(
        graph,
        ComposeOptions(theme=resolve(args.theme), curved=curved, font_scale=args.font_scale),
    )
    return _emit_scene(scene, args)


def cmd_layout(args: argparse.Namespace) -> int:
    graph = _load_graph(args)
    layout.run(graph, args.engine, **_layout_options(args))
    payload = jgf.dumps(graph, positions=True)
    if args.out:
        return _write_out(args, args.out, payload + "\n")
    sys.stdout.write(payload + "\n")
    return 0


def cmd_convert(args: argparse.Namespace) -> int:
    graph = _load_graph(args)
    if args.to == "jgf":
        payload = jgf.dumps(graph)
    elif args.to == "dot":
        payload = _to_dot(graph)
    else:
        payload = _to_kg(graph)
    if args.out:
        return _write_out(args, args.out, payload + "\n")
    sys.stdout.write(payload + "\n")
    return 0


def _quote(name: str) -> str:
    safe = name and (name[0].isalpha() or name[0] == "_")
    if safe and all(c.isalnum() or c == "_" for c in name):
        return name
    return '"' + name.replace('"', '\\"') + '"'


def _to_dot(graph: Graph) -> str:
    arrow = "->" if graph.directed else "--"
    lines = [f"{'digraph' if graph.directed else 'graph'} {_quote(graph.name or 'G')} {{"]
    for node in graph.nodes.values():
        bits = []
        if node.label and node.label != node.id:
            bits.append(f'label={_quote(node.label)}')
        if node.shape:
            bits.append(f"shape={node.shape}")
        suffix = f" [{', '.join(bits)}]" if bits else ""
        lines.append(f"  {_quote(node.id)}{suffix};")
    for edge in graph.edges:
        bits = [f'label={_quote(edge.label)}'] if edge.label else []
        suffix = f" [{', '.join(bits)}]" if bits else ""
        lines.append(f"  {_quote(edge.tail)} {arrow} {_quote(edge.head)}{suffix};")
    lines.append("}")
    return "\n".join(lines)


def _to_kg(graph: Graph) -> str:
    arrow = "->" if graph.directed else "--"
    lines = [("digraph " if graph.directed else "graph ") + (graph.name or "G"), ""]
    for node in graph.nodes.values():
        if node.label and node.label != node.id:
            lines.append(f"{node.id}: {node.label}")
        elif not any(node.id in (e.tail, e.head) for e in graph.edges):
            lines.append(node.id)
    for edge in graph.edges:
        line = f"{edge.tail} {arrow} {edge.head}"
        if edge.label:
            line += f": {edge.label}"
        lines.append(line)
    return "\n".join(lines)


def cmd_chart(args: argparse.Namespace) -> int:
    source = _read(args.source)
    try:
        chart = _chart_from(source, args)
    except ValueError as error:
        print(f"kilix-graphs: {error}", file=sys.stderr)
        return 1
    return _emit_scene(compose_chart(chart), args)


def _chart_from(source: str, args: argparse.Namespace) -> Chart:
    """Read a chart from CSV or JSON.

    CSV: the first column is the category and every other column is a series
    named by its header, which is the shape every tool already exports.
    """
    stripped = source.lstrip()
    chart = Chart(
        title=args.title,
        width=args.width,
        height=args.height,
        theme=resolve(args.theme),
    )
    chart.y = Axis(label=args.ylabel, zero=not args.no_zero)
    chart.x = Axis(label=args.xlabel)

    if stripped.startswith("{"):
        document = json.loads(source)
        chart.categories = [str(c) for c in document.get("categories", [])]
        for entry in document.get("series", []):
            chart.add(
                str(entry.get("label", "")),
                [float(v) for v in entry.get("values", [])],
                str(entry.get("mark", args.mark)),
            )
        if document.get("title") and not args.title:
            chart.title = str(document["title"])
        return chart

    rows = list(csv.reader(io.StringIO(source)))
    rows = [row for row in rows if row and any(cell.strip() for cell in row)]
    if len(rows) < 2:
        raise ValueError("a CSV chart needs a header row and at least one data row")
    header, *data = rows
    chart.categories = [row[0] for row in data]
    for column in range(1, len(header)):
        values: list[float] = []
        for row in data:
            cell = row[column] if column < len(row) else ""
            try:
                values.append(float(cell))
            except ValueError:
                values.append(0.0)
        chart.add(header[column].strip(), values, args.mark)
    if not chart.series:
        raise ValueError("no value columns found; the first column is the category")
    return chart


SYNTAX = """\
The .kg syntax, in full.

  # a comment; blank lines are ignored

  digraph Name          optional header. `graph` for undirected.
                        Without one the graph is directed.

  a -> b                a directed edge
  a -- b                an undirected one
  a -> b -> c           a chain: two edges

  a -> b: ships         a trailing `: text` labels what precedes it --
  disk: Cold storage    the edge on an edge line, the node on a node line

  disk {shape=box}      attributes; may follow a label:
                        `disk: Cold storage {shape=box}`

  group id: Label       a cluster. Indented lines below are its members,
      a                 and groups nest by indentation.
      b

Shapes: round (the default), box, ellipse, circle, diamond, point.
Attributes lifted onto a node: label, shape, color, fillcolor.
On an edge: label, color, style=dashed|dotted, dir=none, weight, minlen.

An identifier is anything without whitespace, `:`, `{` or an arrow. Quote it
if it needs one of those: `"a:b" -> c`.

A worked example:

  # How a frame reaches the screen.
  digraph FramePath

  group capture: Capture
      camera -> rtsp: h264
      rtsp   -> decode

  decode -> present
  present -> shm: "t=s"
  present -> inline: "t=d,o=z"
  shm    -> terminal
  inline -> terminal

  terminal {shape=box}

DOT and JSON Graph Format are read too; the format is detected from the file
name, or from the content when reading standard input.
"""


def cmd_syntax(args: argparse.Namespace) -> int:
    sys.stdout.write(SYNTAX)
    return 0


def _viewer_session(args: argparse.Namespace):
    from .view import Session, Settings  # noqa: PLC0415

    if not args.source:
        # `gui` with no file opens on an example, so the window is never an
        # empty box with a file dialog behind it.
        return Session(source="digraph\n\na -> b -> c\nb -> d\n",
                       settings=Settings(engine=args.engine, direction=args.direction,
                                         theme=args.theme, scale=getattr(args, "scale", 1.0)))
    settings = Settings(
        engine=args.engine,
        direction=args.direction,
        theme=args.theme,
        scale=getattr(args, "scale", 1.0),
    )
    if args.source == "-":
        return Session(source=sys.stdin.read(), settings=settings, fmt=args.format)
    return Session(path=args.source, settings=settings, fmt=args.format)


def cmd_tui(args: argparse.Namespace) -> int:
    from . import tui  # noqa: PLC0415

    if not sys.stdout.isatty():
        print(
            "kilix-graphs: tui needs a terminal. "
            "Use `draw` to write a rendering somewhere.",
            file=sys.stderr,
        )
        return 2
    return tui.run(_viewer_session(args))


def cmd_gui(args: argparse.Namespace) -> int:
    """The graphical viewer, in whichever surface this machine has.

    The pane is the native one: 39 of the 42 entries in the Kilix content
    catalog launch as a terminal pane and exactly one opens an X window, so a
    desktop toolkit is the exception on this stack rather than the default.
    The window is the fallback for a desktop session outside Kilix, and
    `--window` asks for it directly.
    """
    from . import pane  # noqa: PLC0415
    from .render import raster  # noqa: PLC0415

    session = _viewer_session(args)
    if not args.window and pane.available() and raster.available():
        return pane.run(session)

    has_display = os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY")
    if args.window or has_display:
        from . import gui  # noqa: PLC0415

        return gui.run(session)

    # Neither surface. Degrade to the cell viewer rather than refusing: this
    # verb means "the best graphical surface there is", and on a machine
    # without soft-raster the floor is still a working viewer. A catalog
    # action that exits non-zero on a fresh install is not an action.
    from . import tui  # noqa: PLC0415

    if not sys.stdout.isatty():
        print("kilix-graphs: no graphical surface and no terminal.", file=sys.stderr)
        return 2
    print(
        "kilix-graphs: no graphics protocol here; opening the cell viewer.",
        file=sys.stderr,
    )
    return tui.run(session)


def cmd_doctor(args: argparse.Namespace) -> int:
    from .render import raster  # noqa: PLC0415

    print(f"kilix-graphs {__version__}")
    print(f"  python            {sys.version.split()[0]}")
    print(f"  terminal graphics {'yes' if render.supports_graphics() else 'no'}")
    print(f"  unicode output    {'yes' if render.supports_unicode() else 'no'}")

    try:
        import soft_raster  # noqa: PLC0415

        library = soft_raster.default_library()
        primitives = getattr(library, "supports_graph_primitives", False)
        print(f"  soft-raster       {library.path}")
        print(f"    0.5 primitives  {'yes' if primitives else 'no (rebuild it)'}")
    except Exception as error:  # noqa: BLE001 - report, never raise, from doctor
        print(f"  soft-raster       unavailable: {error}")

    try:
        import kitty_frame_presenter  # noqa: PLC0415, F401

        print("  frame presenter   yes")
    except ImportError:
        print("  frame presenter   no (a still image still draws)")

    from . import pane  # noqa: PLC0415

    print(f"  raster backend    {'ready' if raster.available() else 'not available'}")
    print(f"  chosen renderer   {render.choose()}")
    if pane.available() and raster.available():
        surface = "pane (pixels and a mouse, in this terminal)"
    elif os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY"):
        surface = "window (tkinter; no graphics protocol here)"
    else:
        surface = "none -- use `tui`"
    print(f"  gui surface       {surface}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="kilix-graphs",
        description="Draw graphs and charts in a terminal.",
    )
    parser.add_argument("--version", action="version", version=f"kilix-graphs {__version__}")
    subparsers = parser.add_subparsers(dest="command", required=True)

    def output_flags(sub: argparse.ArgumentParser) -> None:
        sub.add_argument("-o", "--out", help="write to a file (.ppm, .png, .svg, or text)")
        sub.add_argument(
            "--force", action="store_true",
            help="replace an existing regular file named by -o (never a symlink or the input)",
        )
        sub.add_argument(
            "-r", "--renderer", choices=_RENDERERS, default="auto",
            help="auto probes the terminal (default)",
        )
        sub.add_argument(
            "--scale", type=_bounded("--scale", 0.01, 64.0), default=1.0,
            help="pixels per scene unit (0.01 to 64)",
        )
        sub.add_argument("--theme", default="dark", choices=("dark", "light"))
        sub.add_argument("--ascii", action="store_true", help="text output without box drawing")
        sub.add_argument(
            "--colour", "--color", dest="colour", action="store_true", default=None,
            help="force ANSI colour in text output",
        )
        sub.add_argument(
            "--no-colour", "--no-color", dest="colour", action="store_false",
            help="never emit ANSI colour",
        )

    def layout_flags(sub: argparse.ArgumentParser) -> None:
        sub.add_argument(
            "-e", "--engine", default="layered", choices=sorted(layout.ENGINES),
        )
        sub.add_argument("-d", "--direction", default="TB", choices=("TB", "BT", "LR", "RL"))
        sub.add_argument("--nodesep", type=_bounded("--nodesep", 0.0, 4000.0))
        sub.add_argument("--ranksep", type=_bounded("--ranksep", 0.0, 4000.0))
        sub.add_argument(
            "--ranker",
            choices=("network-simplex", "coordinate-descent", "longest-path"),
            help="layered ranking; the default is exact",
        )
        sub.add_argument(
            "-f", "--format", choices=parse.FORMATS,
            help="input format (detected from the name or the content by default)",
        )

    draw = subparsers.add_parser("draw", help="lay out and render a graph")
    draw.add_argument("source", help="a .kg, .dot or .json file, or - for stdin")
    layout_flags(draw)
    output_flags(draw)
    draw.add_argument(
        "--font-scale", type=_bounded("--font-scale", 1, 8, int), default=1,
        dest="font_scale", help="integer label size multiplier (1 to 8)",
    )
    draw.add_argument(
        "--straight", dest="curved", action="store_false",
        help="poly-line edges instead of smoothed ones",
    )
    draw.set_defaults(func=cmd_draw, curved=True)

    positions = subparsers.add_parser("layout", help="positions only, as JSON")
    positions.add_argument("source")
    layout_flags(positions)
    positions.add_argument("-o", "--out")
    positions.add_argument("--force", action="store_true", help="replace an existing regular file")
    positions.set_defaults(func=cmd_layout)

    convert = subparsers.add_parser("convert", help="translate between input formats")
    convert.add_argument("source")
    convert.add_argument("-t", "--to", default="jgf", choices=("kg", "dot", "jgf"))
    convert.add_argument("-f", "--format", choices=parse.FORMATS)
    convert.add_argument("-o", "--out")
    convert.add_argument("--force", action="store_true", help="replace an existing regular file")
    convert.set_defaults(func=cmd_convert)

    chart = subparsers.add_parser("chart", help="render a chart from CSV or JSON")
    chart.add_argument("source", help="a .csv or .json file, or - for stdin")
    chart.add_argument("-m", "--mark", default="line", choices=("line", "bar", "scatter", "area"))
    chart.add_argument("--title", default="")
    chart.add_argument("--xlabel", default="")
    chart.add_argument("--ylabel", default="")
    chart.add_argument("--width", type=_bounded("--width", 80.0, 20000.0), default=760.0)
    chart.add_argument("--height", type=_bounded("--height", 60.0, 20000.0), default=420.0)
    chart.add_argument(
        "--no-zero", action="store_true",
        help="do not force a value axis to include zero (never use with bars)",
    )
    output_flags(chart)
    chart.set_defaults(func=cmd_chart)

    for name, function, blurb in (
        ("tui", cmd_tui, "open the interactive terminal viewer"),
        ("gui", cmd_gui, "open the graphical viewer: this pane, or a window"),
    ):
        viewer = subparsers.add_parser(name, help=blurb)
        viewer.add_argument("source", nargs="?", default="-" if name == "tui" else None,
                            help="a .kg, .dot or .json file")
        viewer.add_argument("-e", "--engine", default="layered", choices=sorted(layout.ENGINES))
        viewer.add_argument("-d", "--direction", default="TB", choices=("TB", "BT", "LR", "RL"))
        viewer.add_argument("--theme", default="dark", choices=("dark", "light"))
        viewer.add_argument("-f", "--format", choices=parse.FORMATS)
        if name == "gui":
            viewer.add_argument(
                "--scale", type=_bounded("--scale", 0.25, 8.0), default=1.0,
                help="initial zoom",
            )
            viewer.add_argument(
                "--window", action="store_true",
                help="open a desktop window instead of drawing in this pane",
            )
        viewer.set_defaults(func=function)

    syntax = subparsers.add_parser("syntax", help="what to write in a .kg file")
    syntax.set_defaults(func=cmd_syntax)

    doctor = subparsers.add_parser("doctor", help="report what this installation can do")
    doctor.set_defaults(func=cmd_doctor)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return int(args.func(args))
    except FileNotFoundError as error:
        print(f"kilix-graphs: {error.filename}: no such file", file=sys.stderr)
        return 2
    except (ValueError, OSError) as error:
        print(f"kilix-graphs: {error}", file=sys.stderr)
        return 1
    except BrokenPipeError:
        return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
