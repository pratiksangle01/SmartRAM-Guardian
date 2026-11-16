"""Desktop pet: a small always-on-top dolphin that expands into a status panel on click."""
from __future__ import annotations

import logging
import sys
import time

from . import config as cfgmod
from . import pet_art, rules

log = logging.getLogger("smartram")

SIZE = pet_art.SIZE
DARK = "#14161c"
RAISED = "#2a2e39"
HOVER = "#3b4050"
TEXT = "#e5e7eb"
MUTED = "#9ca3af"
KEY = "#010203"  # colour made see-through on Windows
COLORS = {"ok": "#4ade80", "busy": "#fbbf24", "high": "#f87171", "paused": "#9ca3af"}
ROWS = 5
BAR = 280


def fmt_bytes(n):
    n = float(n or 0)
    if n >= 2**30:
        return "%.1f GB" % (n / 2**30)
    return "%.0f MB" % (n / 2**20)


def _row(name, size, tail):
    return "%-17s %8s  %s" % (name[:17], fmt_bytes(size), tail)


def tip_row(tip):
    return "%-16s %8s  %d min" % (tip["name"][:16], fmt_bytes(tip["bytes"]), tip["idle_minutes"])


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
    tips = [_row(s["name"], s["bytes"], "idle %d min" % s["idle_minutes"])
            for s in snap.get("suggest") or []]
    if tips:
        tips.append("to close these too: smartram allow <app>")
    own = snap.get("self") or {}
    own_text = "Guardian itself: %s" % fmt_bytes(own.get("rss"))
    if own.get("cpu") is not None:
        own_text += ", %.1f%% CPU" % own["cpu"]
    if snap.get("battery"):
        own_text += ", battery saver"
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
        "tips": "\n".join(tips),
        "self": own_text,
    }


def mood(snap, threshold):
    if snap.get("paused"):
        return "paused"
    pct = (snap.get("mem") or {}).get("percent", 0)
    if pct >= 90:
        return "high"
    return "busy" if pct >= threshold else "ok"


def new_root(tk, tries=3, wait=time.sleep):
    """A Tk root window.

    On Windows Tk now and then cannot read its own startup file and works again a
    moment later. Anything else, such as a missing display, is reported at once.
    """
    for left in range(tries - 1, -1, -1):
        try:
            return tk.Tk()
        except tk.TclError as e:
            if not left or "init.tcl" not in str(e):
                raise
            wait(1)


