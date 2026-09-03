"""The desktop viewer: `kilix-graphs gui [FILE]`.

Tkinter, because it is in the standard library. A graph viewer that made this
package acquire its first runtime dependency would be a bad trade for a window
with six buttons in it.

Pixels reach Tk as PNG bytes held in memory. Tk 8.6 reads PNG from `data=` but
not the PPM the rasteriser writes natively, and the alternative -- a temporary
file per redraw, on every keystroke -- is worse than encoding one in-process.
The encoder is the same one the command line writes files with.

Without `soft-raster` the window still opens and shows the cell rendering in a
text widget, because "the drawing needs a native library you do not have" is
not a reason to show nothing.
"""

from __future__ import annotations

import base64
from pathlib import Path

from .view import DIRECTIONS, ENGINES, THEMES, Session, Settings

__all__ = ["run"]

#: How often to stat the file, in milliseconds.
POLL_MS = 500


class NoDisplay(RuntimeError):
    """Raised when there is no display to open a window on."""


class _App:
    def __init__(self, session: Session) -> None:
        import tkinter as tk  # noqa: PLC0415 - only when a window is wanted
        from tkinter import ttk  # noqa: PLC0415

        self.tk = tk
        self.ttk = ttk
        self.session = session
        self.photo = None  # kept alive: Tk does not own the reference
        self._shown: object = None

        self.root = tk.Tk()
        self.root.title(self._title())
        self.root.geometry("1100x820")
        self.root.minsize(520, 360)

        self._build_toolbar()
        self._build_canvas()
        self._build_status()
        self._bind_keys()

        self.root.after(POLL_MS, self._poll)
        self.refresh()

    # ---------------------------------------------------------------- chrome

    def _title(self) -> str:
        name = self.session.path.name if self.session.path else "<stdin>"
        return f"{name} — kilix-graphs"

    def _build_toolbar(self) -> None:
        tk, ttk = self.tk, self.ttk
        bar = ttk.Frame(self.root, padding=(8, 6))
        bar.pack(side="top", fill="x")

        ttk.Button(bar, text="Open…", command=self.open_file).pack(side="left")
        ttk.Button(bar, text="Reload", command=self.reload_now).pack(side="left", padx=(4, 12))

        self.engine = tk.StringVar(value=self.session.settings.engine)
        self.direction = tk.StringVar(value=self.session.settings.direction)
        self.theme = tk.StringVar(value=self.session.settings.theme)
        self.curved = tk.BooleanVar(value=self.session.settings.curved)
        self.zoom = tk.DoubleVar(value=self.session.settings.scale)

        def combo(label: str, variable, values, width: int):
            ttk.Label(bar, text=label).pack(side="left", padx=(0, 3))
            box = ttk.Combobox(
                bar, textvariable=variable, values=list(values),
                width=width, state="readonly",
            )
            box.pack(side="left", padx=(0, 10))
            box.bind("<<ComboboxSelected>>", lambda _event: self.apply())
            return box

        combo("Engine", self.engine, ENGINES, 9)
        self.direction_box = combo("Direction", self.direction, DIRECTIONS, 4)
        combo("Theme", self.theme, THEMES, 6)

        ttk.Checkbutton(
            bar, text="Curved", variable=self.curved, command=self.apply
        ).pack(side="left", padx=(0, 12))

        ttk.Label(bar, text="Zoom").pack(side="left", padx=(0, 3))
        ttk.Spinbox(
            bar, from_=0.25, to=6.0, increment=0.25, width=5,
            textvariable=self.zoom, command=self.apply,
        ).pack(side="left", padx=(0, 4))
        ttk.Button(bar, text="Fit", command=self.fit).pack(side="left", padx=(0, 12))

        ttk.Button(bar, text="Export…", command=self.export).pack(side="right")

    def _build_canvas(self) -> None:
        tk, ttk = self.tk, self.ttk
        frame = ttk.Frame(self.root)
        frame.pack(side="top", fill="both", expand=True)

        self.canvas = tk.Canvas(frame, background="#1a1a19", highlightthickness=0)
        vertical = ttk.Scrollbar(frame, orient="vertical", command=self.canvas.yview)
        horizontal = ttk.Scrollbar(frame, orient="horizontal", command=self.canvas.xview)
        self.canvas.configure(yscrollcommand=vertical.set, xscrollcommand=horizontal.set)
        self.canvas.grid(row=0, column=0, sticky="nsew")
        vertical.grid(row=0, column=1, sticky="ns")
        horizontal.grid(row=1, column=0, sticky="ew")
        frame.rowconfigure(0, weight=1)
        frame.columnconfigure(0, weight=1)

        # Drag to pan, which is what everyone tries first on a big drawing.
        self.canvas.bind("<ButtonPress-1>", lambda e: self.canvas.scan_mark(e.x, e.y))
        self.canvas.bind("<B1-Motion>", lambda e: self.canvas.scan_dragto(e.x, e.y, gain=1))
        self.canvas.bind("<MouseWheel>", self._wheel)
        self.canvas.bind("<Button-4>", lambda e: self.canvas.yview_scroll(-3, "units"))
        self.canvas.bind("<Button-5>", lambda e: self.canvas.yview_scroll(3, "units"))

        self.text = tk.Text(
            frame, wrap="none", font=("monospace", 10),
            background="#1a1a19", foreground="#d8d8d2", borderwidth=0,
        )  # gridded only when there are no pixels to show

    def _build_status(self) -> None:
        ttk = self.ttk
        self.status = ttk.Label(self.root, anchor="w", padding=(8, 4))
        self.status.pack(side="bottom", fill="x")

    def _bind_keys(self) -> None:
        for key, action in (
            ("<Control-o>", lambda e: self.open_file()),
            ("<Control-s>", lambda e: self.export()),
            ("<Control-r>", lambda e: self.reload_now()),
            ("<Control-q>", lambda e: self.root.destroy()),
            ("e", lambda e: self._cycle("engine", 1)),
            ("E", lambda e: self._cycle("engine", -1)),
            ("d", lambda e: self._cycle("direction", 1)),
            ("t", lambda e: self._cycle("theme", 1)),
            ("c", lambda e: (self.curved.set(not self.curved.get()), self.apply())),
            ("plus", lambda e: self._nudge_zoom(0.25)),
            ("equal", lambda e: self._nudge_zoom(0.25)),
            ("minus", lambda e: self._nudge_zoom(-0.25)),
            ("f", lambda e: self.fit()),
        ):
            self.root.bind(key, action)

    # --------------------------------------------------------------- actions

    def _cycle(self, name: str, step: int) -> None:
        self.session.cycle(name, step)
        self.engine.set(self.session.settings.engine)
        self.direction.set(self.session.settings.direction)
        self.theme.set(self.session.settings.theme)
        self.refresh()

    def fit(self) -> None:
        """Zoom so the whole drawing is on screen.

        A drawing pinned to the top-left of a window three times its size is
        the first thing anyone tries to fix, and doing it by hand means
        guessing at a number in a spinbox.
        """
        scene = self.session.scene()
        if scene is None or scene.width <= 0 or scene.height <= 0:
            return
        self.root.update_idletasks()
        # Ask whether the canvas is on screen, not how big it claims to be.
        # A hidden widget keeps reporting its last size, so a size test lets
        # `fit` run against the text fallback and set a zoom from a viewport
        # nobody is looking at.
        if not self.canvas.winfo_ismapped():
            return
        room_x = self.canvas.winfo_width() - 8
        room_y = self.canvas.winfo_height() - 8
        if room_x < 32 or room_y < 32:
            return
        wanted = min(room_x / scene.width, room_y / scene.height)
        self.zoom.set(round(max(0.25, min(6.0, wanted)), 2))
        self.apply()

    def _nudge_zoom(self, delta: float) -> None:
        self.zoom.set(round(max(0.25, min(6.0, self.zoom.get() + delta)), 2))
        self.apply()

    def _wheel(self, event) -> None:
        self.canvas.yview_scroll(-1 if event.delta > 0 else 1, "units")

    def apply(self) -> None:
        """Push the controls into the session and redraw."""
        self.session.update(
            engine=self.engine.get(),
            direction=self.direction.get(),
            theme=self.theme.get(),
            curved=bool(self.curved.get()),
            scale=float(self.zoom.get()),
        )
        self.refresh()

    def reload_now(self) -> None:
        self.session.reload(force=True)
        self.refresh()

    def open_file(self) -> None:
        from tkinter import filedialog  # noqa: PLC0415

        chosen = filedialog.askopenfilename(
            title="Open a graph",
            filetypes=[
                ("Graphs", "*.kg *.dot *.gv *.json *.jgf"),
                ("All files", "*"),
            ],
        )
        if not chosen:
            return
        self.session = Session(path=chosen, settings=self.session.settings)
        self.root.title(self._title())
        self.refresh()

    def export(self) -> None:
        from tkinter import filedialog, messagebox  # noqa: PLC0415

        stem = self.session.path.stem if self.session.path else "graph"
        chosen = filedialog.asksaveasfilename(
            title="Export", initialfile=f"{stem}.png",
            defaultextension=".png",
            filetypes=[("PNG", "*.png"), ("SVG", "*.svg"), ("Netpbm", "*.ppm")],
        )
        if not chosen:
            return
        try:
            self._write(Path(chosen))
        except Exception as error:  # noqa: BLE001 - a dialog, not a traceback
            messagebox.showerror("Export failed", str(error))
            return
        self.status.configure(text=f"wrote {chosen}")

    def _write(self, target: Path) -> None:
        from .render import raster  # noqa: PLC0415

        suffix = target.suffix.lower()
        if suffix == ".svg":
            target.write_text(self.session.svg(), encoding="utf-8")
            return
        canvas = self.session.raster()
        try:
            if suffix == ".ppm":
                canvas.write_ppm(str(target))
            else:
                target.write_bytes(raster.png_bytes(canvas))
        finally:
            canvas.close()

    # -------------------------------------------------------------- drawing

    def refresh(self) -> None:
        # A direction only means something to the two engines that rank.
        self.direction_box.configure(
            state="readonly" if self.session.settings.engine in ("layered", "tree") else "disabled"
        )
        scene = self.session.scene()
        if scene is None:
            self._show_message(self.session.error or "nothing to draw")
            return
        try:
            self._show_raster()
        except Exception as error:  # noqa: BLE001 - fall back, do not die
            self._show_text(str(error))
        self._update_status()

    def _show_raster(self) -> None:
        from .render import raster  # noqa: PLC0415

        canvas = self.session.raster()
        try:
            blob = raster.png_bytes(canvas)
        finally:
            canvas.close()
        self.photo = self.tk.PhotoImage(data=base64.b64encode(blob))
        self.text.grid_forget()
        self.canvas.grid(row=0, column=0, sticky="nsew")
        self.canvas.delete("all")
        # Centre a drawing smaller than the window rather than pinning it to
        # the corner, which reads as the window being broken rather than the
        # drawing being small.
        self.root.update_idletasks()
        room_x, room_y = self.canvas.winfo_width(), self.canvas.winfo_height()
        image_w, image_h = self.photo.width(), self.photo.height()
        offset_x = max(0, (room_x - image_w) // 2)
        offset_y = max(0, (room_y - image_h) // 2)
        self.canvas.create_image(offset_x, offset_y, anchor="nw", image=self.photo)
        self.canvas.configure(
            scrollregion=(0, 0, max(room_x, image_w + offset_x),
                          max(room_y, image_h + offset_y)),
            background=f"#{self.session.theme.surface:06x}",
        )

    def _show_text(self, why: str) -> None:
        """No pixels available: show the cell rendering and say why."""
        self.canvas.grid_forget()
        self.text.grid(row=0, column=0, sticky="nsew")
        theme = self.session.theme
        self.text.configure(
            background=f"#{theme.surface:06x}", foreground=f"#{theme.ink:06x}"
        )
        self.text.delete("1.0", "end")
        self.text.insert("1.0", self.session.text())
        self.status.configure(text=f"cell rendering — {why}")

    def _show_message(self, message: str) -> None:
        self.canvas.grid_forget()
        self.text.grid(row=0, column=0, sticky="nsew")
        self.text.delete("1.0", "end")
        self.text.insert("1.0", message)
        self.status.configure(text=message)

    def _update_status(self) -> None:
        stats = self.session.stats
        parts = [
            f"{stats.nodes} nodes",
            f"{stats.edges} edges",
        ]
        if stats.clusters:
            parts.append(f"{stats.clusters} groups")
        parts.append(f"{stats.width:.0f}×{stats.height:.0f}")
        if stats.layout_seconds:
            parts.append(f"laid out in {stats.layout_seconds * 1000:.0f} ms")
        self.status.configure(text="   ".join(parts))

    def _poll(self) -> None:
        if self.session.reload():
            self.refresh()
        self.root.after(POLL_MS, self._poll)

    def run(self) -> None:
        self.root.mainloop()


def run(session: Session) -> int:
    """Open the window. Returns a process exit status."""
    try:
        import tkinter  # noqa: PLC0415, F401
    except ImportError:
        print(
            "kilix-graphs: this Python has no tkinter. It is in the standard "
            "library but some distributions package it separately "
            "(python3-tk on Debian). Use `kilix-graphs tui` meanwhile."
        )
        return 3
    try:
        _App(session).run()
    except Exception as error:  # noqa: BLE001 - Tk raises TclError for no display
        if "no display" in str(error).lower() or "DISPLAY" in str(error):
            print(
                "kilix-graphs: no display to open a window on. "
                "Use `kilix-graphs tui` for the terminal viewer."
            )
            return 3
        raise
    return 0


def open_file(
    path: str | Path | None,
    source: str | None = None,
    settings: Settings | None = None,
    fmt: str | None = None,
) -> int:
    return run(Session(path=path, source=source, settings=settings, fmt=fmt))
