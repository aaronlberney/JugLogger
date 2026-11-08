"""Share graphics. Every graphic is a transparent PNG made to sit on top of a photo
or video, the way Strava's overlays do: light type, one red accent, no background.

Everything is drawn at 2x and scaled down at the end for smooth edges.
"""

from __future__ import annotations

import io
from dataclasses import dataclass
from datetime import datetime
from functools import lru_cache
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from ..boarddb import v_grade_rank
from ..summary import ClimbResult, SessionSummary
from .board import BoardRenderer

S = 2                          # supersampling factor
WIDTH, HEIGHT = 1080, 1920     # Instagram story
CX = WIDTH * S // 2
RED = (214, 40, 40)              # accent: #D62828
FONTS = Path(__file__).resolve().parent.parent / "fonts"
APP_NAME = "JUGLOGGER"


@dataclass(frozen=True)
class Theme:
    name: str
    fg: tuple[int, int, int]

    def ink(self, opacity: float = 1.0) -> tuple[int, int, int, int]:
        return self.fg + (round(255 * opacity),)


THEMES = {
    "light": Theme("light", (255, 255, 255)),   # white type, for photos and dark backgrounds
    "dark": Theme("dark", (17, 17, 17)),        # black type, for bright photos
}
ACCENT = RED + (255,)


@lru_cache(maxsize=64)
def font(weight: str, size: float) -> ImageFont.FreeTypeFont:
    files = {
        "medium": "Outfit_500Medium.ttf",
        "semibold": "Outfit_600SemiBold.ttf",
        "bold": "Outfit_700Bold.ttf",
        "black": "Outfit_800ExtraBold.ttf",
        "mono": "JetBrainsMono_500Medium.ttf",
        "mono-bold": "JetBrainsMono_700Bold.ttf",
    }
    return ImageFont.truetype(str(FONTS / files[weight]), round(size * S))


def u(value: float) -> int:
    return round(value * S)


# Text helpers

def text_width(text: str, f: ImageFont.FreeTypeFont, tracking: float = 0) -> float:
    if not tracking:
        return f.getlength(text)
    return sum(f.getlength(ch) for ch in text) + u(tracking) * (len(text) - 1)


def draw_text(draw: ImageDraw.ImageDraw, x: float, y: float, text: str,
              f: ImageFont.FreeTypeFont, fill, align: str = "center",
              tracking: float = 0) -> float:
    """Draws text with its top-of-caps at y. Returns the width drawn."""
    width = text_width(text, f, tracking)
    left = {"center": x - width / 2, "left": x, "right": x - width}[align]
    top = y - f.getbbox("H")[1]
    if not tracking:
        draw.text((left, top), text, font=f, fill=fill)
    else:
        for ch in text:
            draw.text((left, top), ch, font=f, fill=fill)
            left += f.getlength(ch) + u(tracking)
    return width


def cap_height(f: ImageFont.FreeTypeFont) -> int:
    box = f.getbbox("H")
    return box[3] - box[1]


def fit(text: str, weight: str, size: float, max_width: float, min_size: float) -> tuple[str, ImageFont.FreeTypeFont]:
    """Shrink the font until the text fits, then cut it with an ellipsis."""
    while size > min_size and font(weight, size).getlength(text) > u(max_width):
        size -= 2
    f = font(weight, size)
    if f.getlength(text) <= u(max_width):
        return text, f
    while text and f.getlength(text + "…") > u(max_width):
        text = text[:-1]
    return text.rstrip() + "…", f


# Building blocks

def draw_logo(image: Image.Image, y: float, theme: Theme, scale: float = 1.0) -> float:
    """The JugLogger mark (a red jug: a U-shaped hold) next to the wordmark,
    centered. Returns the bottom y."""
    draw = ImageDraw.Draw(image)
    f = font("black", 50 * scale)
    tracking = 1.5 * scale
    word_w = text_width(APP_NAME, f, tracking)
    cap = cap_height(f)
    mark_w = cap * 1.75
    gap = u(16 * scale)
    left = CX - (mark_w + gap + word_w) / 2
    draw_jug(image, left, y + cap * 0.08, mark_w, cap * 0.92)
    draw_text(draw, left + mark_w + gap, y, APP_NAME, f, theme.ink(), align="left", tracking=tracking)
    return y + cap


