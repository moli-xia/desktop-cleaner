"""Draw every interface bitmap (category icons, checkboxes, navigation glyphs, rounded
button/field nine-slices, sidebar logo) into assets/<scale>/ for 100 %, 150 % and 200 % DPI.

Build-time only; needs Pillow:

    python tools/make_assets.py

Tk 8.6 cannot render colour emoji or scale bitmaps smoothly, so each asset is drawn with
4x supersampling at every scale instead of being resized at runtime.
"""
import math
from pathlib import Path

from PIL import Image, ImageDraw

import make_icon

ROOT = Path(__file__).resolve().parent.parent
ASSETS = ROOT / "assets"
S = 4  # supersampling factor
SCALES = {"1x": 1.0, "1.5x": 1.5, "2x": 2.0}
WHITE = (255, 255, 255, 255)
BLUE = (66, 99, 235)

# Category icons: key -> (background colour, emoji stored in config, label for the picker).
CATEGORY_ICONS = {
    "doc": ((76, 125, 240), "📄", "文档"),
    "image": ((242, 95, 143), "🖼️", "图片"),
    "video": ((142, 92, 246), "🎬", "视频"),
    "audio": ((245, 158, 58), "🎵", "音频"),
    "archive": ((201, 146, 62), "📦", "压缩包"),
    "program": ((52, 179, 126), "💻", "程序"),
    "folder": ((244, 178, 62), "📂", "文件夹"),
    "other": ((124, 138, 165), "📁", "其他"),
    "code": ((34, 166, 179), "🧩", "代码"),
    "sheet": ((46, 158, 107), "📊", "表格"),
    "design": ((214, 79, 207), "🎨", "设计"),
    "download": ((47, 168, 224), "📥", "下载"),
    "book": ((224, 85, 63), "📚", "书籍"),
    "star": ((242, 192, 55), "⭐", "收藏"),
}


def rounded(d, box, radius, **kwargs):
    d.rounded_rectangle(box, radius=radius, **kwargs)


def polyline(d, points, width, fill=255):
    d.line(points, fill=fill, width=int(width), joint="curve")
    for x, y in points:  # round caps
        d.ellipse([x - width / 2, y - width / 2, x + width / 2, y + width / 2], fill=fill)


def star_points(cx, cy, outer, inner, count=5, rotation=-math.pi / 2):
    points = []
    for i in range(count * 2):
        r = outer if i % 2 == 0 else inner
        a = rotation + math.pi / count * i
        points.append((cx + math.cos(a) * r, cy + math.sin(a) * r))
    return points


# --- category glyphs -------------------------------------------------------------------
# Each glyph draws onto an "L" mask in a 20-unit coordinate system: 255 paints the glyph
# colour, 0 erases back to the tile colour (used for details such as text lines).

def g_doc(d, u):
    rounded(d, [6 * u, 3.8 * u, 14 * u, 16.2 * u], 1.3 * u, fill=255)
    for y, w in ((7.6, 4.2), (10.1, 4.2), (12.6, 2.8)):
        polyline(d, [(8 * u, y * u), ((8 + w) * u, y * u)], 1.05 * u, fill=0)


def g_image(d, u):
    rounded(d, [4.2 * u, 4.8 * u, 15.8 * u, 15.2 * u], 1.6 * u, fill=255)
    d.polygon([(5.8 * u, 13.6 * u), (9 * u, 9.4 * u), (11 * u, 11.9 * u), (12.4 * u, 10.6 * u), (14.2 * u, 13.6 * u)], fill=0)
    d.ellipse([11.6 * u, 6.3 * u, 13.8 * u, 8.5 * u], fill=0)


def g_video(d, u):
    d.polygon([(7 * u, 5.4 * u), (15.2 * u, 10 * u), (7 * u, 14.6 * u)], fill=255)


