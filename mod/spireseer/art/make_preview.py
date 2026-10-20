"""Draws the Workshop preview image (workshop/image.jpg) and the in-game badge. Icons only, no text.

    python mod/spireseer/art/make_preview.py      (needs Pillow)

A seer's eye over a spire, three cards whose play order is shown with pips (the first glowing green, as in
Suggest mode), and the targeting reticle around an enemy with a sword intent.
"""

import math
import random
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter

HERE = Path(__file__).resolve().parent
OUT = HERE.parent / "workshop" / "image.jpg"
BADGE = HERE.parent / "resources" / "spireseerResources" / "badge.png"
W = H = 1024          # drawn large, saved at 640x640
GOLD = (226, 182, 83)
GOLD_DARK = (150, 110, 40)
GREEN = (110, 245, 140)
CREAM = (235, 226, 204)


def background():
    img = Image.new("RGB", (W, H))
    px = img.load()
    for y in range(H):
        for x in range(W):
            d = math.hypot(x - W / 2, y - H * 0.34) / (W * 0.75)
            v = max(0.0, 1 - d)
            px[x, y] = (int(14 + 40 * v), int(12 + 31 * v), int(20 + 27 * v))
    d = ImageDraw.Draw(img)
    rnd = random.Random(7)
    for _ in range(150):   # embers
        x, y, r = rnd.randrange(W), rnd.randrange(H), rnd.choice((1, 1, 2, 2, 3))
        d.ellipse((x - r, y - r, x + r, y + r), fill=rnd.choice([(226, 182, 83), (210, 120, 60), (240, 210, 140)]))
    return img


def spire(d):
    base_y, top_y, cx = 900, 190, W // 2
    d.polygon([(cx - 170, base_y), (cx - 45, top_y + 130), (cx, top_y), (cx + 45, top_y + 130), (cx + 170, base_y)],
              fill=(28, 22, 30), outline=(70, 58, 52))
    for i, (wx, wy) in enumerate([(cx - 18, 380), (cx + 10, 470), (cx - 30, 560), (cx + 26, 650)]):
        d.rectangle((wx, wy, wx + 12, wy + 22), fill=(200, 140, 60) if i % 2 == 0 else (120, 80, 40))


def eye(img):
    glow = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    cx, cy = W // 2, 175
    ImageDraw.Draw(glow).ellipse((cx - 170, cy - 170, cx + 170, cy + 170), fill=(226, 182, 83, 80))
    glow = glow.filter(ImageFilter.GaussianBlur(45))
    img.paste(glow, (0, 0), glow)
    d = ImageDraw.Draw(img)
    pts = [(cx + 135 * math.cos(math.radians(t)), cy + 60 * math.sin(math.radians(t))) for t in range(0, 361, 6)]
    d.polygon(pts, fill=(245, 230, 190), outline=GOLD)
    d.ellipse((cx - 52, cy - 52, cx + 52, cy + 52), fill=GOLD, outline=GOLD_DARK, width=4)
    d.ellipse((cx - 23, cy - 23, cx + 23, cy + 23), fill=(30, 20, 14))
    d.ellipse((cx - 14, cy - 18, cx - 2, cy - 6), fill=(255, 250, 230))
    for k in range(12):
        a = math.radians(k * 30)
        d.line((cx + 158 * math.cos(a), cy + 78 * math.sin(a), cx + 196 * math.cos(a), cy + 102 * math.sin(a)),
               fill=GOLD, width=5)


def pips(d, cx, cy, n, col):
    """Play order as dots in a ring: one, two or three."""
    d.ellipse((cx - 44, cy - 44, cx + 44, cy + 44), fill=(20, 16, 14), outline=col, width=6)
    offs = {1: [(0, 0)], 2: [(-15, 0), (15, 0)], 3: [(-18, 10), (18, 10), (0, -16)]}[n]
    for ox, oy in offs:
        d.ellipse((cx + ox - 9, cy + oy - 9, cx + ox + 9, cy + oy + 9), fill=col)