def draw_jug(image: Image.Image, left: float, top: float, width: float, height: float) -> None:
    """The mark: a jug hold seen side-on, a deep bowl with a flat rim. Drawn 4x larger
    and scaled down so the curves are smooth."""
    k = 4
    w, h = max(1, round(width * k)), max(1, round(height * k))
    mask = Image.new("L", (w, h * 2), 0)
    d = ImageDraw.Draw(mask)
    d.ellipse((0, -h, w, h), fill=255)                        # outer bowl
    lip = h * 0.34
    d.ellipse((w * 0.2, -h * 1.3, w * 0.8, lip), fill=0)       # the pocket you grab
    mask = mask.crop((0, 0, w, h)).resize((round(width), round(height)), Image.LANCZOS)
    layer = Image.new("RGBA", mask.size, ACCENT)
    layer.putalpha(mask)
    image.alpha_composite(layer, (round(left), round(top)))


def draw_stat(draw: ImageDraw.ImageDraw, y: float, label: str, value: str, theme: Theme,
              value_size: float = 112, accent_value: bool = False) -> float:
    """Strava-style stat: small label above a big value. Returns the bottom y."""
    label_font = font("medium", 40)
    draw_text(draw, CX, y, label, label_font, theme.ink(0.95))
    y += cap_height(label_font) + u(22)
    value_font = font("semibold", value_size)
    draw_text(draw, CX, y, value, value_font, ACCENT if accent_value else theme.ink())
    return y + cap_height(value_font)


def pyramid_rows(summary: SessionSummary) -> list[tuple[str, int]]:
    return summary.pyramid()


def draw_pyramid(draw: ImageDraw.ImageDraw, y: float, rows: list[tuple[str, int]], theme: Theme,
                 max_width: float, block: float, gap: float, labels: bool) -> float:
    """Centered stacks of red blocks, hardest grade on top. Returns the bottom y."""
    if not rows:
        return y
    widest = max(count for _, count in rows) or 1
    block = min(block, (max_width - gap * (widest - 1)) / widest)
    radius = u(block * 0.18)
    row_h = block + gap * 1.6
    label_font = font("semibold", block * 0.62)
    widest_px = widest * block + (widest - 1) * gap
    label_x = CX - u(widest_px / 2) - u(block * 0.55)
    for grade, count in rows:
        if labels:
            draw_text(draw, label_x, y + (u(block) - cap_height(label_font)) / 2, grade,
                      label_font, theme.ink(0.95 if count else 0.4), align="right")
        if count:
            row_w = count * block + (count - 1) * gap
            x = CX - u(row_w / 2)
            for _ in range(count):
                draw.rounded_rectangle((x, y, x + u(block), y + u(block)), radius=radius, fill=ACCENT)
                x += u(block + gap)
        else:
            draw.rounded_rectangle((CX - u(block * 0.4), y + u(block / 2) - u(2),
                                    CX + u(block * 0.4), y + u(block / 2) + u(2)),
                                   radius=u(2), fill=theme.ink(0.35))
        y += u(row_h)
    return y - u(gap * 1.6)


def tries_label(climb: ClimbResult) -> str:
    if climb.flash:
        return "FLASH"
    if climb.sent:
        tries = climb.tries_to_send or climb.tries
        return f"{tries} TRIES"
    return f"PROJECT · {climb.tries} {'TRY' if climb.tries == 1 else 'TRIES'}"


def angle_label(climb: ClimbResult) -> str:
    parts = [f"{climb.angle}°" if climb.angle is not None else "—"]
    if climb.is_mirror:
        parts.append("MIRRORED")
    return "  ·  ".join(parts)


def session_date(summary: SessionSummary) -> str:
    started = datetime.strptime(summary.started_at, "%Y-%m-%d %H:%M:%S")
    return started.strftime("%b %d").upper()


# The graphics