def g_audio(d, u):
    d.ellipse([4.2 * u, 12 * u, 8.2 * u, 15.4 * u], fill=255)
    d.ellipse([10.6 * u, 11 * u, 14.6 * u, 14.4 * u], fill=255)
    polyline(d, [(7.6 * u, 13.4 * u), (7.6 * u, 6.2 * u)], 1.5 * u)
    polyline(d, [(14 * u, 12.4 * u), (14 * u, 5 * u)], 1.5 * u)
    d.polygon([(6.9 * u, 5.6 * u), (14.7 * u, 4.2 * u), (14.7 * u, 6.9 * u), (6.9 * u, 8.3 * u)], fill=255)


def g_archive(d, u):
    rounded(d, [4.2 * u, 4.6 * u, 15.8 * u, 7.8 * u], 0.9 * u, fill=255)
    rounded(d, [5 * u, 8.4 * u, 15 * u, 15.6 * u], 1 * u, fill=255)
    rounded(d, [8.3 * u, 9.6 * u, 11.7 * u, 11.3 * u], 0.6 * u, fill=0)


def g_program(d, u):
    rounded(d, [3.8 * u, 4.6 * u, 16.2 * u, 14.2 * u], 1.5 * u, fill=255)
    d.rectangle([3.8 * u, 7.6 * u, 16.2 * u, 8.4 * u], fill=0)
    d.ellipse([5.4 * u, 5.5 * u, 6.7 * u, 6.8 * u], fill=0)
    d.ellipse([7.4 * u, 5.5 * u, 8.7 * u, 6.8 * u], fill=0)
    d.rectangle([8.8 * u, 14.9 * u, 11.2 * u, 16.2 * u], fill=255)
    d.rectangle([6.6 * u, 15.6 * u, 13.4 * u, 16.6 * u], fill=255)


def g_folder(d, u):
    rounded(d, [3.8 * u, 5.4 * u, 9.6 * u, 8.8 * u], 1 * u, fill=255)
    rounded(d, [3.8 * u, 7.2 * u, 16.2 * u, 15.4 * u], 1.3 * u, fill=255)
    d.rectangle([3.8 * u, 8.8 * u, 16.2 * u, 9.6 * u], fill=0)


def g_other(d, u):
    for cx in (5.8, 10, 14.2):
        d.ellipse([(cx - 1.55) * u, 8.45 * u, (cx + 1.55) * u, 11.55 * u], fill=255)


def g_code(d, u):
    polyline(d, [(7.6 * u, 6.4 * u), (4 * u, 10 * u), (7.6 * u, 13.6 * u)], 1.7 * u)
    polyline(d, [(12.4 * u, 6.4 * u), (16 * u, 10 * u), (12.4 * u, 13.6 * u)], 1.7 * u)
    polyline(d, [(11.3 * u, 5.4 * u), (8.7 * u, 14.6 * u)], 1.5 * u)


def g_sheet(d, u):
    rounded(d, [4 * u, 4.6 * u, 16 * u, 15.4 * u], 1.4 * u, fill=255)
    for y in (8.2, 11.8):
        d.rectangle([4 * u, (y - 0.45) * u, 16 * u, (y + 0.45) * u], fill=0)
    for x in (8, 12):
        d.rectangle([(x - 0.45) * u, 4.6 * u, (x + 0.45) * u, 15.4 * u], fill=0)


def g_design(d, u):
    d.ellipse([3.6 * u, 3.6 * u, 16.4 * u, 16.4 * u], fill=255)
    d.ellipse([10.6 * u, 10.6 * u, 16.9 * u, 16.9 * u], fill=0)
    d.ellipse([12 * u, 11.8 * u, 14.6 * u, 14.4 * u], fill=255)
    for cx, cy in ((6.6, 8.2), (9.6, 6.1), (13, 7.7)):
        d.ellipse([(cx - 1.25) * u, (cy - 1.25) * u, (cx + 1.25) * u, (cy + 1.25) * u], fill=0)