def card(img, cx, cy, angle, order, first, kind):
    cw, ch = 220, 300
    layer = Image.new("RGBA", (cw + 80, ch + 80), (0, 0, 0, 0))
    if first:   # the green glow Suggest mode puts on the next card
        glow = Image.new("RGBA", layer.size, (0, 0, 0, 0))
        ImageDraw.Draw(glow).rounded_rectangle((18, 18, cw + 62, ch + 62), 28, fill=(110, 245, 140, 210))
        layer = Image.alpha_composite(layer, glow.filter(ImageFilter.GaussianBlur(15)))
    d = ImageDraw.Draw(layer)
    body = {"attack": (122, 46, 38), "skill": (44, 84, 112)}[kind]
    d.rounded_rectangle((40, 40, cw + 40, ch + 40), 20, fill=(38, 32, 28), outline=GREEN if first else GOLD, width=5)
    d.rounded_rectangle((56, 58, cw + 24, 92), 8, fill=(62, 52, 42))           # title bar, no text
    d.rounded_rectangle((56, 100, cw + 24, 210), 10, fill=body)                 # art
    cxa, cya = 40 + cw / 2, 155
    if kind == "attack":   # crossed blades icon
        d.line((cxa - 34, cya + 34, cxa + 34, cya - 34), fill=CREAM, width=9)
        d.line((cxa - 34, cya - 34, cxa + 34, cya + 34), fill=CREAM, width=9)
    else:                  # shield icon
        d.polygon([(cxa - 32, cya - 34), (cxa + 32, cya - 34), (cxa + 28, cya + 10), (cxa, cya + 38), (cxa - 28, cya + 10)],
                  fill=CREAM)
    for i in range(3):     # text lines as bars
        d.rounded_rectangle((72, 228 + i * 26, cw + 8 - i * 30, 240 + i * 26), 5, fill=(90, 80, 66))
    d.ellipse((22, 22, 74, 74), fill=(190, 60, 40), outline=(255, 210, 160), width=4)   # energy orb, no number
    d.ellipse((38, 38, 58, 58), fill=(255, 190, 120))
    layer = layer.rotate(angle, resample=Image.BICUBIC, expand=True)
    img.paste(layer, (int(cx - layer.width / 2), int(cy - layer.height / 2)), layer)
    d2 = ImageDraw.Draw(img)
    nx, ny = cx + math.sin(math.radians(-angle)) * 215, cy - math.cos(math.radians(-angle)) * 215
    pips(d2, nx, ny, order, GREEN if first else GOLD)


def enemy(d, cx, cy):
    d.ellipse((cx - 75, cy - 40, cx + 75, cy + 115), fill=(18, 14, 18))
    d.ellipse((cx - 50, cy - 98, cx + 50, cy + 5), fill=(18, 14, 18))
    d.polygon([(cx - 40, cy - 78), (cx - 88, cy - 145), (cx - 20, cy - 92)], fill=(18, 14, 18))
    d.polygon([(cx + 40, cy - 78), (cx + 88, cy - 145), (cx + 20, cy - 92)], fill=(18, 14, 18))
    d.ellipse((cx - 27, cy - 55, cx - 10, cy - 40), fill=(230, 70, 50))
    d.ellipse((cx + 10, cy - 55, cx + 27, cy - 40), fill=(230, 70, 50))
    x0, y0, x1, y1, L = cx - 120, cy - 165, cx + 120, cy + 140, 46     # targeting reticle
    for (x, y, sx, sy) in ((x0, y0, 1, 1), (x1, y0, -1, 1), (x0, y1, 1, -1), (x1, y1, -1, -1)):
        d.line((x, y, x + sx * L, y), fill=CREAM, width=8)
        d.line((x, y, x, y + sy * L), fill=CREAM, width=8)
    sx, sy = cx, cy - 215                                             # attack intent: a sword
    d.polygon([(sx - 7, sy + 30), (sx + 7, sy + 30), (sx + 7, sy - 38), (sx, sy - 50), (sx - 7, sy - 38)], fill=(255, 120, 100))
    d.rectangle((sx - 24, sy + 28, sx + 24, sy + 38), fill=(255, 120, 100))
    d.rectangle((sx - 5, sy + 38, sx + 5, sy + 58), fill=(200, 90, 70))


def main():
    img = background()
    d = ImageDraw.Draw(img)
    spire(d)
    eye(img)
    d = ImageDraw.Draw(img)
    enemy(d, 800, 560)
    card(img, 270, 760, 10, 1, True, "attack")
    card(img, 470, 810, 0, 2, False, "attack")
    card(img, 670, 760, -10, 3, False, "skill")
    out = img.resize((640, 640), Image.LANCZOS)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    out.save(OUT, "JPEG", quality=88, optimize=True)
    # badge for the in-game mod list: the eye on dark stone
    b = Image.new("RGBA", (40, 40), (0, 0, 0, 0))
    bd = ImageDraw.Draw(b)
    bd.ellipse((0, 0, 39, 39), fill=(30, 24, 30), outline=GOLD, width=2)
    bd.ellipse((6, 13, 33, 27), fill=(245, 230, 190))
    bd.ellipse((14, 13, 26, 26), fill=GOLD)
    bd.ellipse((17, 16, 23, 23), fill=(30, 20, 14))
    b.save(BADGE)
    print(f"wrote {OUT} ({OUT.stat().st_size // 1024} KB) and {BADGE}")


if __name__ == "__main__":
    main()
