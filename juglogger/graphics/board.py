"""Draw a TB2 climb on the board: the real hold images, dimmed, with the climb's
holds lit in their role colors."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from PIL import Image, ImageChops, ImageDraw, ImageFilter

from ..boarddb import BoardDB, ProductSize

HOLD_RADIUS_UNITS = 3.4   # ring radius in board units (inches on the real board)
DIM_ALPHA = 0.30          # how visible the unlit holds are


def _hex_to_rgb(color: str) -> tuple[int, int, int]:
    color = color.lstrip("#")
    return tuple(int(color[i:i + 2], 16) for i in (0, 2, 4))


def _brighten(rgb: tuple[int, int, int]) -> tuple[int, int, int]:
    """LED colors like pure blue read dark on a phone; lift them a little."""
    return tuple(min(255, int(v * 0.8 + 60)) for v in rgb)


class BoardRenderer:
    def __init__(self, db: BoardDB, images_dir: Path | None):
        self.db = db
        self.images_dir = images_dir

    @lru_cache(maxsize=8)
    def _base(self, layout_id: int) -> tuple[Image.Image, ProductSize] | None:
        layout = self.db.layout(layout_id)
        if not layout:
            return None
        size = self.db.product_size_12x12(layout.product_id)
        if not size:
            return None
        images = []
        if self.images_dir:
            for filename in self.db.image_filenames(layout_id, size.id):
                path = self.images_dir / filename
                if path.exists():
                    images.append(Image.open(path).convert("RGBA"))
        if images:
            canvas = Image.new("RGBA", images[0].size, (0, 0, 0, 0))
            for image in images:
                if image.size != canvas.size:
                    image = image.resize(canvas.size, Image.LANCZOS)
                canvas.alpha_composite(image)
            return canvas, size
        return None, size

    def has_images(self, layout_id: int) -> bool:
        base = self._base(layout_id)
        return bool(base and base[0] is not None)

    def render(self, climb_uuid: str, is_mirror: bool, width: int,
               fallback_color: tuple[int, int, int] = (255, 255, 255)) -> Image.Image | None:
        climb = self.db.climb(climb_uuid)
        if not climb or climb.layout_id is None:
            return None
        base = self._base(climb.layout_id)
        if not base:
            return None
        image, size = base
        holds = self.db.holds(climb, mirrored=is_mirror)

        if image is None:
            image = self._placeholder(climb.layout_id, size, fallback_color)
        scale = width / image.width
        board = image.resize((width, round(image.height * scale)), Image.LANCZOS)
        units_x = size.edge_right - size.edge_left
        units_y = size.edge_top - size.edge_bottom
        px_per_unit = board.width / units_x

        def to_px(x: float, y: float) -> tuple[float, float]:
            return ((x - size.edge_left) / units_x * board.width,
                    board.height - (y - size.edge_bottom) / units_y * board.height)

        # Dim every hold, then cut the lit holds back in at full strength.
        alpha = board.getchannel("A").point(lambda a: int(a * DIM_ALPHA))
        dimmed = board.copy()
        dimmed.putalpha(alpha)

        radius = HOLD_RADIUS_UNITS * px_per_unit
        lit_mask = Image.new("L", board.size, 0)
        draw_mask = ImageDraw.Draw(lit_mask)
        for hold in holds:
            cx, cy = to_px(hold.x, hold.y)
            r = radius * 0.92
            draw_mask.ellipse((cx - r, cy - r, cx + r, cy + r), fill=255)
        lit = board.copy()
        lit.putalpha(ImageChops.multiply(board.getchannel("A"), lit_mask))
        out = Image.alpha_composite(dimmed, lit)

        # Rings with a soft LED glow.
        stroke = max(3, round(radius * 0.2))
        glow = Image.new("RGBA", board.size, (0, 0, 0, 0))
        rings = Image.new("RGBA", board.size, (0, 0, 0, 0))
        draw_glow, draw_rings = ImageDraw.Draw(glow), ImageDraw.Draw(rings)
        for hold in holds:
            cx, cy = to_px(hold.x, hold.y)
            color = _brighten(_hex_to_rgb(hold.color))
            box = (cx - radius, cy - radius, cx + radius, cy + radius)
            draw_glow.ellipse(box, outline=color + (200,), width=stroke * 2)
            draw_rings.ellipse(box, outline=color + (255,), width=stroke)
        glow = glow.filter(ImageFilter.GaussianBlur(radius * 0.35))
        out = Image.alpha_composite(out, glow)
        return Image.alpha_composite(out, rings)

    def _placeholder(self, layout_id: int, size: ProductSize,
                     color: tuple[int, int, int]) -> Image.Image:
        """No hold images downloaded: draw every hole as a small dot instead."""
        width = 1080
        height = round(width * (size.edge_top - size.edge_bottom) / (size.edge_right - size.edge_left))
        image = Image.new("RGBA", (width, height), (0, 0, 0, 0))
        draw = ImageDraw.Draw(image)
        units_x = size.edge_right - size.edge_left
        units_y = size.edge_top - size.edge_bottom
        for x, y in self.db.layout_hole_positions(layout_id):
            px = (x - size.edge_left) / units_x * width
            py = height - (y - size.edge_bottom) / units_y * height
            draw.ellipse((px - 7, py - 7, px + 7, py + 7), fill=color + (255,))
        return image
