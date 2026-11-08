"""Draw a TB2 climb on the board: the colorless TB2 layout image (Mirror or Spray,
12x12) on a transparent background, unlit holds faded, the climb's holds at full
strength with a ring in their role color."""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

import numpy as np
from PIL import Image, ImageChops, ImageDraw, ImageFilter, ImageOps

from ..boarddb import BoardDB, Layout
from .calibrate import Fit, fit, median_spacing

ASSETS = Path(__file__).resolve().parent.parent / "assets"
RING_RADIUS = 0.5         # ring radius, as a fraction of the typical gap between holds
DIM_ALPHA = 0.32          # how visible the unlit holds are


def asset_name(layout: Layout) -> str | None:
    name = layout.name.lower()
    if "mirror" in name:
        return "tb2-mirror-12x12"
    if "spray" in name:
        return "tb2-spray-12x12"
    return None


def _hex_to_rgb(color: str) -> tuple[int, int, int]:
    color = color.lstrip("#")
    return tuple(int(color[i:i + 2], 16) for i in (0, 2, 4))


def _brighten(rgb: tuple[int, int, int]) -> tuple[int, int, int]:
    """LED colors like pure blue read dark on a phone; lift them a little."""
    return tuple(min(255, int(v * 0.8 + 60)) for v in rgb)


class BoardRenderer:
    def __init__(self, db: BoardDB, assets_dir: Path = ASSETS):
        self.db = db
        self.assets_dir = assets_dir

    @lru_cache(maxsize=4)
    def layout_image(self, layout_id: int) -> tuple[Image.Image, Fit, float] | None:
        """The layout's hold image, its calibration to board coordinates, and the
        ring radius in image pixels."""
        layout = self.db.layout(layout_id)
        name = asset_name(layout) if layout else None
        if not name or not (self.assets_dir / f"{name}.png").exists():
            return None
        image = Image.open(self.assets_dir / f"{name}.png").convert("RGBA")
        meta = json.loads((self.assets_dir / f"{name}.json").read_text())
        holes = self.db.layout_hole_positions(layout_id)
        size = self.db.product_size_12x12(layout.product_id)
        if size:
            holes = [(x, y) for x, y in holes
                     if size.edge_left <= x <= size.edge_right and size.edge_bottom <= y <= size.edge_top]
        if len(holes) < 10:
            return None
        centers = [(x, y) for x, y, _ in meta["holds"]]
        calibration = fit(holes, centers)
        radius = RING_RADIUS * median_spacing(np.asarray(centers, float))
        return image, calibration, radius

    def render(self, climb_uuid: str, is_mirror: bool, width: int, theme: str = "light",
               lit_only: bool = False) -> Image.Image | None:
        """lit_only=True drops every hold that isn't part of the climb."""
        climb = self.db.climb(climb_uuid)
        if not climb or climb.layout_id is None:
            return None
        loaded = self.layout_image(climb.layout_id)
        if not loaded:
            return None
        image, calibration, ring_px = loaded
        holds = self.db.holds(climb, mirrored=is_mirror)

        scale = width / image.width
        board = image.resize((width, round(image.height * scale)), Image.LANCZOS)
        if theme == "dark":
            # Black type on a bright photo: dark holds instead of light ones.
            rgb = ImageOps.invert(board.convert("RGB")).point(lambda v: int(v * 0.8))
            rgb.putalpha(board.getchannel("A"))
            board = rgb

        def to_px(x: float, y: float) -> tuple[float, float]:
            px, py = calibration.to_px(x, y)
            return px * scale, py * scale

        # Fade every hold, then bring the lit holds back at full strength.
        dimmed = board.copy()
        dim = 0.0 if lit_only else DIM_ALPHA
        dimmed.putalpha(board.getchannel("A").point(lambda a: int(a * dim)))
        radius = ring_px * scale
        lit_mask = Image.new("L", board.size, 0)
        draw_mask = ImageDraw.Draw(lit_mask)
        for hold in holds:
            cx, cy = to_px(hold.x, hold.y)
            r = radius * 0.95
            draw_mask.ellipse((cx - r, cy - r, cx + r, cy + r), fill=255)
        lit = board.copy()
        lit.putalpha(ImageChops.multiply(board.getchannel("A"), lit_mask))
        out = Image.alpha_composite(dimmed, lit)

        # Rings in the hold's role color, with a soft LED glow.
        stroke = max(3, round(radius * 0.2))
        glow = Image.new("RGBA", board.size, (0, 0, 0, 0))
        rings = Image.new("RGBA", board.size, (0, 0, 0, 0))
        draw_glow, draw_rings = ImageDraw.Draw(glow), ImageDraw.Draw(rings)
        for hold in holds:
            cx, cy = to_px(hold.x, hold.y)
            color = _brighten(_hex_to_rgb(hold.color))
            box = (cx - radius, cy - radius, cx + radius, cy + radius)
            draw_glow.ellipse(box, outline=color + (190,), width=stroke * 2)
            draw_rings.ellipse(box, outline=color + (255,), width=stroke)
        glow = glow.filter(ImageFilter.GaussianBlur(radius * 0.3))
        out = Image.alpha_composite(out, glow)
        return Image.alpha_composite(out, rings)

    def calibration_preview(self, layout_id: int, width: int = 1200) -> Image.Image | None:
        """Every database hole ringed on the layout image, to check the alignment by eye."""
        loaded = self.layout_image(layout_id)
        if not loaded:
            return None
        image, calibration, ring_px = loaded
        scale = width / image.width
        board = image.resize((width, round(image.height * scale)), Image.LANCZOS)
        out = Image.new("RGBA", board.size, (24, 26, 32, 255))
        out.alpha_composite(board)
        draw = ImageDraw.Draw(out)
        r = ring_px * scale * 0.6
        for x, y in self.db.layout_hole_positions(layout_id):
            px, py = calibration.to_px(x, y)
            px, py = px * scale, py * scale
            draw.ellipse((px - r, py - r, px + r, py + r), outline=(255, 59, 48, 255), width=2)
        return out
