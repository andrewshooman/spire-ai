"""Draws the Workshop preview image (workshop/image.jpg) and the in-game badge.

    python mod/spireseer/art/make_preview.py      (needs Pillow)

Original art: a seer's eye over a spire, three cards numbered in play order (the first glowing green, as in
Suggest mode), and the targeting reticle around an enemy silhouette.
"""

import math
import random
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont

HERE = Path(__file__).resolve().parent
OUT = HERE.parent / "workshop" / "image.jpg"
BADGE = HERE.parent / "resources" / "spireseerResources" / "badge.png"
W = H = 1024
GOLD = (226, 182, 83)
GOLD_DARK = (150, 110, 40)
GREEN = (110, 245, 140)
CREAM = (235, 226, 204)
FONTS = Path("C:/Windows/Fonts")


def font(name, size):
    for n in (name, "georgiab.ttf", "georgia.ttf"):
        try:
            return ImageFont.truetype(str(FONTS / n), size)
        except OSError:
            continue
    return ImageFont.load_default()


def background():
    img = Image.new("RGB", (W, H))
    px = img.load()
    for y in range(H):
        for x in range(W):
            d = math.hypot(x - W / 2, y - H * 0.38) / (W * 0.75)
            v = max(0.0, 1 - d)
            px[x, y] = (int(14 + 38 * v), int(12 + 30 * v), int(20 + 26 * v))
    d = ImageDraw.Draw(img)
    rnd = random.Random(7)
    for _ in range(140):   # embers
        x, y, r = rnd.randrange(W), rnd.randrange(H), rnd.choice((1, 1, 2, 2, 3))
        c = rnd.choice([(226, 182, 83), (210, 120, 60), (240, 210, 140)])
        d.ellipse((x - r, y - r, x + r, y + r), fill=c)
    return img


def spire(d):
    # a dark tapering spire behind everything, with a few lit windows
    base_y, top_y, cx = 760, 150, W // 2
    d.polygon([(cx - 150, base_y), (cx - 40, top_y + 120), (cx, top_y), (cx + 40, top_y + 120), (cx + 150, base_y)],
              fill=(28, 22, 30), outline=(70, 58, 52))
    for i, (wx, wy) in enumerate([(cx - 18, 330), (cx + 10, 420), (cx - 30, 510), (cx + 26, 580)]):
        d.rectangle((wx, wy, wx + 12, wy + 22), fill=(200, 140, 60) if i % 2 == 0 else (120, 80, 40))


def eye(img):
    # the seer's eye, glowing gold, above the spire
    glow = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    g = ImageDraw.Draw(glow)
    cx, cy = W // 2, 140
    g.ellipse((cx - 150, cy - 150, cx + 150, cy + 150), fill=(226, 182, 83, 70))
    glow = glow.filter(ImageFilter.GaussianBlur(40))
    img.paste(glow, (0, 0), glow)
    d = ImageDraw.Draw(img)
    pts = []
    for t in range(0, 361, 6):
        a = math.radians(t)
        pts.append((cx + 120 * math.cos(a), cy + 52 * math.sin(a) * (1 if t <= 180 else 1)))
    d.polygon(pts, fill=(245, 230, 190), outline=GOLD)
    d.ellipse((cx - 46, cy - 46, cx + 46, cy + 46), fill=GOLD, outline=GOLD_DARK, width=4)
    d.ellipse((cx - 20, cy - 20, cx + 20, cy + 20), fill=(30, 20, 14))
    d.ellipse((cx - 12, cy - 16, cx - 2, cy - 6), fill=(255, 250, 230))
    for k in range(12):   # rays
        a = math.radians(k * 30)
        d.line((cx + 140 * math.cos(a), cy + 70 * math.sin(a), cx + 175 * math.cos(a), cy + 92 * math.sin(a)),
               fill=GOLD, width=4)


