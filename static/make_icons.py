"""Draws the app icons (home-screen icon for phones). Run from this folder: python make_icons.py
Needs Pillow (already installed with matplotlib). The generated PNGs are committed, so this only
needs re-running if you want to change the artwork."""
from PIL import Image, ImageDraw

SIZE = 1024


def draw():
    img = Image.new("RGB", (SIZE, SIZE))
    px = img.load()
    for y in range(SIZE):                              # diagonal blue -> cyan gradient, like the logo
        for x in range(SIZE):
            t = (x + y) / (2 * SIZE)
            px[x, y] = (int(79 + (0 - 79) * t), int(142 + (212 - 142) * t), int(247 + (255 - 247) * t))
    d = ImageDraw.Draw(img, "RGBA")
    # Everything stays inside the central 60% so it survives the rounded or circular masks phones apply
    left, right, base = 270, 754, 700
    bars = [(0, 180), (1, 290), (2, 400)]
    w, gap = 120, 52
    for i, h in bars:
        x0 = left + i * (w + gap)
        d.rounded_rectangle([x0, base - h, x0 + w, base], radius=26, fill=(255, 255, 255, 235))
    pts = [(left + 40, 560), (left + 220, 450), (left + 330, 500), (right - 30, 300)]   # rising trend line
    d.line(pts, fill=(5, 5, 8, 255), width=44, joint="curve")
    for p in pts:
        d.ellipse([p[0] - 30, p[1] - 30, p[0] + 30, p[1] + 30], fill=(5, 5, 8, 255))
    tip = pts[-1]
    d.polygon([(tip[0] + 58, tip[1] - 58), (tip[0] - 70, tip[1] - 38), (tip[0] + 38, tip[1] + 70)], fill=(5, 5, 8, 255))
    return img


if __name__ == "__main__":
    art = draw()
    for name, size in (("icon-512.png", 512), ("icon-192.png", 192), ("apple-touch-icon.png", 180)):
        art.resize((size, size), Image.LANCZOS).save(name, optimize=True)
        print("wrote", name, size)