def render_stats(summary: SessionSummary, theme: Theme, **_) -> Image.Image:
    image = _canvas()
    draw = ImageDraw.Draw(image)
    hardest = summary.hardest
    if summary.send_count:
        stats = [("Sends", str(summary.send_count)),
                 ("Top Grade", hardest.grade if hardest and hardest.grade else "—"),
                 ("Time", summary_duration(summary))]
    else:
        stats = [("Climbs", str(len(summary.climbs))),
                 ("Attempts", str(summary.total_tries)),
                 ("Time", summary_duration(summary))]
    y = u(100)
    for index, (label, value) in enumerate(stats):
        y = draw_stat(draw, y, label, value, theme)
        y += u(64)
    rows = pyramid_rows(summary)
    if rows:
        y += u(8)
        rows = [row for row in rows if row[1]]
        y = draw_pyramid(draw, y, rows, theme, max_width=300, block=30, gap=8, labels=False)
        y += u(72)
    else:
        y += u(16)
    draw_logo(image, y, theme)
    return image


def render_pyramid(summary: SessionSummary, theme: Theme, **_) -> Image.Image:
    image = _canvas()
    draw = ImageDraw.Draw(image)
    y = draw_stat(draw, u(100), "Send Pyramid", f"{summary.send_count} {'send' if summary.send_count == 1 else 'sends'}",
                  theme, value_size=96)
    y += u(80)
    rows = pyramid_rows(summary)
    if rows:
        y = draw_pyramid(draw, y, rows, theme, max_width=600, block=78, gap=12, labels=True)
    else:
        draw_text(draw, CX, y, "No sends yet", font("medium", 44), theme.ink(0.6))
        y += cap_height(font("medium", 44))
    y += u(56)
    meta = f"{session_date(summary)}  ·  {summary_duration(summary).upper()}"
    if summary.angles:
        meta += "  ·  " + " / ".join(f"{a}°" for a in summary.angles)
    mono = font("mono", 28)
    draw_text(draw, CX, y, meta, mono, theme.ink(0.85), tracking=1)
    y += cap_height(mono) + u(72)
    draw_logo(image, y, theme)
    return image


MAX_LIST_ROWS = 10


def render_list(summary: SessionSummary, theme: Theme, **_) -> Image.Image:
    image = _canvas()
    draw = ImageDraw.Draw(image)
    mono = font("mono", 28)
    y = u(100)
    header = f"{session_date(summary)}  ·  {summary_duration(summary).upper()}  ·  {summary.send_count}/{len(summary.climbs)} SENT"
    draw_text(draw, CX, y, header, mono, theme.ink(0.85), tracking=1)
    y += cap_height(mono) + u(64)

    climbs = sorted(summary.climbs, key=lambda c: (not c.sent, -v_grade_rank(c.grade), c.first_at))
    shown = climbs[:MAX_LIST_ROWS]
    column = 840
    left, right = CX - u(column / 2), CX + u(column / 2)
    meta_font = font("mono", 26)
    grade_font = font("bold", 50)
    for index, climb in enumerate(shown):
        name, name_font = fit(climb.name, "semibold", 48, column - 150, 36)
        draw_text(draw, left, y, name, name_font, theme.ink(1 if climb.sent else 0.7), align="left")
        grade = climb.grade or "—"
        draw_text(draw, right, y, grade, grade_font, ACCENT if climb.sent else theme.ink(0.45), align="right")
        y += max(cap_height(name_font), cap_height(grade_font)) + u(20)
        x = left
        x += draw_text(draw, x, y, angle_label(climb) + "  ·  ", meta_font, theme.ink(0.7), align="left", tracking=1)
        label = tries_label(climb)
        draw_text(draw, x, y, label, meta_font, ACCENT if climb.flash else theme.ink(0.7), align="left", tracking=1)
        y += cap_height(meta_font)
        if index < len(shown) - 1:
            y += u(30)
            draw.line((left, y, right, y), fill=theme.ink(0.18), width=u(1.5))
            y += u(30)
    if len(climbs) > len(shown):
        y += u(40)
        draw_text(draw, CX, y, f"+{len(climbs) - len(shown)} more", font("medium", 34), theme.ink(0.7))
        y += cap_height(font("medium", 34))
    if not climbs:
        draw_text(draw, CX, y, "No climbs logged", font("medium", 44), theme.ink(0.6))
        y += cap_height(font("medium", 44))
    y += u(80)
    draw_logo(image, y, theme)
    return image