class Widget:
    def __init__(self, engine, stop_event, selftest=0):
        import tkinter as tk
        import tkinter.font as tkfont
        self.tk, self.engine, self.stop = tk, engine, stop_event
        self.expanded, self.frame, self._drag = False, 0, None
        self._pet_state = self._shown = None  # what is drawn now; redraw only on change
        self._tips_shown = self._asking = None
        self._allowed = set()  # suggestions just accepted, hidden until the engine catches up

        root = self.root = new_root(tk)
        root.title("SmartRAM Guardian")
        root.overrideredirect(True)
        root.configure(bg=DARK)
        for attr, value in (("-topmost", True), ("-alpha", 0.97)):
            try:
                root.attributes(attr, value)
            except tk.TclError:
                pass
        # On Windows only the dolphin shows, with no box around it. macOS does not paint
        # pictures on a see-through window (checked on a real machine), so there and
        # on Linux the pet sits on a small dark tile.
        self._clear = DARK
        if sys.platform == "win32":
            try:
                root.attributes("-transparentcolor", KEY)
                self._clear = KEY
            except tk.TclError:
                pass

        # A colour key cannot blend soft edges, so Windows gets the hard-edged drawings.
        prefix = "CRISP_" if self._clear == KEY else ""
        self.art = {name: tk.PhotoImage(master=root, data=getattr(pet_art, prefix + name))
                    for name in ("OPEN", "BLINK", "PAUSED")}
        mono = self.mono = tkfont.nametofont("TkFixedFont").copy()
        mono.configure(size=9)
        bold = tkfont.nametofont("TkDefaultFont").copy()
        bold.configure(size=10, weight="bold")

        self.pet = tk.Frame(root, width=SIZE, height=SIZE, bd=0, highlightthickness=0)
        self.pet_img = tk.Label(self.pet, image=self.art["OPEN"], bd=0, highlightthickness=0)
        self.pet_img.place(x=0, y=0)
        # Status dot: green fine, yellow busy, red high, grey paused.
        self.dot = tk.Canvas(self.pet, width=13, height=13, bd=0, highlightthickness=0)
        self._dot = self.dot.create_oval(1, 1, 12, 12, outline="#0b0d12", width=2)
        self.dot.place(x=3, y=SIZE - 16)
        self._pet_parts = (self.pet, self.pet_img, self.dot)
        self.head = tk.Frame(root, bg=DARK)
        tk.Label(self.head, text="SmartRAM Guardian", font=bold, bg=DARK, fg=TEXT,
                 anchor="w").pack(fill="x", padx=8)
        self.labels = {}
        self._label(self.head, "status", mono, MUTED).pack(fill="x", padx=8)

        self.panel = tk.Frame(root, bg=DARK)
        self._label(self.panel, "ram", mono, TEXT).pack(fill="x", padx=10, pady=(6, 2))
        self.bar = tk.Canvas(self.panel, height=6, width=BAR, bg=RAISED,
                             highlightthickness=0, bd=0)
        self._fill = self.bar.create_rectangle(0, 0, 0, 6, outline="")
        self.bar.pack(padx=10, pady=2)
        self._label(self.panel, "saved", mono, COLORS["ok"]).pack(fill="x", padx=10)
        self._label(self.panel, "focus", mono, TEXT).pack(fill="x", padx=10, pady=(0, 6))
        self._label(self.panel, "closed_title", bold, TEXT).pack(fill="x", padx=10)
        self._label(self.panel, "closed", mono, MUTED).pack(fill="x", padx=10)
        tk.Label(self.panel, text="Kept running", font=bold, bg=DARK, fg=TEXT,
                 anchor="w").pack(fill="x", padx=10, pady=(6, 0))
        self._label(self.panel, "kept", mono, MUTED).pack(fill="x", padx=10)
        # Shown only while there is something to suggest.
        self.tips = tk.Frame(self.panel, bg=DARK)
        tk.Label(self.tips, text="Idle in the background", font=bold, bg=DARK, fg=TEXT,
                 anchor="w").pack(fill="x", padx=10, pady=(6, 0))
        self.tip_rows = tk.Frame(self.tips, bg=DARK)
        self.tip_rows.pack(fill="x", padx=10)
        self.footer = self._label(self.panel, "self", mono, "#6b7280")
        self.footer.pack(fill="x", padx=10, pady=(6, 0))
        buttons = tk.Frame(self.panel, bg=DARK)
        buttons.pack(fill="x", padx=10, pady=8)
        self.pause_btn = self._button(buttons, "Pause", self._toggle_pause)
        self.pause_btn.pack(side="left")
        self.clean_btn = self._button(buttons, "Clean now", self._clean_now)
        self.clean_btn.pack(side="left", padx=6)
        self._button(buttons, "Quit", self._quit).pack(side="right")

        for part in self._pet_parts:
            part.bind("<ButtonPress-1>", self._press)
            part.bind("<B1-Motion>", self._motion)
            part.bind("<ButtonRelease-1>", self._release)

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

    def _button(self, parent, text, command, small=False):
        """A flat button. Built from a label: native buttons ignore colours on macOS."""
        btn = self.tk.Label(parent, text=text, bg=RAISED, fg=TEXT, cursor="hand2",
                            padx=6 if small else 12, pady=0 if small else 3)
        if small:
            btn.configure(font=self.mono)
        btn.command = command
        btn.bind("<ButtonRelease-1>", lambda _e: command())
        btn.bind("<Enter>", lambda _e: btn.configure(bg=HOVER))
        btn.bind("<Leave>", lambda _e: btn.configure(bg=RAISED))
        return btn

    def _layout(self):
        root = self.root
        sw, sh = root.winfo_screenwidth(), root.winfo_screenheight()
        self.px = max(0, min(self.px, sw - SIZE))
        self.py = max(0, min(self.py, sh - SIZE))
        if not self.expanded:
            self.head.grid_forget()
            self.panel.grid_forget()
            for part in self._pet_parts:
                part.configure(bg=self._clear)
            root.configure(bg=self._clear)
            self.pet.grid(row=0, column=0)
            self._size = (SIZE, SIZE)
            root.geometry("%dx%d+%d+%d" % (SIZE, SIZE, self.px, self.py))
            return
        right, bottom = self.px + SIZE / 2 > sw / 2, self.py + SIZE / 2 > sh / 2
        for part in self._pet_parts:
            part.configure(bg=DARK)
        root.configure(bg=DARK)
        row = 2 if bottom else 0
        self.pet.grid(row=row, column=1 if right else 0)
        self.head.grid(row=row, column=0 if right else 1, sticky="ew")
        self.panel.grid(row=1, column=0, columnspan=2, sticky="ew")
        root.update_idletasks()
        w, h = self._size = (root.winfo_reqwidth(), root.winfo_reqheight())
        x = self.px + SIZE - w if right else self.px
        y = self.py + SIZE - h if bottom else self.py
        root.geometry("%dx%d+%d+%d" % (w, h, max(0, x), max(0, y)))

    def _relayout(self):
        """Resize the open panel when its content grew or shrank."""
        if self.expanded:
            self.root.update_idletasks()
            if (self.root.winfo_reqwidth(), self.root.winfo_reqheight()) != self._size:
                self._layout()

    def toggle(self):
        self.expanded = not self.expanded
        # While the panel is open the engine keeps its details fresh; closed, it rests.
        self.engine.watching = self.expanded
        self._asking = None
        if self.expanded:
            self._fill_panel()
            self.engine.wake()
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

    def _paused(self):
        return self.engine.paths.pause.exists()

    def _toggle_pause(self):
        pause = self.engine.paths.pause
        try:
            if pause.exists():
                pause.unlink()
            else:
                pause.write_text("paused from widget", encoding="utf-8")
        except OSError:
            pass
        self._draw_pet()
        if self.expanded:
            self._fill_panel()
        self.engine.wake()

    def _clean_now(self):
        try:
            self.engine.paths.clean.write_text("clean", encoding="utf-8")
        except OSError:
            pass
        self.engine.wake()

    def _ask(self, name):
        """First click on Allow asks; the row turns into a yes/no question."""
        self._asking = name
        self._fill_tips(self.engine.snapshot or {})
        self._relayout()

    def _allow(self, name):
        """Let the guardian close this app when it idles, starting now."""
        self._asking = None
        if not rules.is_protected(rules.normalize(name)):
            try:
                cfgmod.update_lists(self.engine.paths.config, name, "close_list",
                                    ["protect_list"])
                self.engine.paths.clean.write_text("clean", encoding="utf-8")
                self._allowed.add(name)
            except OSError:
                pass
        self._fill_tips(self.engine.snapshot or {})
        self._relayout()
        self.engine.wake()

    def _quit(self):
        self.stop.set()
        self.engine.wake()
        self.root.destroy()

    def _draw_pet(self, blink=False):
        snap = self.engine.snapshot or {}
        paused = self._paused()
        color = COLORS["paused" if paused else mood(snap, self.engine.cfg["act_above_percent"])]
        working = bool(snap.get("closing")) and not paused
        # A gentle swim shows it is alive; it swims faster while closing something.
        dy = 0 if paused else (self.frame // (1 if working else 2)) % 2 * 2
        art = "PAUSED" if paused else "BLINK" if blink else "OPEN"
        state = (art, dy, color)
        if state != self._pet_state:
            self._pet_state = state
            self.pet_img.configure(image=self.art[art])
            self.pet_img.place(x=0, y=dy)
            self.dot.itemconfigure(self._dot, fill=color)

    def _fill_tips(self, snap):
        """One row per idle app, each with a button that lets the guardian close it."""
        offered = snap.get("suggest") or []
        self._allowed &= {s["name"] for s in offered}
        tips = [s for s in offered if s["name"] not in self._allowed]
        shown = ([(s["name"], s["bytes"], s["idle_minutes"]) for s in tips], self._asking)
        if shown == self._tips_shown:
            return
        self._tips_shown = shown
        for child in self.tip_rows.winfo_children():
            child.destroy()
        for s in tips:
            name = s["name"]
            row = self.tk.Frame(self.tip_rows, bg=DARK)
            row.pack(fill="x", pady=1)
            asking = name == self._asking
            text = "Close %s when idle?" % name[:16] if asking else tip_row(s)
            self.tk.Label(row, text=text, font=self.mono, bg=DARK, fg=COLORS["busy"],
                          anchor="w").pack(side="left")
            if asking:
                self._button(row, "No", lambda: self._ask(None), small=True).pack(side="right")
                self._button(row, "Yes", lambda n=name: self._allow(n),
                             small=True).pack(side="right", padx=4)
            else:
                self._button(row, "Allow", lambda n=name: self._ask(n),
                             small=True).pack(side="right")
        if tips:
            self.tips.pack(fill="x", before=self.footer)
        else:
            self.tips.pack_forget()

    def _fill_panel(self):
        snap = self.engine.snapshot or {}
        paused = self._paused()
        self._shown = (snap.get("updated"), paused)
        text = panel_text(snap)
        for key, lbl in self.labels.items():
            lbl.configure(text=text[key])
        self._fill_tips(snap)
        self.pause_btn.configure(text="Resume" if paused else "Pause")
        pct = (snap.get("mem") or {}).get("percent", 0)
        self.bar.coords(self._fill, 0, 0, BAR * pct / 100.0, 6)
        self.bar.itemconfigure(
            self._fill, fill=COLORS[mood(snap, self.engine.cfg["act_above_percent"])])

    def _refresh_panel(self):
        """Rewrite the open panel only when its data changed."""
        snap = self.engine.snapshot or {}
        if (snap.get("updated"), self._paused()) == self._shown:
            return
        self._fill_panel()
        self._relayout()

    def _tick(self):
        if self.stop.is_set():
            self.root.destroy()
            return
        self.frame += 1
        blink = self.frame % 10 == 0
        self._draw_pet(blink)
        if blink:
            self.root.after(160, self._draw_pet)
        if self.expanded:
            self._refresh_panel()
        if self.frame % 6 == 0:
            self.root.lift()
        # Half the animation rate on battery.
        on_battery = (self.engine.snapshot or {}).get("battery")
        self.root.after(1000 if on_battery else 500, self._tick)

    def run(self):
        self.root.mainloop()


def run(engine, stop_event, selftest=0):
    """Show the pet until quit. Returns False when it cannot be shown."""
    try:
        widget = Widget(engine, stop_event, selftest)
    except Exception as e:
        log.warning("running without the desktop pet: %s: %s", type(e).__name__, e)
        return False
    widget.run()
    return True
