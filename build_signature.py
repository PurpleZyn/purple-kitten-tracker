import json
import math
import random
from pathlib import Path

from PIL import Image, ImageChops, ImageDraw, ImageEnhance, ImageFilter, ImageFont, ImageOps, ImageSequence

ROOT = Path(__file__).resolve().parent
CONFIG_PATH = ROOT / "signature_config.json"


def load_config():
    with CONFIG_PATH.open("r", encoding="utf-8") as f:
        return json.load(f)


def resolve_path(value):
    path = Path(value)
    if path.is_absolute():
        return path
    return ROOT / path


def open_image(path):
    path = resolve_path(path)
    if not path.exists():
        raise FileNotFoundError(f"Signature asset not found: {path.relative_to(ROOT)}")

    img = Image.open(path)

    # If an animated source is ever used, the combined signature is a PNG,
    # so use its first frame.
    try:
        if getattr(img, "is_animated", False):
            img.seek(0)
    except Exception:
        pass

    return img.convert("RGBA")


def font(size, bold=True):
    candidates = [
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/truetype/liberation2/LiberationSans-Bold.ttf",
        "/usr/share/fonts/truetype/liberation2/LiberationSans-Regular.ttf",
    ]

    if not bold:
        candidates = [p for p in candidates if "Bold" not in p] + candidates

    for candidate in candidates:
        p = Path(candidate)
        if p.exists():
            return ImageFont.truetype(str(p), size=size)

    return ImageFont.load_default()


def contain_width(img, target_width, allow_upscale=True):
    if img.width == target_width:
        return img

    if img.width < target_width and not allow_upscale:
        return img

    ratio = target_width / img.width
    target_height = max(1, round(img.height * ratio))
    return img.resize((target_width, target_height), Image.Resampling.LANCZOS)