def card(img, cx, cy, angle, num, first, title, cost, kind):
    cw, ch = 210, 290
    layer = Image.new("RGBA", (cw + 80, ch + 80), (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)
    if first:   # the green glow Suggest mode puts on the next card
        glow = Image.new("RGBA", layer.size, (0, 0, 0, 0))
        ImageDraw.Draw(glow).rounded_rectangle((18, 18, cw + 62, ch + 62), 28, fill=(110, 245, 140, 200))
        layer = Image.alpha_composite(layer, glow.filter(ImageFilter.GaussianBlur(14)))
        d = ImageDraw.Draw(layer)
    body = {"attack": (120, 46, 38), "skill": (44, 84, 112)}[kind]
    d.rounded_rectangle((40, 40, cw + 40, ch + 40), 20, fill=(38, 32, 28), outline=GOLD if not first else GREEN, width=5)
    d.rounded_rectangle((54, 96, cw + 26, 200), 10, fill=body)
    d.rounded_rectangle((54, 56, cw + 26, 92), 8, fill=(60, 50, 40))
    f = font("georgiab.ttf", 24)
    d.text((40 + cw / 2, 74), title, font=f, fill=CREAM, anchor="mm")
    d.ellipse((24, 24, 74, 74), fill=(190, 60, 40), outline=(255, 210, 160), width=3)   # cost orb
    d.text((49, 49), str(cost), font=font("georgiab.ttf", 30), fill=(255, 245, 230), anchor="mm")
    for i in range(3):   # text lines
        d.rounded_rectangle((70, 220 + i * 26, cw + 10 - i * 30, 232 + i * 26), 5, fill=(90, 80, 66))
    layer = layer.rotate(angle, resample=Image.BICUBIC, expand=True)
    img.paste(layer, (int(cx - layer.width / 2), int(cy - layer.height / 2)), layer)
    # play-order number above the card
    d2 = ImageDraw.Draw(img)
    nx, ny = cx + math.sin(math.radians(-angle)) * 210, cy - math.cos(math.radians(-angle)) * 210
    d2.ellipse((nx - 38, ny - 38, nx + 38, ny + 38), fill=(20, 16, 14), outline=GREEN if first else GOLD, width=5)
    d2.text((nx, ny + 2), str(num), font=font("georgiab.ttf", 46), fill=GREEN if first else GOLD, anchor="mm")


def enemy(d, cx, cy):
    # a horned silhouette with the targeting reticle around it
    d.ellipse((cx - 70, cy - 40, cx + 70, cy + 110), fill=(18, 14, 18))
    d.ellipse((cx - 48, cy - 95, cx + 48, cy + 5), fill=(18, 14, 18))
    d.polygon([(cx - 40, cy - 75), (cx - 85, cy - 140), (cx - 20, cy - 90)], fill=(18, 14, 18))
    d.polygon([(cx + 40, cy - 75), (cx + 85, cy - 140), (cx + 20, cy - 90)], fill=(18, 14, 18))
    d.ellipse((cx - 26, cy - 52, cx - 10, cy - 38), fill=(230, 70, 50))
    d.ellipse((cx + 10, cy - 52, cx + 26, cy - 38), fill=(230, 70, 50))
    x0, y0, x1, y1, L = cx - 115, cy - 160, cx + 115, cy + 135, 44
    for (x, y, sx, sy) in ((x0, y0, 1, 1), (x1, y0, -1, 1), (x0, y1, 1, -1), (x1, y1, -1, -1)):
        d.line((x, y, x + sx * L, y), fill=CREAM, width=7)
        d.line((x, y, x, y + sy * L), fill=CREAM, width=7)
    # intent: sword with damage
    d.text((cx, cy - 190), "12", font=font("georgiab.ttf", 40), fill=(255, 120, 100), anchor="mm")


def main():
    img = background()
    d = ImageDraw.Draw(img)
    spire(d)
    eye(img)
    d = ImageDraw.Draw(img)
    enemy(d, 790, 470)
    card(img, 300, 640, 10, 1, True, "Bash", 2, "attack")
    card(img, 470, 690, 0, 2, False, "Strike", 1, "attack")
    card(img, 640, 640, -10, 3, False, "Defend", 1, "skill")
    # title plate
    plate = Image.new("RGBA", (W, 230), (0, 0, 0, 0))
    ImageDraw.Draw(plate).rectangle((0, 0, W, 230), fill=(10, 8, 10, 215))
    img.paste(plate, (0, H - 230), plate)
    d = ImageDraw.Draw(img)
    d.line((60, H - 228, W - 60, H - 228), fill=GOLD_DARK, width=3)
    d.text((W / 2 + 3, H - 150 + 3), "SPIRESEER", font=font("georgiab.ttf", 118), fill=(0, 0, 0), anchor="mm")
    d.text((W / 2, H - 150), "SPIRESEER", font=font("georgiab.ttf", 118), fill=GOLD, anchor="mm")
    d.text((W / 2, H - 62), "See the best turn.  Or let it play.", font=font("georgiai.ttf", 44), fill=CREAM, anchor="mm")
    OUT.parent.mkdir(parents=True, exist_ok=True)
    img.save(OUT, "JPEG", quality=90)
    # badge for the in-game mod list: the eye on dark stone
    b = Image.new("RGBA", (40, 40), (0, 0, 0, 0))
    bd = ImageDraw.Draw(b)
    bd.ellipse((0, 0, 39, 39), fill=(30, 24, 30), outline=GOLD, width=2)
    bd.ellipse((6, 13, 33, 27), fill=(245, 230, 190))
    bd.ellipse((14, 13, 26, 26), fill=GOLD)
    bd.ellipse((17, 16, 23, 23), fill=(30, 20, 14))
    b.save(BADGE)
    print(f"wrote {OUT} and {BADGE}")


if __name__ == "__main__":
    main()
