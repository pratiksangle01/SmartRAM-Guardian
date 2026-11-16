"""Desktop pet: a small always-on-top face that expands into a status panel on click."""
from __future__ import annotations

import sys
import time

SIZE = 56
DARK = "#14161c"
TEXT = "#e5e7eb"
MUTED = "#9ca3af"
KEY = "#010203"  # colour made see-through on Windows
COLORS = {"ok": "#4ade80", "busy": "#fbbf24", "high": "#f87171", "paused": "#9ca3af"}
ROWS = 5


def fmt_bytes(n):
    n = float(n or 0)
    if n >= 2**30:
        return "%.1f GB" % (n / 2**30)
    return "%.0f MB" % (n / 2**20)


def _row(name, size, tail):
    return "%-17s %8s  %s" % (name[:17], fmt_bytes(size), tail)


def panel_text(snap):
    """The text blocks shown in the expanded panel."""
    mem = snap.get("mem") or {}
    total, avail = mem.get("total", 0), mem.get("available", 0)
    closed = snap.get("closed") or []
    lines = [_row(e["name"], e["bytes"], time.strftime("%H:%M", time.localtime(e["time"])))
             for e in closed[:ROWS]]
    for name in snap.get("closing") or []:
        lines.insert(0, "%-17s %8s  %s" % (name[:17], "", "closing..."))
    kept = [_row(k["name"], k["bytes"], k["reason"]) for k in (snap.get("kept") or [])[:ROWS]]
    return {
        "status": snap.get("note", "starting..."),
        "ram": "RAM %.0f%%   %s of %s used" % (mem.get("percent", 0),
                                              fmt_bytes(total - avail), fmt_bytes(total)),
        "saved": "Saved %s   (this session %s)" % (fmt_bytes(snap.get("saved_total")),
                                                   fmt_bytes(snap.get("saved_session"))),
        "focus": "Working in: %s" % (snap.get("focus") or "-"),
        "closed_title": "Closed (%d)" % snap.get("closed_total", 0),
        "closed": "\n".join(lines[:ROWS]) or "nothing yet",
        "kept": "\n".join(kept) or "-",
    }


def mood(snap, threshold):
    if snap.get("paused"):
        return "paused"
    pct = (snap.get("mem") or {}).get("percent", 0)
    if pct >= 90:
        return "high"
    return "busy" if pct >= threshold else "ok"