def g_download(d, u):
    polyline(d, [(10 * u, 4.4 * u), (10 * u, 10.6 * u)], 2 * u)
    d.polygon([(6.2 * u, 9.6 * u), (13.8 * u, 9.6 * u), (10 * u, 13.8 * u)], fill=255)
    polyline(d, [(5 * u, 15.6 * u), (15 * u, 15.6 * u)], 1.8 * u)


def g_book(d, u):
    rounded(d, [5 * u, 4 * u, 15.4 * u, 16 * u], 1.2 * u, fill=255)
    d.rectangle([7.4 * u, 4 * u, 8.3 * u, 16 * u], fill=0)
    d.rectangle([10.2 * u, 7.2 * u, 13.2 * u, 8 * u], fill=0)


def g_star(d, u):
    d.polygon(star_points(10 * u, 10.4 * u, 6.6 * u, 3 * u), fill=255)


def g_skipped(d, u):
    polyline(d, [(6.6 * u, 10 * u), (13.4 * u, 10 * u)], 2 * u)


GLYPHS = {"doc": g_doc, "image": g_image, "video": g_video, "audio": g_audio, "archive": g_archive, "program": g_program, "folder": g_folder, "other": g_other, "code": g_code, "sheet": g_sheet, "design": g_design, "download": g_download, "book": g_book, "star": g_star}


def tile_icon(size, color, glyph, glyph_color=WHITE, radius=5.2):
    s = size * S
    u = s / 20.0
    background = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    rounded(ImageDraw.Draw(background), [0, 0, s - 1, s - 1], radius * u, fill=tuple(color) + (255,))
    mask = Image.new("L", (s, s), 0)
    glyph(ImageDraw.Draw(mask), u)
    layer = Image.new("RGBA", (s, s), glyph_color)
    layer.putalpha(mask)
    return Image.alpha_composite(background, layer).resize((size, size), Image.LANCZOS)


# --- statistic card icons ----------------------------------------------------------------

def g_check(d, u):
    polyline(d, [(5.2 * u, 10.4 * u), (8.6 * u, 13.8 * u), (14.8 * u, 6.6 * u)], 2.1 * u)


def g_pause(d, u):
    rounded(d, [6 * u, 5.4 * u, 8.6 * u, 14.6 * u], 0.8 * u, fill=255)
    rounded(d, [11.4 * u, 5.4 * u, 14 * u, 14.6 * u], 0.8 * u, fill=255)


def g_bars(d, u):
    rounded(d, [4.6 * u, 10.6 * u, 7.4 * u, 15.4 * u], 0.7 * u, fill=255)
    rounded(d, [8.6 * u, 5.6 * u, 11.4 * u, 15.4 * u], 0.7 * u, fill=255)
    rounded(d, [12.6 * u, 8.4 * u, 15.4 * u, 15.4 * u], 0.7 * u, fill=255)


STAT_ICONS = {"stat_ready": (BLUE, g_check), "stat_keep": ((124, 138, 165), g_pause), "stat_size": ((18, 133, 119), g_bars)}


# --- navigation glyphs (mask only, tinted at export) --------------------------------------

def n_home(d, u):
    for x, y in ((2.6, 2.6), (10.2, 2.6), (2.6, 10.2), (10.2, 10.2)):
        rounded(d, [x * u, y * u, (x + 5.2) * u, (y + 5.2) * u], 1.4 * u, fill=255)


def n_history(d, u):
    d.arc([3 * u, 3 * u, 15 * u, 15 * u], start=300, end=225, fill=255, width=int(1.9 * u))
    cx, cy = 9 * u + math.cos(math.radians(225)) * 6 * u, 9 * u + math.sin(math.radians(225)) * 6 * u
    d.polygon([(cx - 2.4 * u, cy - 0.4 * u), (cx + 0.9 * u, cy - 2.7 * u), (cx + 0.9 * u, cy + 1.7 * u)], fill=255)