def make_background(width, height, source):
    """
    Builds one continuous background for the entire signature.

    The source artwork is converted into a dark purple texture, resized to the
    full signature width, tiled vertically, and alternated so obvious seams are
    less noticeable.
    """
    texture = open_image(source)

    # Resize texture to the final canvas width.
    ratio = width / texture.width
    texture = texture.resize(
        (width, max(1, round(texture.height * ratio))),
        Image.Resampling.LANCZOS,
    )

    # Darken slightly so the real banners remain the focus.
    texture = ImageEnhance.Brightness(texture).enhance(0.58)
    texture = ImageEnhance.Contrast(texture).enhance(1.08)
    texture = ImageEnhance.Color(texture).enhance(0.95)

    bg = Image.new("RGBA", (width, height), (9, 4, 15, 255))

    y = 0
    flip = False
    while y < height:
        tile = ImageOps.flip(texture) if flip else texture
        crop_h = min(tile.height, height - y)
        bg.alpha_composite(tile.crop((0, 0, tile.width, crop_h)), (0, y))
        y += crop_h
        flip = not flip

    # Purple/black wash to unify all repeated texture pieces.
    wash = Image.new("RGBA", bg.size, (15, 3, 25, 95))
    bg = Image.alpha_composite(bg, wash)

    # Subtle vignette.
    vignette = Image.new("L", (width, height), 0)
    vd = ImageDraw.Draw(vignette)
    steps = 70
    for i in range(steps):
        alpha = int(210 * (1 - i / steps) ** 2)
        vd.rectangle(
            (i, i, width - 1 - i, height - 1 - i),
            outline=alpha,
            width=1,
        )
    black = Image.new("RGBA", bg.size, (0, 0, 0, 0))
    black.putalpha(vignette)
    bg = Image.alpha_composite(bg, black)

    # A few deterministic purple particles.
    rnd = random.Random(3683025)
    sparks = Image.new("RGBA", bg.size, (0, 0, 0, 0))
    sd = ImageDraw.Draw(sparks)
    for _ in range(max(40, height // 22)):
        x = rnd.randrange(20, max(21, width - 20))
        y = rnd.randrange(20, max(21, height - 20))
        r = rnd.choice([1, 1, 1, 2, 2, 3])
        a = rnd.randrange(18, 70)
        sd.ellipse((x-r, y-r, x+r, y+r), fill=(206, 98, 255, a))
    sparks = sparks.filter(ImageFilter.GaussianBlur(0.5))
    bg = Image.alpha_composite(bg, sparks)

    return bg


def trim_transparency(img):
    """Crop unused transparent margins from decorative overlay art."""
    if "A" not in img.getbands():
        return img
    bbox = img.getchannel("A").getbbox()
    return img.crop(bbox) if bbox else img


def prepare_header_asset(img, target_width):
    """Trim and resize a transparent header overlay to a consistent width."""
    img = trim_transparency(img)
    return contain_width(img, target_width)


def paste_header_asset(canvas, header, y):
    """Blend a transparent header into the existing signature background."""
    x = (canvas.width - header.width) // 2

    # A soft purple bloom underneath helps the transparent artwork feel
    # integrated into the page instead of pasted on top.
    alpha = header.getchannel("A")
    glow_alpha = alpha.filter(ImageFilter.GaussianBlur(9))
    glow_alpha = glow_alpha.point(lambda p: int(p * 0.34))
    glow = Image.new("RGBA", header.size, (205, 76, 255, 0))
    glow.putalpha(glow_alpha)
    canvas.alpha_composite(glow, (x, y))

    canvas.alpha_composite(header, (x, y))
    return y + header.height


def make_banner_card(img, card_width):
    """
    Places a real banner inside a consistent dark-purple card.

    Banner artwork itself is never AI-redrawn or altered except for resizing.
    """
    horizontal_pad = 12
    vertical_pad = 12
    inner_width = card_width - (horizontal_pad * 2)
    banner = contain_width(img, inner_width)

    card = Image.new(
        "RGBA",
        (card_width, banner.height + vertical_pad * 2),
        (0, 0, 0, 0),
    )
    d = ImageDraw.Draw(card)

    # Outer panel.
    d.rounded_rectangle(
        (0, 0, card.width - 1, card.height - 1),
        radius=14,
        fill=(8, 6, 13, 228),
        outline=(203, 100, 246, 235),
        width=3,
    )
    # Inner fine border.
    d.rounded_rectangle(
        (5, 5, card.width - 6, card.height - 6),
        radius=11,
        outline=(91, 38, 124, 190),
        width=1,
    )

    x = (card_width - banner.width) // 2
    card.alpha_composite(banner, (x, vertical_pad))
    return card


def paste_with_shadow(canvas, card, x, y):
    shadow = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
    sd = ImageDraw.Draw(shadow)
    sd.rounded_rectangle(
        (x + 7, y + 9, x + card.width + 7, y + card.height + 9),
        radius=15,
        fill=(0, 0, 0, 150),
    )
    shadow = shadow.filter(ImageFilter.GaussianBlur(9))
    canvas.alpha_composite(shadow)
    canvas.alpha_composite(card, (x, y))


def draw_section_header(canvas, y, text, wide=False):
    """
    Draws a header directly onto the background rather than inserting another
    image, so the whole signature feels like one continuous piece.
    """
    draw = ImageDraw.Draw(canvas)
    width = canvas.width

    box_width = 760 if wide else 680
    box_height = 62
    x1 = (width - box_width) // 2
    x2 = x1 + box_width
    y2 = y + box_height

    # Soft glow behind header.
    glow = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
    gd = ImageDraw.Draw(glow)
    gd.rounded_rectangle(
        (x1 - 4, y - 4, x2 + 4, y2 + 4),
        radius=18,
        outline=(211, 91, 255, 120),
        width=7,
    )
    glow = glow.filter(ImageFilter.GaussianBlur(10))
    canvas.alpha_composite(glow)

    # Main plate.
    draw.rounded_rectangle(
        (x1, y, x2, y2),
        radius=14,
        fill=(13, 7, 22, 226),
        outline=(211, 91, 255, 245),
        width=2,
    )

    # Thin decorative rails.
    mid_y = y + box_height // 2
    rail = 92 if wide else 72
    draw.line((x1 + 22, mid_y, x1 + 22 + rail, mid_y),
              fill=(164, 69, 213, 190), width=2)
    draw.line((x2 - 22 - rail, mid_y, x2 - 22, mid_y),
              fill=(164, 69, 213, 190), width=2)

    title_font = font(31 if wide else 28, bold=True)
    bbox = draw.textbbox((0, 0), text, font=title_font)
    tw = bbox[2] - bbox[0]
    th = bbox[3] - bbox[1]
    tx = (width - tw) // 2
    ty = y + (box_height - th) // 2 - 3

    # Shadow + bright title.
    draw.text((tx + 2, ty + 2), text, font=title_font,
              fill=(47, 11, 63, 255))
    draw.text((tx, ty), text, font=title_font,
              fill=(245, 225, 255, 255))

    return y2


def draw_footer(canvas, y, text):
    if not text:
        return y

    draw = ImageDraw.Draw(canvas)
    f = font(22, bold=True)
    bbox = draw.textbbox((0, 0), text, font=f)
    tw = bbox[2] - bbox[0]
    tx = (canvas.width - tw) // 2

    line_y = y + 7
    margin = 90
    gap = 24
    left_end = tx - gap
    right_start = tx + tw + gap

    if left_end > margin:
        draw.line((margin, line_y + 12, left_end, line_y + 12),
                  fill=(126, 47, 167, 150), width=1)
    if right_start < canvas.width - margin:
        draw.line((right_start, line_y + 12, canvas.width - margin, line_y + 12),
                  fill=(126, 47, 167, 150), width=1)

    draw.text((tx + 1, y + 1), text, font=f, fill=(49, 8, 65, 255))
    draw.text((tx, y), text, font=f, fill=(211, 157, 240, 255))
    return y + 36


def build_signature():
    cfg = load_config()

    canvas_width = int(cfg.get("canvas_width", 1000))
    outer_margin = 30
    card_width = canvas_width - (outer_margin * 2)

    tracker = make_banner_card(open_image(cfg["live_tracker"]), card_width)

    identity_cards = [
        make_banner_card(open_image(path), card_width)
        for path in cfg.get("identity_banners", [])
    ]

    history_header_path = cfg.get("history_header_image")
    history_header = (
        prepare_header_asset(open_image(history_header_path), 780)
        if history_header_path
        else None
    )

    history = []
    for section in cfg.get("sections", []):
        history.append(
            {
                "title": section["title"],
                "header": (
                    prepare_header_asset(open_image(section["header_image"]), 700)
                    if section.get("header_image")
                    else None
                ),
                "cards": [
                    make_banner_card(open_image(path), card_width)
                    for path in section.get("banners", [])
                ],
            }
        )

    footer_art_path = cfg.get("footer_art")
    footer_art = (
        prepare_header_asset(open_image(footer_art_path), 460)
        if footer_art_path
        else None
    )

    # Layout sizes.
    top_gap = 28
    normal_gap = 18
    identity_gap = 20
    before_history = 34
    header_height = 62
    after_header = 10
    between_sections = 22
    footer_space = 78
    bottom_gap = 30

    # Calculate final height before drawing the background.
    height = top_gap
    height += tracker.height + normal_gap

    for card in identity_cards:
        height += card.height + identity_gap

    height += before_history + (history_header.height if history_header else header_height) + 18

    for section in history:
        height += (section["header"].height if section["header"] else header_height) + after_header
        for card in section["cards"]:
            height += card.height + normal_gap
        height += between_sections

    if footer_art:
        height += footer_art.height + 8

    height += footer_space + bottom_gap

    canvas = make_background(
        canvas_width,
        height,
        cfg.get("background", "docs/purplezyn-bg.png"),
    )

    # Continuous outer neon frame.
    draw = ImageDraw.Draw(canvas)
    draw.rounded_rectangle(
        (8, 8, canvas_width - 9, height - 9),
        radius=18,
        outline=(181, 76, 225, 210),
        width=2,
    )
    draw.rounded_rectangle(
        (13, 13, canvas_width - 14, height - 14),
        radius=16,
        outline=(67, 28, 90, 165),
        width=1,
    )

    y = top_gap
    x = outer_margin

    paste_with_shadow(canvas, tracker, x, y)
    y += tracker.height + normal_gap

    for card in identity_cards:
        paste_with_shadow(canvas, card, x, y)
        y += card.height + identity_gap

    y += before_history

    if history_header:
        y = paste_header_asset(canvas, history_header, y)
    else:
        y = draw_section_header(
            canvas,
            y,
            cfg.get("history_title", "BATTLE HISTORY"),
            wide=True,
        )
    y += 18

    for section in history:
        if section["header"]:
            y = paste_header_asset(canvas, section["header"], y)
        else:
            y = draw_section_header(canvas, y, section["title"], wide=False)
        y += after_header

        for card in section["cards"]:
            paste_with_shadow(canvas, card, x, y)
            y += card.height + normal_gap

        y += between_sections

    if footer_art:
        y = paste_header_asset(canvas, footer_art, y)
        y += 4

    footer_text = cfg.get("footer_text", "")
    y += 8
    draw_footer(canvas, y, footer_text)

    output_path = resolve_path(cfg.get("output", "docs/signature.png"))
    output_path.parent.mkdir(parents=True, exist_ok=True)

    # Convert to RGB to keep the final PNG broadly compatible and smaller.
    final = Image.new("RGB", canvas.size, (8, 5, 12))
    final.paste(canvas, mask=canvas.getchannel("A"))
    final.save(output_path, "PNG", optimize=True)

    print(f"Built {output_path.relative_to(ROOT)} ({final.width} x {final.height})")


if __name__ == "__main__":
    build_signature()
