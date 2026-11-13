"""Draw a TB2 climb on the board: the TB2 layout image (Mirror or Spray, 12x12, holds
in their real colors) on a transparent background, unlit holds faded, the climb's
holds at full strength and outlined in their role color."""

from __future__ import annotations

import json
import logging
import math
from functools import lru_cache
from pathlib import Path

import numpy as np
from PIL import Image, ImageChops, ImageDraw, ImageFilter

from ..boarddb import BoardDB, Layout
from .calibrate import Fit, fit, median_spacing

log = logging.getLogger(__name__)
ASSETS = Path(__file__).resolve().parent.parent / "assets"
RING_RADIUS = 0.5         # outline scale (and the fallback ring radius), as a fraction of the typical gap between holds
MAX_SHAVE = 21            # most pixels shaved off the hold shapes to pull touching holds apart
CUT_RECLAIM = 3           # pixels of a hand-drawn cut line that the hold beside it takes back
SHAPE_REACH = 5           # how far from its bolt a hold can extend, in ring radii
DIM_ALPHA = 0.47          # how visible the unlit holds are
RIM_WIDTH = 0.0035        # dark rim around each hold, as a fraction of the board width
RIM_COLOR = (14, 15, 18)
RIM_ALPHA_LIGHT = 0.4    # rim strength under white type (dark photos)
RIM_ALPHA_DARK = 0.85     # rim strength under black type (bright photos)


HEAT_FULL = 10            # uses at which a heatmap hold reaches its darkest shade, in any session
HEAT_DEEP = (74, 8, 12)   # that darkest shade


def heat_color(color: tuple[int, int, int], count: int) -> tuple[int, int, int]:
    """The heatmap shade for a hold used `count` times: `color` at one use, HEAT_DEEP
    at HEAT_FULL uses or more. It depends on the count alone, never on the rest of the
    session, and steps are larger at low counts, where most holds are."""
    t = min(1.0, math.log(max(1, count)) / math.log(HEAT_FULL))
    return tuple(round(a + (b - a) * t) for a, b in zip(color, HEAT_DEEP))


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


def _grow(mask: Image.Image, by: float) -> Image.Image:
    """Grow a 0/255 mask outward by about `by` pixels, with rounded corners. A blur
    of sigma `by` crosses 16% of full strength that far outside a straight edge."""
    return mask.filter(ImageFilter.GaussianBlur(by)).point(lambda v: 255 if v > 40 else 0)


def _hold_shape(solid: Image.Image, center: tuple[float, float], holes: np.ndarray,
                fallback_radius: float, cuts: Image.Image | None = None) -> tuple[Image.Image, tuple[int, int]]:
    """The mask of the hold bolted at `center` (layout-image pixels) and its top-left
    corner. `solid` is the layout image's hold mask (0 or 255); `holes` are the pixel
    positions of every bolt on the layout. Where holds touch in the image they are
    pulled apart, so the shape follows the hold's own edge and may lap slightly onto
    its neighbor. `cuts` are hand-drawn gaps already removed from `solid`; a hold
    takes back the part of a gap right next to it. With no hold under the bolt, it's a plain circle."""
    cx, cy = center
    reach = round(fallback_radius * SHAPE_REACH)
    left, top = max(0, round(cx) - reach), max(0, round(cy) - reach)
    right, bottom = min(solid.width, round(cx) + reach), min(solid.height, round(cy) + reach)
    window = solid.crop((left, top, right, bottom))
    mask = np.asarray(window) > 0
    ys, xs = np.mgrid[top:bottom, left:right]
    distance = np.hypot(xs - cx, ys - cy)

    hx, hy = holes[:, 0], holes[:, 1]
    near = (hx >= left) & (hx < right) & (hy >= top) & (hy < bottom) & (np.hypot(hx - cx, hy - cy) > 1)
    neighbors = [(ox, oy, int(oy) - top, int(ox) - left) for ox, oy in holes[near]]

    def spread(image: Image.Image) -> np.ndarray:
        return np.asarray(image.filter(ImageFilter.MaxFilter(3))) > 0

    def flood(shrink: int) -> np.ndarray | None:
        """The blob under the bolt, after shaving `shrink` pixels off every blob, which
        separates holds that touch. The shaved pieces then grow back a pixel at a time
        until they meet, so each hold gets its own edge back."""
        eroded = window.filter(ImageFilter.MinFilter(shrink)) if shrink > 1 else window.copy()
        nearest = np.where(np.asarray(eroded) > 0, distance, np.inf)
        y, x = np.unravel_index(nearest.argmin(), nearest.shape)
        if nearest[y, x] > fallback_radius:
            return None
        ImageDraw.floodfill(eroded, (int(x), int(y)), 128)
        pieces = np.asarray(eroded)
        mine, rest = pieces == 128, pieces == 255
        for _ in range(shrink if shrink > 1 else 0):
            grown = spread(Image.fromarray(mine)) & mask & ~rest
            rest = (spread(Image.fromarray(rest)) & mask & ~grown) | rest
            if (grown == mine).all():
                break
            mine = grown
        return mine

    region = None
    for shrink in range(1, MAX_SHAVE + 1, 2):
        found = flood(shrink)
        if found is None:
            break
        region = found
        if not any(region[row, col] for _, _, row, col in neighbors):
            break
    if region is None:
        return Image.fromarray(((distance <= fallback_radius) * 255).astype(np.uint8)), (left, top)

    # Last resort, for holds that never come apart: each pixel goes to the nearest bolt.
    for ox, oy, row, col in neighbors:
        if region[row, col]:
            log.debug("hold at %.0f,%.0f is fused with its neighbor; cut between the bolts", cx, cy)
            region &= distance <= np.hypot(xs - ox, ys - oy)
    if cuts is not None:
        gap = np.asarray(cuts.crop((left, top, right, bottom))) > 0
        reach_into = region
        for _ in range(CUT_RECLAIM):
            reach_into = np.asarray(Image.fromarray(reach_into).filter(ImageFilter.MaxFilter(3))) > 0
        region = region | (reach_into & gap)
    return Image.fromarray((region * 255).astype(np.uint8)), (left, top)