def n_rules(d, u):
    for y in (4.4, 9, 13.6):
        d.ellipse([2.6 * u, (y - 1.3) * u, 5.2 * u, (y + 1.3) * u], fill=255)
        rounded(d, [7 * u, (y - 0.95) * u, 15.6 * u, (y + 0.95) * u], 0.9 * u, fill=255)


def n_settings(d, u):
    cx = cy = 9 * u
    d.ellipse([cx - 5.6 * u, cy - 5.6 * u, cx + 5.6 * u, cy + 5.6 * u], fill=255)
    for i in range(8):
        a = math.radians(i * 45)
        dx, dy = math.cos(a), math.sin(a)
        px_, py_ = -dy, dx
        length, half = 7.6 * u, 1.55 * u
        d.polygon([(cx + dx * 4 * u + px_ * half, cy + dy * 4 * u + py_ * half), (cx + dx * length + px_ * half, cy + dy * length + py_ * half), (cx + dx * length - px_ * half, cy + dy * length - py_ * half), (cx + dx * 4 * u - px_ * half, cy + dy * 4 * u - py_ * half)], fill=255)
    d.ellipse([cx - 2.3 * u, cy - 2.3 * u, cx + 2.3 * u, cy + 2.3 * u], fill=0)


def n_about(d, u):
    d.ellipse([2.4 * u, 2.4 * u, 15.6 * u, 15.6 * u], outline=255, width=int(1.8 * u))
    d.ellipse([7.9 * u, 5.2 * u, 10.1 * u, 7.4 * u], fill=255)
    rounded(d, [8 * u, 8.4 * u, 10 * u, 13.2 * u], 0.8 * u, fill=255)


NAV_ICONS = {"nav_home": n_home, "nav_history": n_history, "nav_rules": n_rules, "nav_settings": n_settings, "nav_about": n_about}
NAV_COLORS = {"": (187, 199, 220, 255), "_active": WHITE}


def mask_icon(size, glyph, color, units=18):
    s = size * S
    mask = Image.new("L", (s, s), 0)
    glyph(ImageDraw.Draw(mask), s / float(units))
    layer = Image.new("RGBA", (s, s), color)
    layer.putalpha(mask)
    return layer.resize((size, size), Image.LANCZOS)


# --- checkboxes -----------------------------------------------------------------------------

def checkbox(size, state):
    s = size * S
    u = s / 18.0
    image = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    d = ImageDraw.Draw(image)
    if state == "on":
        rounded(d, [0.5 * u, 0.5 * u, 17.5 * u, 17.5 * u], 4 * u, fill=BLUE + (255,))
        polyline(d, [(4.6 * u, 9.3 * u), (7.7 * u, 12.4 * u), (13.5 * u, 6 * u)], 2 * u, fill=WHITE)
    elif state == "off":
        rounded(d, [0.5 * u, 0.5 * u, 17.5 * u, 17.5 * u], 4 * u, fill=WHITE, outline=(176, 186, 204, 255), width=int(1.4 * u))
    else:
        rounded(d, [0.5 * u, 0.5 * u, 17.5 * u, 17.5 * u], 4 * u, fill=(238, 241, 246, 255), outline=(221, 226, 235, 255), width=int(1.2 * u))
        polyline(d, [(6 * u, 9 * u), (12 * u, 9 * u)], 1.8 * u, fill=(180, 188, 203, 255))
    return image.resize((size, size), Image.LANCZOS)


# --- nine-slice button / field backgrounds -----------------------------------------------------

def slice_image(size_w, size_h, radius, fill, outline=None, width=1.0):
    w, h = size_w * S, size_h * S
    image = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    d = ImageDraw.Draw(image)
    box = [0, 0, w - 1, h - 1]
    if outline:
        rounded(d, box, radius * S, fill=outline + (255,))
        inset = width * S
        rounded(d, [inset, inset, w - 1 - inset, h - 1 - inset], max(radius * S - inset, 1), fill=fill + (255,))
    else:
        rounded(d, box, radius * S, fill=fill + (255,))
    return image.resize((size_w, size_h), Image.LANCZOS)


