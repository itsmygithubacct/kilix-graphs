# Changelog

## 0.1.0 - 2026-09-02

First release.

- Graph model with clusters, parallel edges and self-loops. Labels are
  measured by terminal display width, so a CJK label is sized as it renders
  rather than as it counts. A node carries its
  intrinsic size, measured from its label, before any layout runs; layout
  assigns positions and never invents a size.
- Three inputs: a DOT parser for the subset that matters, the `.kg` line
  syntax, and JSON Graph Format. Format detection by extension, then by
  content.
- Five layout engines: layered (Sugiyama), force-directed, tree, circular and
  grid. The layered engine is a full five-phase pipeline — greedy
  feedback-arc set, exact network-simplex ranking, dummy
  chains, barycentre ordering scored by the Barth-Junger-Mutzel accumulator
  count, Brandes-Kopf coordinate assignment, and a cluster separation sweep
  that keeps cluster boxes disjoint and nothing inside a group it is not in.
- Edge routing: poly-line through the layered dummy chain, Catmull-Rom
  smoothing, clipping to box, ellipse and diamond outlines, arrowheads,
  parallel edges bowed apart, and self-loop lobes.
- A scene layer of resolution-independent draw operations, shared by the
  node-link and chart surfaces and consumed by every renderer.
- Three renderers: pixels through soft-raster 0.5, a cell grid with Unicode
  box drawing or plain ASCII, and SVG. Backend selection is a terminal
  capability probe, not a flag.
- Charts: linear, log, band and point scales, the d3 tick algorithm, and line,
  bar, scatter and area marks. A scene operation declares its role -- grid,
  axis or data -- so the cell renderer can drop chrome it cannot make
  recessive and plot the data on a braille sub-cell grid instead.
- Two themes, both validated for colourblind separation, lightness band and
  contrast against their own surface.
- A five-verb command: `draw`, `chart`, `convert`, `layout`, `doctor`.
- A `./kilix-graphs` launcher that runs the package straight from a
  checkout, and a `make all` build target, so the repository is runnable
  and installable without pip.
- 144 tests, no runtime dependencies. The raster tests skip rather than fail
  where soft-raster is absent.