class BoardRenderer:
    def __init__(self, db: BoardDB, assets_dir: Path = ASSETS):
        self.db = db
        self.assets_dir = assets_dir
        self._shapes: dict[tuple, tuple[Image.Image, tuple[int, int]]] = {}   # hold shapes already found

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

    @lru_cache(maxsize=4)
    def _hold_mask(self, layout_id: int) -> tuple[Image.Image, Image.Image | None]:
        """Where the holds are in the layout image (0 or 255), with the hand-drawn cut
        lines (tb2-*-cuts.png, see scripts/import_hold_cuts.py) taken out, and those
        cut lines where they cross a hold."""
        image = self.layout_image(layout_id)[0]
        solid = image.getchannel("A").point(lambda a: 255 if a > 127 else 0)
        path = self.assets_dir / f"{asset_name(self.db.layout(layout_id))}-cuts.png"
        if not path.exists():
            return solid, None
        cuts = ImageChops.multiply(Image.open(path).convert("L").point(lambda v: 255 if v > 127 else 0), solid)
        return ImageChops.subtract(solid, cuts), cuts

    @lru_cache(maxsize=4)
    def _bolts(self, layout_id: int) -> np.ndarray:
        calibration = self.layout_image(layout_id)[1]
        return np.array([calibration.to_px(x, y) for x, y in self.db.layout_hole_positions(layout_id)])

    def hold_shape(self, layout_id: int, x: float, y: float) -> tuple[Image.Image, tuple[int, int]]:
        """The mask of the hold bolted at board position (x, y), in layout-image pixels,
        and its top-left corner."""
        key = (layout_id, x, y)
        if key not in self._shapes:
            _, calibration, ring_px = self.layout_image(layout_id)
            solid, cuts = self._hold_mask(layout_id)
            self._shapes[key] = _hold_shape(solid, calibration.to_px(x, y), self._bolts(layout_id), ring_px, cuts)
        return self._shapes[key]

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

        def to_px(x: float, y: float) -> tuple[float, float]:
            px, py = calibration.to_px(x, y)
            return px * scale, py * scale

        # The shape of each lit hold, found in the layout image around its bolt.
        shapes = [(hold, *self.hold_shape(climb.layout_id, hold.x, hold.y)) for hold in holds]

        radius = ring_px * scale
        stroke = max(3, round(radius * 0.2))
        pad = stroke * 4
        lit_mask = Image.new("L", board.size, 0)
        glow = Image.new("RGBA", board.size, (0, 0, 0, 0))
        outlines = Image.new("RGBA", board.size, (0, 0, 0, 0))
        for hold, shape, (left, top) in shapes:
            # Scale the shape to the output size, with room around it for the outline.
            size = (max(1, round(shape.width * scale)), max(1, round(shape.height * scale)))
            local = Image.new("L", (size[0] + pad * 2, size[1] + pad * 2), 0)
            local.paste(shape.resize(size, Image.BILINEAR), (pad, pad))
            origin = (round(left * scale) - pad, round(top * scale) - pad)
            inside = local.point(lambda v: 255 if v > 127 else 0)
            lit_mask.paste(255, origin, inside)
            color = _brighten(_hex_to_rgb(hold.color))
            line = ImageChops.subtract(_grow(inside, stroke), inside)
            wide = ImageChops.subtract(_grow(inside, stroke * 2), inside)
            line = line.filter(ImageFilter.GaussianBlur(0.7))
            glow.paste(color + (190,), origin, wide)
            outlines.paste(color + (255,), origin, line)

        # Fade every hold, then bring the lit holds back at full strength.
        alpha = board.getchannel("A")
        dim = 0.0 if lit_only else DIM_ALPHA
        dimmed = board.copy()
        dimmed.putalpha(alpha.point(lambda a: int(a * dim)))
        lit = board.copy()
        lit.putalpha(ImageChops.multiply(alpha, lit_mask))
        out = Image.alpha_composite(dimmed, lit)

        # A dark rim around the unlit holds, so the light holds still read on a bright
        # or busy photo. Stronger under black type (theme "dark" = bright photo).
        if dim:
            grow = max(1, round(width * RIM_WIDTH))
            # A blur pushed back up to full strength: a quick way to grow the hold shapes.
            rim = alpha.filter(ImageFilter.GaussianBlur(grow / 2)).point(lambda a: min(255, a * 3))
            rim = ImageChops.multiply(rim, ImageChops.invert(alpha))   # around the holds, never behind them
            strength = (RIM_ALPHA_DARK if theme == "dark" else RIM_ALPHA_LIGHT) * min(1.0, dim * 1.5)
            backing = Image.new("RGBA", board.size, RIM_COLOR + (0,))
            backing.putalpha(rim.point(lambda a: int(a * strength)))
            out = Image.alpha_composite(backing, out)

        # The lit holds are outlined in their role color, with a soft LED glow.
        glow = glow.filter(ImageFilter.GaussianBlur(radius * 0.3))
        # The glow stays outside the lit holds, so they keep their own color.
        glow.putalpha(ImageChops.multiply(glow.getchannel("A"), ImageChops.invert(lit_mask)))
        out = Image.alpha_composite(out, glow)
        return Image.alpha_composite(out, outlines)

    def hold_use(self, climbs) -> tuple[int, dict[tuple[float, float], int]] | None:
        """How many of the session's climbs use each hold, for the layout with the most
        climbs: (layout id, {(x, y): count}). `climbs` need climb_uuid and is_mirror."""
        layouts: dict[int, dict[tuple[float, float], int]] = {}
        for entry in climbs:
            climb = self.db.climb(entry.climb_uuid)
            if not climb or climb.layout_id is None or not self.layout_image(climb.layout_id):
                continue
            use = layouts.setdefault(climb.layout_id, {})
            for spot in {(hold.x, hold.y) for hold in self.db.holds(climb, mirrored=entry.is_mirror)}:
                use[spot] = use.get(spot, 0) + 1
        if not layouts:
            return None
        return max(layouts.items(), key=lambda item: sum(item[1].values()))

    def render_heatmap(self, layout_id: int, use: dict[tuple[float, float], int], width: int,
                       color: tuple[int, int, int]) -> Image.Image:
        """Only the holds in `use`, each a flat, solid fill of its own shape: `color`
        for a hold used once, darkening with every further use. The scale is fixed
        (see heat_color), so a shade means the same count in every session."""
        image = self.layout_image(layout_id)[0]
        scale = width / image.width
        out = Image.new("RGBA", (width, round(image.height * scale)), (0, 0, 0, 0))
        for (x, y), count in sorted(use.items(), key=lambda item: item[1]):
            shape, (left, top) = self.hold_shape(layout_id, x, y)
            size = (max(1, round(shape.width * scale)), max(1, round(shape.height * scale)))
            out.paste(heat_color(color, count) + (255,), (round(left * scale), round(top * scale)),
                      shape.resize(size, Image.LANCZOS))
        return out

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

    def outline_preview(self, layout_id: int) -> Image.Image | None:
        """Every hold's outline drawn on the layout image at its own size, to check the
        shapes by eye. Colors only tell neighbors apart. To fix one, draw on this image
        in bright cyan and run scripts/import_hold_cuts.py."""
        loaded = self.layout_image(layout_id)
        if not loaded:
            return None
        image = loaded[0]
        out = Image.new("RGBA", image.size, (20, 22, 28, 255))
        out.alpha_composite(image)
        colors = [(255, 60, 60), (60, 160, 255), (60, 230, 90), (255, 80, 230), (255, 200, 40)]
        pad = 4
        for index, (x, y) in enumerate(self.db.layout_hole_positions(layout_id)):
            shape, (left, top) = self.hold_shape(layout_id, x, y)
            local = Image.new("L", (shape.width + pad * 2, shape.height + pad * 2), 0)
            local.paste(shape, (pad, pad))
            line = ImageChops.subtract(local.filter(ImageFilter.MaxFilter(3)), local)
            out.paste(colors[index % len(colors)] + (255,), (left - pad, top - pad), line)
        return out
