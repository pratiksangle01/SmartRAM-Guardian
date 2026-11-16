"""Draws the dolphin pet and writes smartram/pet_art.py.

Only needed to change the artwork. Requires Pillow: pip install pillow
"""
import base64
import io
import math
from pathlib import Path

from PIL import Image, ImageChops, ImageDraw, ImageFilter, ImageOps

SIZE = 64        # final image size in pixels
S = SIZE * 8     # drawn large, then scaled down for smooth edges
U = S / 108.0    # shapes use a 0..100 grid, plus a margin for the outline

BODY, FIN, BELLY = (86, 190, 245), (52, 152, 222), (228, 246, 255)
LINE, BLUSH, STAR = (24, 84, 140), (255, 158, 190), (255, 214, 92)

BODY_PTS = [(8, 53), (17, 45), (24, 37), (34, 25), (50, 18), (66, 21), (79, 32), (87, 48),
            (90, 64), (82, 66), (76, 54), (64, 47), (50, 46), (38, 52), (28, 60), (17, 60)]
DORSAL_PTS = [(46, 21), (58, 6), (65, 6), (64, 14), (67, 24)]
FLUKE_PTS = [(88, 62), (99, 60), (98, 72), (90, 71), (87, 84), (76, 79), (83, 68)]
FLIPPER_PTS = [(37, 49), (45, 52), (50, 65), (42, 62), (35, 54)]
MOUTH_PTS = [(10, 54.5), (16, 56), (22, 54.5), (25.5, 51.5)]
EYE = (26, 42.5)
STARS = [(84, 14, 8), (94, 30, 4.5), (10, 24, 4)]  # x, y, radius


def spline(points, closed=True, steps=20):
    """Catmull-Rom curve through the points, in pixels."""
    n, out = len(points), []
    for i in range(n if closed else n - 1):
        if closed:
            p0, p1, p2, p3 = (points[(i + k) % n] for k in (-1, 0, 1, 2))
        else:
            p0, p1 = points[max(i - 1, 0)], points[i]
            p2, p3 = points[i + 1], points[min(i + 2, n - 1)]
        for s in range(steps):
            t = s / steps
            out.append(tuple(
                0.5 * (2 * p1[k] + (p2[k] - p0[k]) * t
                       + (2 * p0[k] - 5 * p1[k] + 4 * p2[k] - p3[k]) * t * t
                       + (3 * p1[k] - p0[k] - 3 * p2[k] + p3[k]) * t ** 3) * U
                for k in (0, 1)))
    if not closed:
        out.append((points[-1][0] * U, points[-1][1] * U))
    return out


def shape(points):
    mask = Image.new("L", (S, S), 0)
    ImageDraw.Draw(mask).polygon(spline(points), fill=255)
    return mask


def box(cx, cy, rx, ry=None):
    ry = rx if ry is None else ry
    return [(cx - rx) * U, (cy - ry) * U, (cx + rx) * U, (cy + ry) * U]


def star_points(cx, cy, r):
    pts = []
    for i in range(8):
        a, rr = math.pi * i / 4, r if i % 2 == 0 else r * 0.32
        pts.append(((cx + rr * math.sin(a)) * U, (cy - rr * math.cos(a)) * U))
    return pts


def draw_large(eye_open=True):
    """The pet at eight times its final size."""
    body, dorsal = shape(BODY_PTS), shape(DORSAL_PTS)
    flukes, flipper = shape(FLUKE_PTS), shape(FLIPPER_PTS)
    whole = body
    for part in (dorsal, flukes, flipper):
        whole = ImageChops.lighter(whole, part)
    stars = Image.new("L", (S, S), 0)
    for cx, cy, r in STARS:
        ImageDraw.Draw(stars).polygon(star_points(cx, cy, r), fill=255)

    img = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    # A dark outline keeps the dolphin and its sparkles readable on any wallpaper.
    img.paste(LINE + (255,), mask=whole.filter(ImageFilter.MaxFilter(21)))
    img.paste(LINE + (255,), mask=stars.filter(ImageFilter.MaxFilter(15)))
    img.paste(FIN + (255,), mask=ImageChops.lighter(dorsal, flukes))
    img.paste(BODY + (255,), mask=body)
    # Belly: the strip of the body left uncovered when the body is shifted up.
    shifted = ImageChops.offset(body, int(-1 * U), int(-8 * U))
    img.paste(BELLY + (255,), mask=ImageChops.multiply(body, ImageOps.invert(shifted)))
    img.paste(LINE + (255,), mask=flipper.filter(ImageFilter.MaxFilter(13)))
    img.paste(FIN + (255,), mask=flipper)
    img.paste(STAR + (255,), mask=stars)

    d = ImageDraw.Draw(img)
    d.ellipse(box(31, 50, 3.6, 2.2), fill=BLUSH)
    d.line(spline(MOUTH_PTS, closed=False), fill=LINE, width=int(1.3 * U), joint="curve")
    ex, ey = EYE
    if eye_open:
        d.ellipse(box(ex, ey, 3.6, 4.0), fill=(18, 40, 70))
        d.ellipse(box(ex - 1.1, ey - 1.5, 1.4), fill="white")
        d.ellipse(box(ex + 1.2, ey + 1.6, 0.7), fill="white")
    else:
        d.arc(box(ex, ey + 1.5, 3.6, 3.2), 190, 350, fill=(18, 40, 70), width=int(1.5 * U))
    return img


def smooth(large):
    """Soft edges, for systems that blend the pet with what is behind it."""
    return large.convert("RGBa").resize((SIZE, SIZE), Image.LANCZOS).convert("RGBA")


def crisp(large):
    """No half-transparent pixels. Windows shows the pet through a colour key, and a
    soft edge would be mixed with that key colour into a dark fringe."""
    solid = Image.new("RGBA", large.size, LINE + (255,))
    solid.alpha_composite(large)
    out = solid.convert("RGB").resize((SIZE, SIZE), Image.LANCZOS).convert("RGBA")
    alpha = large.getchannel("A").resize((SIZE, SIZE), Image.LANCZOS)
    out.putalpha(alpha.point(lambda a: 255 if a >= 128 else 0))
    return out


def grey(img):
    r, g, b, a = img.split()
    lum = ImageOps.grayscale(Image.merge("RGB", (r, g, b)))
    return Image.merge("RGBA", (lum, lum, lum, a))


def encode(img):
    buf = io.BytesIO()
    img.save(buf, "PNG", optimize=True)
    text = base64.b64encode(buf.getvalue()).decode()
    return "\n".join('    "%s"' % text[i:i + 88] for i in range(0, len(text), 88))


def main():
    awake, asleep = draw_large(True), draw_large(False)
    lines = ['"""Dolphin pet images (PNG, base64). Generated by tools/draw_pet.py.', "",
             "The CRISP_ set has hard edges for Windows; see tools/draw_pet.py.", '"""', "",
             "SIZE = %d" % SIZE]
    for prefix, finish in (("", smooth), ("CRISP_", crisp)):
        art = {"OPEN": finish(awake), "BLINK": finish(asleep), "PAUSED": grey(finish(asleep))}
        for name, img in art.items():
            lines += ["", "%s%s = (" % (prefix, name), encode(img), ")"]
    out = Path(__file__).resolve().parent.parent / "smartram" / "pet_art.py"
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("wrote %s" % out)


if __name__ == "__main__":
    main()
