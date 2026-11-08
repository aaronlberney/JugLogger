"""Turn the TB2 layout graphics into colorless, transparent hold images.

    python scripts/build_layout_assets.py

Reads juglogger/assets/source/tb2-{mirror,spray}-12x12.webp (holds on a gray panel
with a title) and writes juglogger/assets/tb2-{mirror,spray}-12x12.png plus a .json
with the center of every hold, used to line the image up with the board database.

The output keeps only the holds: alpha is the hold shape, RGB is a grayscale shade
(black holds come out darker than the wood/cream ones, so the board keeps its
texture). The background is fully transparent.
"""

from __future__ import annotations

import json
import sys
from collections import deque
from pathlib import Path

import numpy as np
from PIL import Image, ImageFilter

ROOT = Path(__file__).resolve().parent.parent
ASSETS = ROOT / "juglogger" / "assets"
CROP = (0.05, 0.05, 0.955, 0.955)   # inside the panel, below the title
MIN_AREA_FRACTION = 0.00004          # specks smaller than this are dropped


def fill_enclosed(mask: np.ndarray) -> np.ndarray:
    """Fill gaps fully enclosed by a hold (gloss highlights on black holds): any
    non-hold pixel that can't reach the image edge becomes hold."""
    h, w = mask.shape
    outside = np.zeros_like(mask, dtype=bool)
    queue = deque()
    for y in range(h):
        for x in (0, w - 1):
            if not mask[y, x] and not outside[y, x]:
                outside[y, x] = True
                queue.append((y, x))
    for x in range(w):
        for y in (0, h - 1):
            if not mask[y, x] and not outside[y, x]:
                outside[y, x] = True
                queue.append((y, x))
    while queue:
        y, x = queue.popleft()
        for ny, nx in ((y + 1, x), (y - 1, x), (y, x + 1), (y, x - 1)):
            if 0 <= ny < h and 0 <= nx < w and not mask[ny, nx] and not outside[ny, nx]:
                outside[ny, nx] = True
                queue.append((ny, nx))
    return ~outside


def components(mask: np.ndarray) -> list[np.ndarray]:
    """Connected regions of a boolean mask, as arrays of (y, x)."""
    h, w = mask.shape
    seen = np.zeros_like(mask, dtype=bool)
    regions = []
    ys, xs = np.nonzero(mask)
    for y0, x0 in zip(ys, xs):
        if seen[y0, x0]:
            continue
        queue, pixels = deque([(y0, x0)]), []
        seen[y0, x0] = True
        while queue:
            y, x = queue.popleft()
            pixels.append((y, x))
            for ny, nx in ((y + 1, x), (y - 1, x), (y, x + 1), (y, x - 1)):
                if 0 <= ny < h and 0 <= nx < w and mask[ny, nx] and not seen[ny, nx]:
                    seen[ny, nx] = True
                    queue.append((ny, nx))
        regions.append(np.array(pixels))
    return regions


def build(source: Path, out_png: Path, out_json: Path) -> None:
    image = Image.open(source).convert("RGB")
    w, h = image.size
    image = image.crop((round(CROP[0] * w), round(CROP[1] * h), round(CROP[2] * w), round(CROP[3] * h)))
    rgb = np.asarray(image, np.float32)
    lum = rgb @ np.array([0.299, 0.587, 0.114], np.float32)
    chroma = rgb.max(axis=2) - rgb.min(axis=2)

    # The panel is neutral gray between ~100 and ~145 everywhere (watermark and
    # vignette included). A hold pixel is much darker, much brighter, or tinted.
    alpha = np.clip(np.maximum.reduce([
        (88 - lum) / 18,       # black holds
        (lum - 158) / 18,      # wood / cream holds
        (chroma - 9) / 8,      # anything with color
    ]), 0, 1)

    # Close pinholes (bolt holes) and soften the edge a touch.
    a_img = Image.fromarray((alpha * 255).astype(np.uint8))
    a_img = a_img.filter(ImageFilter.MaxFilter(5)).filter(ImageFilter.MinFilter(5))
    alpha = np.asarray(a_img, np.float32) / 255

    # Find the holds; drop specks and anything touching the crop edge (panel border).
    small = a_img.resize((a_img.width // 2, a_img.height // 2), Image.BILINEAR)
    mask_small = fill_enclosed(np.asarray(small) > 110)
    filled = np.asarray(Image.fromarray(mask_small.astype(np.uint8) * 255).resize(a_img.size, Image.BILINEAR),
                        np.float32) / 255
    alpha = np.maximum(alpha, filled * (filled > 0.5))
    min_area = MIN_AREA_FRACTION * mask_small.size
    keep = np.zeros_like(mask_small)
    centers = []
    for region in components(mask_small):
        ys, xs = region[:, 0], region[:, 1]
        touches_edge = ys.min() == 0 or xs.min() == 0 or ys.max() == mask_small.shape[0] - 1 \
            or xs.max() == mask_small.shape[1] - 1
        if len(region) < min_area or touches_edge:
            continue
        keep[ys, xs] = True
        centers.append((float(xs.mean() * 2 + 1), float(ys.mean() * 2 + 1), int(len(region) * 4)))
    keep_full = np.asarray(Image.fromarray(keep.astype(np.uint8) * 255).resize(a_img.size, Image.NEAREST)
                           .filter(ImageFilter.MaxFilter(5))) > 0
    alpha = alpha * keep_full

    # Colorless: grayscale shade, normalized so black holds are mid-gray and the
    # lightest holds are near white. Theme color is applied when rendering.
    shade = np.clip((lum - 20) / 200, 0, 1) * 0.55 + 0.45
    out = np.dstack([shade * 255] * 3 + [alpha * 255]).astype(np.uint8)
    result = Image.fromarray(out, "RGBA")

    # Trim to the holds with a small margin and shift the centers to match.
    left, top, right, bottom = result.getchannel("A").getbbox()
    margin = 12
    left, top = max(0, left - margin), max(0, top - margin)
    right, bottom = min(result.width, right + margin), min(result.height, bottom + margin)
    result = result.crop((left, top, right, bottom))
    centers = [(x - left, y - top, area) for x, y, area in centers]

    out_png.parent.mkdir(parents=True, exist_ok=True)
    result.save(out_png, optimize=True)
    out_json.write_text(json.dumps({
        "source": source.name,
        "size": list(result.size),
        "holds": [[round(x, 1), round(y, 1), area] for x, y, area in centers],
    }))
    print(f"{out_png.name}: {result.size[0]}x{result.size[1]}, {len(centers)} holds")


def main() -> int:
    for name in ("tb2-mirror-12x12", "tb2-spray-12x12"):
        build(ASSETS / "source" / f"{name}.webp", ASSETS / f"{name}.png", ASSETS / f"{name}.json")
    return 0


if __name__ == "__main__":
    sys.exit(main())
