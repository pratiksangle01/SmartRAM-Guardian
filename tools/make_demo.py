"""Render the README picture (docs/demo.gif) from the real widget.

The widget is driven with sample data so the picture does not depend on this
machine. Linux only; needs Xvfb and ImageMagick:

    xvfb-run -a -s "-screen 0 470x560x24" python tools/make_demo.py
"""
import os
import subprocess
import sys
import tempfile
import threading
import time
import types

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)

from smartram import config, widget  # noqa: E402

MB = 2**20
NOW = time.time()
SAMPLE = {
    "updated": NOW, "note": "active", "paused": False, "battery": False,
    "mem": {"total": 16 * 2**30, "available": 5 * 2**30, "percent": 68.7},
    "focus": "Code", "saved_total": 1740 * MB, "saved_session": 412 * MB, "closed_total": 14,
    "closing": [],
    "closed": [{"name": "GoogleUpdater", "bytes": 212 * MB, "time": NOW - 120},
               {"name": "AdobeARM", "bytes": 48 * MB, "time": NOW - 900},
               {"name": "crashpad_handler", "bytes": 152 * MB, "time": NOW - 4000}],
    "kept": [{"name": "Code", "bytes": 1300 * MB, "reason": "in use"},
             {"name": "firefox", "bytes": 2100 * MB, "reason": "not on close list"},
             {"name": "Docker Desktop", "bytes": 900 * MB, "reason": "protected"}],
    "suggest": [{"name": "Spotify", "bytes": 410 * MB, "idle_minutes": 42},
                {"name": "Discord", "bytes": 380 * MB, "idle_minutes": 25}],
    "self": {"cpu": 0.2, "rss": 34 * MB, "slowdown": 1},
}


def main():
    out = os.path.join(REPO, "docs")
    os.makedirs(out, exist_ok=True)
    work = tempfile.mkdtemp()
    engine = types.SimpleNamespace(snapshot=SAMPLE, paths=config.Paths(work).ensure(),
                                   cfg=dict(config.DEFAULTS), watching=False, wake=lambda: None)
    w = widget.Widget(engine, threading.Event())
    root = w.root
    sw, sh = root.winfo_screenwidth(), root.winfo_screenheight()

    # A desktop-like backdrop so the pet is seen the way it sits on a real screen.
    back = w.tk.Toplevel(root)
    back.overrideredirect(True)
    back.geometry("%dx%d+0+0" % (sw, sh))
    canvas = w.tk.Canvas(back, width=sw, height=sh, highlightthickness=0, bd=0)
    canvas.pack()
    for y in range(sh):
        t = y / float(sh)
        canvas.create_line(0, y, sw, y, fill="#%02x%02x%02x" % (
            int(58 - 26 * t), int(84 - 34 * t), int(140 - 52 * t)))
    w.px, w.py = sw - widget.SIZE - 22, sh - widget.SIZE - 22
    w._layout()

    frames = []

    def shot(delay):
        back.lower()
        root.lift()
        root.update()
        path = os.path.join(work, "f%02d.png" % len(frames))
        subprocess.run(["import", "-window", "root", path], check=True)
        frames.append((delay, path))

    def pet(frame, blink=False):
        w.frame = frame
        w._pet_state = None
        w._draw_pet(blink)

    for frame in (0, 2, 0):
        pet(frame)
        shot(60)
    pet(2, blink=True)
    shot(18)
    w.toggle()
    pet(0)
    shot(120)
    pet(2)
    shot(120)
    pet(0)
    shot(160)
    w.toggle()
    pet(2)
    shot(60)

    args = ["convert", "-loop", "0"]
    for delay, path in frames:
        args += ["-delay", str(delay), path]
    subprocess.run(args + ["-layers", "Optimize", os.path.join(out, "demo.gif")], check=True)
    print("wrote docs/demo.gif")


if __name__ == "__main__":
    main()
