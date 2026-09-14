"""Draw the application icon from scratch and write app_icon.ico (plus docs previews).

Run with a Python that has Pillow installed (build-time only; the app never needs Pillow).
The sidebar logo and all other interface bitmaps come from tools/make_assets.py:

    python tools/make_icon.py

The design: a rounded blue tile (matching the interface accent) holding a 2x2 grid
of "tidied" cards, with one accent card and a small sparkle for "clean". Small sizes
use a simplified variant so the 16 px taskbar icon stays crisp.
"""
import math
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter

ROOT = Path(__file__).resolve().parent.parent
SIZES = (256, 128, 64, 48, 40, 32, 24, 20, 16)
SUPERSAMPLE = 4
TOP, BOTTOM = (94, 129, 255), (55, 88, 222)      # gradient endpoints
ACCENT = (255, 209, 102)                          # amber card
WHITE = (255, 255, 255)


def lerp(a, b, t):
    return tuple(int(round(a[i] + (b[i] - a[i]) * t)) for i in range(3))


def gradient(size):
    image = Image.new("RGBA", (size, size))
    draw = ImageDraw.Draw(image)
    for y in range(size):
        draw.line([(0, y), (size, y)], fill=lerp(TOP, BOTTOM, y / max(size - 1, 1)) + (255,))
    return image


def rounded_mask(size, box, radius):
    mask = Image.new("L", (size, size), 0)
    ImageDraw.Draw(mask).rounded_rectangle(box, radius=radius, fill=255)
    return mask


def sparkle(draw, cx, cy, r, color):
    """Four-point star with a soft waist."""
    waist = r * 0.28
    points = []
    for i in range(8):
        angle_r = r if i % 2 == 0 else waist
        a = math.pi / 4 * i - math.pi / 2
        points.append((cx + math.cos(a) * angle_r, cy + math.sin(a) * angle_r))
    draw.polygon(points, fill=color)


def render(size):
    small = size <= 24
    detailed = size >= 48  # text lines only where they can be resolved
    s = size * SUPERSAMPLE
    unit = s / 64.0  # design on a 64-unit grid

    # Background tile with gradient and a faint top-left sheen.
    radius = unit * (14 if not small else 12)
    background = gradient(s)
    sheen = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    ImageDraw.Draw(sheen).polygon([(0, 0), (s, 0), (0, s)], fill=WHITE + (22,))
    background = Image.alpha_composite(background, sheen)
    tile = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    tile.paste(background, (0, 0), rounded_mask(s, (0, 0, s - 1, s - 1), radius))

    # Card grid geometry.
    if small:
        margin, gap, card_radius = unit * 11, unit * 5, unit * 3.5
    else:
        margin, gap, card_radius = unit * 15, unit * 5, unit * 4
    card = (s - margin * 2 - gap) / 2
    cards = []
    for row in range(2):
        for col in range(2):
            x0 = margin + col * (card + gap)
            y0 = margin + row * (card + gap)
            cards.append((x0, y0, x0 + card, y0 + card))

    # Soft shadow under the cards (large sizes only; it would only muddy 16 px).
    if not small:
        shadow = Image.new("RGBA", (s, s), (0, 0, 0, 0))
        shadow_draw = ImageDraw.Draw(shadow)
        for box in cards:
            shadow_draw.rounded_rectangle([box[0], box[1] + unit * 1.5, box[2], box[3] + unit * 1.5], radius=card_radius, fill=(20, 40, 120, 70))
        shadow = shadow.filter(ImageFilter.GaussianBlur(unit * 1.6))
        tile = Image.alpha_composite(tile, shadow)

    draw = ImageDraw.Draw(tile)
    for index, box in enumerate(cards):
        color = ACCENT if index == 0 else WHITE
        draw.rounded_rectangle(box, radius=card_radius, fill=color + (255,))
        if detailed:
            # Two short "text" lines on the white cards, one bold line on the accent card.
            inset = card * 0.22
            line_h = card * 0.11
            line_color = (196, 140, 30, 255) if index == 0 else BOTTOM + (70,)
            x0, y0 = box[0] + inset, box[1] + inset
            width = card - inset * 2
            draw.rounded_rectangle([x0, y0, x0 + width, y0 + line_h], radius=line_h / 2, fill=line_color)
            draw.rounded_rectangle([x0, y0 + line_h * 2.1, x0 + width * 0.62, y0 + line_h * 3.1], radius=line_h / 2, fill=line_color)

    # Sparkle at the top-right corner outside the grid on larger sizes.
    if not small:
        sparkle(draw, s - unit * 12.5, unit * 12.5, unit * 5.2, WHITE + (255,))
        sparkle(draw, s - unit * 6.5, unit * 20.5, unit * 2.2, WHITE + (230,))
    return tile.resize((size, size), Image.LANCZOS)


def main():
    images = {size: render(size) for size in SIZES}
    largest = images[SIZES[0]]
    ico = ROOT / "app_icon.ico"
    largest.save(ico, format="ICO", sizes=[(size, size) for size in SIZES], append_images=[images[size] for size in SIZES[1:]])
    docs = ROOT / "docs"
    docs.mkdir(exist_ok=True)
    largest.save(docs / "icon.png")
    # A contact sheet for the docs so every size can be eyeballed.
    sheet_w = sum(size for size in SIZES) + 16 * (len(SIZES) + 1)
    sheet = Image.new("RGBA", (sheet_w, 256 + 32), (243, 245, 250, 255))
    x = 16
    for size in SIZES:
        sheet.paste(images[size], (x, 16 + (256 - size) // 2), images[size])
        x += size + 16
    sheet.save(docs / "icon_sizes.png")
    print("wrote", ico, "with sizes", SIZES)


if __name__ == "__main__":
    main()
