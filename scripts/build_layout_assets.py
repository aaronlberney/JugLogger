"""Turn the TB2 layout renders into transparent hold images.

    python scripts/build_layout_assets.py

Reads juglogger/assets/source/tb2-{mirror,spray}-12x12.jpg (the holds in their real
colors on a black background) and writes juglogger/assets/tb2-{mirror,spray}-12x12.png
plus a .json with the center of every hold, used to line the image up with the board
database.

The output keeps only the holds, in their own colors (gray, yellow, pink), so they
stand out on any photo. The background is fully transparent.
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
BLACK = 16                           # background level; anything brighter is a hold
FEATHER = 40                         # brightness range of the soft edge
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
    rgb = np.asarray(image, np.float32)
    lum = rgb.max(axis=2)

    # The background is black (JPEG noise stays under ~12); every hold is far brighter.
    alpha = np.clip((lum - BLACK) / FEATHER, 0, 1)

    # Bolt holes are dark dots inside a hold: keep them as part of the hold.
    mask = fill_enclosed(alpha > 0.5)
    alpha = np.where(mask, 1.0, alpha)

    min_area = MIN_AREA_FRACTION * mask.size
    keep = np.zeros_like(mask)
    centers = []
    for region in components(mask):
        if len(region) < min_area:
            continue
        ys, xs = region[:, 0], region[:, 1]
        keep[ys, xs] = True
        centers.append((float(xs.mean()), float(ys.mean()), int(len(region))))
    keep_full = np.asarray(Image.fromarray(keep.astype(np.uint8) * 255).filter(ImageFilter.MaxFilter(5))) > 0
    alpha = alpha * keep_full

    # The soft edge pixels are the hold's color blended into black. Take the color
    # from just inside the hold instead, so there is no dark fringe on a bright photo.
    inner = np.asarray(image.filter(ImageFilter.MaxFilter(5)), np.float32)
    edge = (alpha < 1) & ~np.asarray(
        Image.fromarray(mask.astype(np.uint8) * 255).filter(ImageFilter.MinFilter(5))).astype(bool)
    color = np.where(edge[..., None], inner, rgb)
    result = Image.fromarray(np.dstack([color, alpha[..., None] * 255]).astype(np.uint8), "RGBA")

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
        build(ASSETS / "source" / f"{name}.jpg", ASSETS / f"{name}.png", ASSETS / f"{name}.json")
    return 0


if __name__ == "__main__":
    sys.exit(main())