BUTTONS = {
    "btn_primary": ((66, 99, 235), None),
    "btn_primary_hover": ((52, 83, 207), None),
    "btn_primary_pressed": ((46, 75, 199), None),
    "btn_primary_disabled": ((197, 207, 247), None),
    "btn_secondary": ((246, 248, 252), (211, 218, 232)),
    "btn_secondary_hover": ((233, 238, 251), (185, 198, 234)),
    "btn_secondary_pressed": ((220, 227, 245), (170, 184, 224)),
    "btn_secondary_disabled": ((241, 243, 247), (230, 234, 241)),
    "field": ((255, 255, 255), (211, 218, 232)),
    "field_focus": ((255, 255, 255), (66, 99, 235)),
    "field_readonly": ((246, 248, 252), (227, 232, 241)),
    "field_disabled": ((241, 243, 247), (230, 234, 241)),
}


def export(scale_name, factor):
    folder = ASSETS / scale_name
    folder.mkdir(parents=True, exist_ok=True)
    px = lambda value: int(round(value * factor))
    for key, (color, _, _) in CATEGORY_ICONS.items():
        tile_icon(px(20), color, GLYPHS[key]).save(folder / ("cat_" + key + ".png"))
    tile_icon(px(20), (227, 231, 239), g_skipped, glyph_color=(162, 171, 188, 255)).save(folder / "cat_skipped.png")
    for key, (color, glyph) in STAT_ICONS.items():
        tile_icon(px(22), color, glyph, radius=6).save(folder / (key + ".png"))
    for key, glyph in NAV_ICONS.items():
        for suffix, color in NAV_COLORS.items():
            mask_icon(px(18), glyph, color).save(folder / (key + suffix + ".png"))
    for state in ("on", "off", "none"):
        checkbox(px(18), state).save(folder / ("check_" + state + ".png"))
    for key, (fill, outline) in BUTTONS.items():
        # ttk tiles the stretchable middle with one Tk_RedrawImage call per tile (~0.3 ms
        # each on Windows), so the middle must be generous, while the image size is also
        # the element's minimum size, so it cannot be tall. Fields get a wide slice for
        # full-width entries plus a narrow one for short inputs.
        radius = 8 if key.startswith("btn") else 6
        width = 2 if key == "field_focus" else 1
        # Heights equal the natural control height (font + padding) so the middle band
        # needs a single vertical tile.
        if key.startswith("btn"):
            slice_image(px(radius * 2 + 64), px(36), px(radius), fill, outline, width * factor).save(folder / (key + ".png"))
        else:
            slice_image(px(radius * 2 + 200), px(32), px(radius), fill, outline, width * factor).save(folder / (key + ".png"))
            slice_image(px(radius * 2 + 40), px(32), px(radius), fill, outline, width * factor).save(folder / (key.replace("field", "field_small") + ".png"))
    make_icon.render(px(48)).save(folder / "logo.png")


def contact_sheet():
    """docs/assets_preview.png: every 1x asset at 2x magnification for review."""
    folder = ASSETS / "1x"
    files = sorted(folder.glob("*.png"))
    cell, columns = 64, 10
    rows = (len(files) + columns - 1) // columns
    sheet = Image.new("RGBA", (columns * cell, rows * cell), (243, 245, 250, 255))
    for index, path in enumerate(files):
        image = Image.open(path).convert("RGBA")
        image = image.resize((image.width * 2, image.height * 2), Image.NEAREST)
        x = (index % columns) * cell + (cell - image.width) // 2
        y = (index // columns) * cell + (cell - image.height) // 2
        sheet.paste(image, (x, y), image)
    sheet.save(ROOT / "docs" / "assets_preview.png")


def main():
    for name, factor in SCALES.items():
        export(name, factor)
    contact_sheet()
    print("wrote", sum(1 for _ in ASSETS.rglob("*.png")), "assets into", ASSETS)


if __name__ == "__main__":
    main()
