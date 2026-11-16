"""Open the pet on this machine with sample data and save screenshots of it.

usage: python tools/snap_pet.py <output folder>      (any system; needs Pillow)

Saves the pet on the real desktop, the pet on a white sheet (shows whether its
edges are clean on light backgrounds) and the open panel.
"""
import os
import sys
import tempfile
import threading
import time

from PIL import ImageGrab

from make_demo import sample_engine  # also puts the repository on sys.path

from smartram import widget


def capture(out, prefix="", transparent=True):
    w = widget.Widget(sample_engine(tempfile.mkdtemp()), threading.Event(),
                      transparent=transparent)
    root = w.root
    screen_w, screen_h = root.winfo_screenwidth(), root.winfo_screenheight()

    def grab(name, pad, zoom=1):
        for _ in range(4):  # let the window manager and compositor settle
            root.lift()
            root.update()
            time.sleep(0.4)
        shot = ImageGrab.grab()
        scale = shot.width / float(screen_w)  # 2 on Retina screens
        x, y = root.winfo_rootx(), root.winfo_rooty()
        box = (max(0, x - pad), max(0, y - pad),
               min(screen_w, x + root.winfo_width() + pad),
               min(screen_h, y + root.winfo_height() + pad))
        part = shot.crop([int(v * scale) for v in box])
        if zoom > 1:
            part = part.resize((part.width * zoom, part.height * zoom))
        part.save(os.path.join(out, prefix + name))
        print("saved %s%s %s (screen %dx%d, scale %.1f)"
              % (prefix, name, part.size, screen_w, screen_h, scale))

    grab("pet-on-desktop.png", 50, zoom=3)
    sheet = w.tk.Toplevel(root)
    sheet.overrideredirect(True)
    sheet.configure(bg="white")
    sheet.geometry("220x220+%d+%d" % (w.px - 78, w.py - 78))
    sheet.update()
    grab("pet-on-white.png", 50, zoom=3)
    sheet.destroy()
    w.toggle()
    grab("panel.png", 24)
    w._ask("Spotify")
    grab("panel-asking.png", 24)
    w._quit()


def main(out):
    os.makedirs(out, exist_ok=True)
    capture(out)
    if sys.platform == "darwin":
        # For comparison: the pet without a see-through window.
        capture(out, "boxed-", transparent=False)


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "shots")
