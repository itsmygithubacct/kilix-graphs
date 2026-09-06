# kilix-graphs

Graph creation and rendering for the Kilix desktop — node-link drawings and
charts, in a terminal, with no runtime dependencies.

```
$ kilix-graphs draw pipeline.kg
```

```
        Capture
       ╭─────────────╮
       │ ╭─────────╮ │
       │ │ camera  │ │
       │ ╰─────────╯ │
       │      │      │
       │  h264▼      │
       │  ╭───────╮  │
       │  │ rtsp  │  │
       │  ╰───────╯  │
       │      ▼      │
       │ ╭─────────╮ │
       │ │ decode  │ │
       │ ╰─────────╯ │
       ╰─────────────╯
```

In a terminal that speaks the Kitty graphics protocol — Kilix, or Kitty — the
same command draws the same graph in anti-aliased colour instead. Neither is a
degraded version of the other: they are two renderers over one scene.

## What it does

- **Reads** DOT, its own `.kg` line syntax, and JSON Graph Format.
- **Lays out** with five engines: layered (Sugiyama), force-directed, tree,
  circular and grid.
- **Routes** edges — poly-line through the layered dummy chain, smoothed,
  clipped to node outlines, with arrowheads, bowed parallel edges and
  self-loop lobes.
- **Renders** to pixels through [`soft-raster`](
  https://github.com/itsmygithubacct/soft-raster), to a cell grid with Unicode
  box drawing or plain ASCII, or to SVG.
- **Charts** too: line, bar, scatter and area over the same scene and the same
  renderers. In a cell grid the gridlines are dropped and lines are plotted on
  a braille sub-cell dot grid, because a gridline made of characters is as
  loud as the data crossing it.

## Install

```sh
python3 -m pip install .
```

Or run it straight from a clone, with nothing installed:

```sh
./kilix-graphs draw examples/pipeline.kg
```

Nothing is required at runtime. The pixel backend additionally wants
`soft-raster` 0.5 or later and its Python binding, both built from the
`soft-raster` repository (they are on no package index, so there is no extra
to install); without them the text and SVG backends still work and
`kilix-graphs doctor` says so.

Every file this tool writes on its own — the viewers' `w` key beside the
source, and `-o` — is created fresh: an existing file, a symlink, or the
source itself is refused rather than replaced. `-o` accepts `--force` to
replace an existing regular file; a symlink or the input is still refused.

## The `.kg` syntax

DOT is the interchange format. `.kg` is what you type.

```
# The Kilix frame path.
digraph FramePath

group capture: Capture
    camera -> rtsp: h264
    rtsp -> decode

decode -> present
present -> shm: "t=s"
present -> inline: "t=d,o=z"
terminal {shape=box}
```

- `#` starts a comment; blank lines are ignored.
- `a -> b` is a directed edge, `a -- b` an undirected one, and either chains:
  `a -> b -> c`.
- A trailing `: text` labels what precedes it — the edge on an edge line, the
  node on a node line.
- `{key=value, ...}` sets attributes, and may follow a label:
  `disk: Cold storage {shape=box}`. `label` and `shape` are lifted onto the
  node.
- `group id: Label` opens a cluster; indented lines are its members, and
  groups nest.

## Three interfaces

```sh
kilix-graphs tui  graph.kg     # cells, works anywhere
kilix-graphs gui  graph.kg     # pixels and a mouse, in this pane
kilix-graphs draw graph.kg     # one drawing, then exit
```

All three drive the same session, so a setting means the same thing in each
and none of them repeats the pipeline.

### `tui` — the terminal viewer

Curses. Every setting is one key, and the file is reloaded when it changes on
disk, so editing a `.kg` in one pane and watching it redraw in another is the
normal way to work.

| | |
| --- | --- |
| `arrows` `hjkl` | pan · `g`/`G` top/bottom · `0` recentre |
| `e` `E` | next / previous engine |
| `d` `t` `c` `u` | direction · theme · curves · unicode-or-ascii |
| `+` `-` | font scale |
| `r` `w` | reload now · write beside the source |
| `?` `q` | help · quit |

Needs no `soft-raster`: it draws through the same cell renderer as
`--renderer text`.

### `gui` — pixels and a mouse, in the pane

This is the Kilix-native graphical surface, and picking it over a desktop
toolkit is not a style preference. Of the 42 entries in the Kilix content
catalog, **39 launch as a terminal pane and exactly one opens an X window**;
sixteen declare `kitty-graphics` and three of those also take `kitty-mouse`.
The pieces are already modules: `soft-raster` draws, `kitty-frame-presenter`
puts the frame in the pane, and the mouse and keyboard arrive as terminal
escapes.

Drag to pan, wheel to zoom about the pointer, click to select a node — which
needs a hit test against real geometry, and is the thing a character grid
cannot do. Hovering rings the node under the pointer.

`gui` means "the best graphical surface there is" and degrades rather than
refusing: pane, then a tkinter window where there is a display but no graphics
protocol, then the cell viewer. Both toolkits are standard library, so no
surface costs this package a runtime dependency. `--window` asks for the window
directly, and `kilix-graphs doctor` says which one you would get.

There is a `kilix-ui`, and it is deliberately not used here: it is game UI in
C — menus, panels, meters, portraits — built on `kilix-top-down-engine`, and
its own status notes list pointer hit testing as a future feature.

### `draw` and the rest

```sh
kilix-graphs draw     graph.dot -o out.png      # or .ppm, .svg, or text
kilix-graphs draw     graph.kg -e force -d LR   # engine and direction
kilix-graphs draw     graph.dot --ranker longest-path   # compare rankers
kilix-graphs chart    data.csv -m bar --title "Frame budget"
kilix-graphs convert  graph.kg --to dot
kilix-graphs layout   graph.kg                  # positions as JSON, no renderer
kilix-graphs syntax                             # the .kg format, in full
kilix-graphs doctor                             # what this install can do
```

`draw` with no `-o` picks its renderer from what the terminal can do: pixels
where the graphics protocol is available, box drawing where it is not.
`--renderer` overrides the probe; `--ascii` drops to a charset that survives
any pipe.

## As a library

```python
import kilix_graphs as kg

graph = kg.parse.loads("a -> b -> c\nb -> d")
kg.layout.run(graph, "layered", direction="LR")
print(kg.render_text(kg.compose(graph)))
```

Building a graph directly, without a parser:

```python
from kilix_graphs import Graph, compose, layout, render_svg

graph = Graph(name="deps")
graph.edge("app", "lib", label="uses")
graph.node("lib", shape="box")
layout.run(graph)
open("deps.svg", "w").write(render_svg(compose(graph)))
```

## How it is put together

```
  source text            model            layout            scene           output
 ┌────────────┐      ┌───────────┐    ┌────────────┐    ┌───────────┐    ┌──────────────┐
 │ .dot .kg   │─────▶│ Graph     │───▶│ positions  │───▶│ ordered   │───▶│ raster       │
 │ .json      │ parse│  Node     │ eng│  + routes  │comp│ draw ops  │ back│ text · svg   │
 └────────────┘      └───────────┘    └────────────┘    └───────────┘    └──────────────┘
                                          chart data ──▶ scales/marks ──┘
```

The **scene** is the seam that makes the rest work. It is a flat, ordered list
of resolution-independent draw operations, and it is why:

- a node-link drawing and a chart can share one core without either knowing
  about the other;
- the text backend can be a *second renderer* rather than a downsampled
  raster — a corner in a cell grid is a junction glyph, not a dark pixel, and
  no amount of downsampling produces one;
- the tests assert on a list of tuples instead of comparing images.

`view` sits above all of it and is what the TUI and the GUI drive. It knows
which settings affect which stage, so changing the *theme* recomposes without
laying out again — on a 300-node graph that is 36 ms instead of 1.3 seconds,
which is the difference between an interface that feels alive and one that
does not.

`model` and `layout` import nothing from `scene`, `render` or `chart`, and a
test enforces it. That is what would let them lift into `kilix-modules` on the
day a second consumer wants them.

### Layered layout

The engine that carries the product is five phases, each resting on a
published algorithm:

| Phase | Algorithm |
| --- | --- |
| Acyclic | greedy feedback-arc set (Eades, Lin & Smyth 1993) |
| Rank | network simplex (Gansner et al. 1993), exact |
| Normalise | a dummy node per rank an edge crosses |
| Order | barycentre sweeps, best by weighted crossing count (Barth, Jünger & Mutzel) |
| Position | Brandes & Köpf, "Fast and Simple Horizontal Coordinate Assignment" |
| Separate | a left-to-right sweep so cluster boxes stay disjoint |

A group is an assertion about membership, so three things hold in **every**
engine, not only the layered one: sibling boxes never overlap, no box contains
a node from somewhere else, and a nested box stays strictly inside its parent.
The layered engine gets that from the rank structure; the others get it from a
relaxation pass, since a force layout has no idea what a group is.

Normalisation is why edge routing is nearly free: a long edge is *already* a
chain of dummy nodes with coordinates, so joining them is a route that
provably misses every node.

Ranking is exact. `--ranker` also offers `longest-path` and the
`coordinate-descent` local search that came before it, because a claim of
optimality is only worth something if the alternative is still runnable: the
exact ranker matches a brute-force optimum on every small graph tested, while
coordinate descent missed it on 19 of 111, and on a 150-node graph the exact
one is both **35% shorter in total edge length and faster** — a better ranking
makes fewer dummy nodes, and the ordering and positioning phases get the time
back.

### What it does not do

- Interactive editing. Rendering is read-only.
- Graph analysis — centrality, shortest paths, clustering. That is not a
  drawing concern.
- Obstacle-avoiding routing on force layouts. Layered routing is exact;
  force-layout edges are straight lines clipped to the node outlines.
- Reading SVG. Writing it is a small exporter; parsing it is a different
  project.
- Non-ASCII text **in the pixel backend**. The two faces soft-raster embeds
  cover ASCII 32..126 and draw anything else as `?`. Labels are still
  *measured* by display width, so a CJK label is sized and laid out correctly
  and renders properly in the text and SVG backends — it is the bitmap font
  that cannot draw it, not the layout that does not know about it.

## Development

```sh
make all           # build (what the Kilix content installer runs)
make check         # compile and run the suite on a bare interpreter
make test-raster   # the same suite with soft-raster on the path
make examples      # render examples/ into build/
```

The suite is 202 tests. The raster ones skip when `soft-raster` is absent
rather than failing, so `make check` is still a meaningful run anywhere.

## Licence

MIT. See [LICENSE](LICENSE).