class Widget:
    def __init__(self, engine, stop_event, selftest=0):
        import tkinter as tk
        import tkinter.font as tkfont
        self.tk, self.engine, self.stop = tk, engine, stop_event
        self.expanded, self.frame, self._drag = False, 0, None

        root = self.root = tk.Tk()
        root.title("SmartRAM Guardian")
        root.overrideredirect(True)
        root.configure(bg=DARK)
        for attr, value in (("-topmost", True), ("-alpha", 0.97)):
            try:
                root.attributes(attr, value)
            except tk.TclError:
                pass
        self._clear = DARK
        try:
            if sys.platform == "win32":
                root.attributes("-transparentcolor", KEY)
                self._clear = KEY
            elif sys.platform == "darwin":
                root.attributes("-transparent", True)
                self._clear = "systemTransparent"
        except tk.TclError:
            self._clear = DARK

        mono = tkfont.nametofont("TkFixedFont").copy()
        mono.configure(size=9)
        bold = tkfont.nametofont("TkDefaultFont").copy()
        bold.configure(size=10, weight="bold")

        self.pet = tk.Canvas(root, width=SIZE, height=SIZE, highlightthickness=0, bd=0)
        self.head = tk.Frame(root, bg=DARK)
        tk.Label(self.head, text="SmartRAM Guardian", font=bold, bg=DARK, fg=TEXT,
                 anchor="w").pack(fill="x", padx=8)
        self.labels = {}
        self._label(self.head, "status", mono, MUTED).pack(fill="x", padx=8)

        self.panel = tk.Frame(root, bg=DARK)
        self._label(self.panel, "ram", mono, TEXT).pack(fill="x", padx=10, pady=(6, 2))
        self.bar = tk.Canvas(self.panel, height=6, width=280, bg="#2a2e39",
                             highlightthickness=0, bd=0)
        self.bar.pack(padx=10, pady=2)
        self._label(self.panel, "saved", mono, COLORS["ok"]).pack(fill="x", padx=10)
        self._label(self.panel, "focus", mono, TEXT).pack(fill="x", padx=10, pady=(0, 6))
        self._label(self.panel, "closed_title", bold, TEXT).pack(fill="x", padx=10)
        self._label(self.panel, "closed", mono, MUTED).pack(fill="x", padx=10)
        tk.Label(self.panel, text="Kept running", font=bold, bg=DARK, fg=TEXT,
                 anchor="w").pack(fill="x", padx=10, pady=(6, 0))
        self._label(self.panel, "kept", mono, MUTED).pack(fill="x", padx=10)
        buttons = tk.Frame(self.panel, bg=DARK)
        buttons.pack(fill="x", padx=10, pady=8)
        self.pause_btn = self._button(buttons, "Pause", self._toggle_pause)
        self.pause_btn.pack(side="left")
        self._button(buttons, "Quit", self._quit).pack(side="right")

        self.pet.bind("<ButtonPress-1>", self._press)
        self.pet.bind("<B1-Motion>", self._motion)
        self.pet.bind("<ButtonRelease-1>", self._release)

        # Pet's screen position; the panel grows away from the nearest screen edges.
        self.px = root.winfo_screenwidth() - SIZE - 24
        self.py = root.winfo_screenheight() - SIZE - 96
        self._layout()
        self._tick()
        if selftest:
            root.after(300, self.toggle)
            root.after(300 + int(selftest * 1000), self._quit)

    def _label(self, parent, key, font, fg):
        lbl = self.tk.Label(parent, font=font, bg=DARK, fg=fg, anchor="w", justify="left")
        self.labels[key] = lbl
        return lbl

    def _button(self, parent, text, command):
        return self.tk.Button(parent, text=text, command=command, bg="#2a2e39", fg=TEXT,
                              activebackground="#3b4050", activeforeground=TEXT,
                              relief="flat", bd=0, padx=12, pady=3, highlightthickness=0)

    def _layout(self):
        root = self.root
        sw, sh = root.winfo_screenwidth(), root.winfo_screenheight()
        self.px = max(0, min(self.px, sw - SIZE))
        self.py = max(0, min(self.py, sh - SIZE))
        if not self.expanded:
            self.head.grid_forget()
            self.panel.grid_forget()
            self.pet.configure(bg=self._clear)
            root.configure(bg=self._clear)
            self.pet.grid(row=0, column=0)
            root.geometry("%dx%d+%d+%d" % (SIZE, SIZE, self.px, self.py))
            return
        right, bottom = self.px + SIZE / 2 > sw / 2, self.py + SIZE / 2 > sh / 2
        self.pet.configure(bg=DARK)
        root.configure(bg=DARK)
        row = 2 if bottom else 0
        self.pet.grid(row=row, column=1 if right else 0)
        self.head.grid(row=row, column=0 if right else 1, sticky="ew")
        self.panel.grid(row=1, column=0, columnspan=2, sticky="ew")
        root.update_idletasks()
        w, h = root.winfo_reqwidth(), root.winfo_reqheight()
        x = self.px + SIZE - w if right else self.px
        y = self.py + SIZE - h if bottom else self.py
        root.geometry("%dx%d+%d+%d" % (w, h, max(0, x), max(0, y)))

    def toggle(self):
        self.expanded = not self.expanded
        self._refresh()
        self._layout()

    def _press(self, e):
        self._drag = (e.x_root, e.y_root, self.px, self.py, False)

    def _motion(self, e):
        if not self._drag:
            return
        x0, y0, px, py, moved = self._drag
        dx, dy = e.x_root - x0, e.y_root - y0
        if moved or abs(dx) + abs(dy) > 4:
            self._drag = (x0, y0, px, py, True)
            self.px, self.py = px + dx, py + dy
            self._layout()

    def _release(self, _e):
        moved = self._drag and self._drag[4]
        self._drag = None
        if not moved:
            self.toggle()

    def _toggle_pause(self):
        pause = self.engine.paths.pause
        try:
            if pause.exists():
                pause.unlink()
            else:
                pause.write_text("paused from widget", encoding="utf-8")
        except OSError:
            pass
        self._refresh()

    def _quit(self):
        self.stop.set()
        self.root.destroy()

    def _draw_pet(self, snap):
        c, s = self.pet, SIZE
        paused = self.engine.paths.pause.exists()
        color = COLORS["paused" if paused else mood(snap, self.engine.cfg["act_above_percent"])]
        dy = 0 if paused else (self.frame // 2) % 2  # gentle bob shows it is alive
        blink = paused or self.frame % 10 == 0
        working = bool(snap.get("closing"))
        c.delete("all")
        for x1, x2, x3 in ((10, 15, 25), (s - 10, s - 15, s - 25)):
            c.create_polygon(x1, 20 + dy, x2, 3 + dy, x3, 12 + dy, fill=color, outline="")
        c.create_oval(5, 9 + dy, s - 5, s - 3 + dy, fill=color, outline="#0b0d12", width=2)
        for x in (20, s - 20):
            if blink:
                c.create_line(x - 4, 29 + dy, x + 4, 29 + dy, fill="#0b0d12", width=2)
            else:
                c.create_oval(x - 4, 24 + dy, x + 4, 34 + dy, fill="#0b0d12", outline="")
                c.create_oval(x - 1, 26 + dy, x + 2, 29 + dy, fill="white", outline="")
        if working:
            c.create_oval(s / 2 - 4, 37 + dy, s / 2 + 4, 45 + dy, fill="#0b0d12", outline="")
        else:
            c.create_arc(s / 2 - 6, 33 + dy, s / 2 + 6, 43 + dy, start=200, extent=140,
                         style="arc", outline="#0b0d12", width=2)

    def _refresh(self):
        snap = self.engine.snapshot or {}
        self._draw_pet(snap)
        if not self.expanded:
            return
        text = panel_text(snap)
        for key, lbl in self.labels.items():
            lbl.configure(text=text[key])
        paused = self.engine.paths.pause.exists()
        self.pause_btn.configure(text="Resume" if paused else "Pause")
        pct = (snap.get("mem") or {}).get("percent", 0)
        self.bar.delete("all")
        self.bar.create_rectangle(0, 0, 280 * pct / 100.0, 6, outline="",
                                  fill=COLORS[mood(snap, self.engine.cfg["act_above_percent"])])

    def _tick(self):
        if self.stop.is_set():
            self.root.destroy()
            return
        self.frame += 1
        before = self.root.winfo_reqheight()
        self._refresh()
        if self.expanded:
            self.root.update_idletasks()
            if self.root.winfo_reqheight() != before:
                self._layout()
        self.root.lift()
        self.root.after(400, self._tick)

    def run(self):
        self.root.mainloop()


def run(engine, stop_event, selftest=0):
    """Show the pet until quit. Returns False when no display is available."""
    try:
        widget = Widget(engine, stop_event, selftest)
    except Exception:
        return False
    widget.run()
    return True