def render_board(summary: SessionSummary, theme: Theme, board: BoardRenderer | None = None,
                 climb_uuid: str | None = None, lit_only: bool = False, **_) -> Image.Image:
    climb = None
    if climb_uuid:
        climb = next((c for c in summary.climbs if c.climb_uuid == climb_uuid), None)
    label = "Hardest Send"
    if climb is None:
        climb = summary.hardest
    elif climb is not summary.hardest:
        label = "Sent" if climb.sent else "Project"
    if climb is None and summary.climbs:
        climb = max(summary.climbs, key=lambda c: v_grade_rank(c.grade))
        label = "Project"

    image = _canvas()
    draw = ImageDraw.Draw(image)
    if climb is None:
        y = draw_stat(draw, u(100), "Hardest Send", "—", theme)
        draw_logo(image, y + u(80), theme)
        return image

    label_font = font("medium", 40)
    y = u(100)
    draw_text(draw, CX, y, label, label_font, theme.ink(0.95))
    y += cap_height(label_font) + u(24)
    name, name_font = fit(climb.name, "semibold", 96, 940, 56)
    draw_text(draw, CX, y, name, name_font, theme.ink())
    y += cap_height(name_font) + u(36)

    meta_font = font("medium", 42)
    sep = ("  ·  ", theme.ink(0.6))
    parts = [(climb.grade or "—", ACCENT), sep,
             (angle_label(climb).replace("MIRRORED", "Mirrored"), theme.ink()), sep,
             (tries_label(climb).title(), ACCENT if climb.flash else theme.ink())]
    total = sum(meta_font.getlength(p) for p, _ in parts)
    x = CX - total / 2
    for part, color in parts:
        x += draw_text(draw, x, y, part, meta_font, color, align="left")
    y += cap_height(meta_font) + u(64)

    if board:
        board_image = board.render(climb.climb_uuid, climb.is_mirror, width=u(900), theme=theme.name,
                                   lit_only=lit_only)
        if board_image:
            image.alpha_composite(board_image, (CX - board_image.width // 2, round(y)))
            y += board_image.height + u(72)
    draw_logo(image, y, theme)
    return image


def summary_duration(summary: SessionSummary) -> str:
    from ..summary import format_duration
    return format_duration(summary.duration_seconds)


def _canvas() -> Image.Image:
    return Image.new("RGBA", (WIDTH * S, HEIGHT * S * 2), (0, 0, 0, 0))


GRAPHICS = {
    "stats": ("Stats", render_stats),
    "pyramid": ("Grade pyramid", render_pyramid),
    "list": ("Climb list", render_list),
    "board": ("Hardest send", render_board),
}


def render_graphic(kind: str, summary: SessionSummary, theme: str = "light",
                   crop: bool = True, board: BoardRenderer | None = None,
                   climb_uuid: str | None = None, lit_only: bool = False) -> bytes:
    """Returns PNG bytes. crop=True gives a sticker cut tight around the content;
    crop=False gives a full 1080x1920 story frame with the content centered."""
    _, renderer = GRAPHICS[kind]
    image = renderer(summary, THEMES[theme], board=board, climb_uuid=climb_uuid, lit_only=lit_only)
    image = image.resize((image.width // S, image.height // S), Image.LANCZOS)
    bbox = image.getchannel("A").getbbox() or (0, 0, 1, 1)
    content = image.crop(bbox)
    if crop:
        pad = 36
        out = Image.new("RGBA", (content.width + pad * 2, content.height + pad * 2), (0, 0, 0, 0))
        out.alpha_composite(content, (pad, pad))
    else:
        if content.height > HEIGHT - 120:
            ratio = (HEIGHT - 120) / content.height
            content = content.resize((round(content.width * ratio), HEIGHT - 120), Image.LANCZOS)
        out = Image.new("RGBA", (WIDTH, HEIGHT), (0, 0, 0, 0))
        # Keep the horizontal position (everything is drawn centered); center vertically.
        x = bbox[0] if content.width == bbox[2] - bbox[0] else (WIDTH - content.width) // 2
        out.alpha_composite(content, (x, (HEIGHT - content.height) // 2))
    buffer = io.BytesIO()
    out.save(buffer, "PNG", optimize=True)
    return buffer.getvalue()
