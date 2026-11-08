"""Line up board coordinates (hole x/y from the database) with the bundled layout
images, by matching database holes to the hold centers detected in each image.

The fit is a scale and offset per axis (the images are straight-on and unrotated):
    px = ax * x + bx,   py = ay * y + by
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np


@dataclass(frozen=True)
class Fit:
    ax: float
    bx: float
    ay: float
    by: float
    matched: int          # holes matched to a hold in the image
    total: int            # holes in the layout
    rms_px: float         # average distance between a hole and its matched hold

    def to_px(self, x: float, y: float) -> tuple[float, float]:
        return self.ax * x + self.bx, self.ay * y + self.by

    @property
    def px_per_unit(self) -> float:
        return abs(self.ax)


def fit(holes: list[tuple[float, float]], centers: list[tuple[float, float]], iterations: int = 30) -> Fit:
    h = np.asarray(holes, float)
    c = np.asarray(centers, float)

    # Start by lining up the bounding boxes (board y points up, image y points down).
    (x0, y0), (x1, y1) = h.min(0), h.max(0)
    (px0, py0), (px1, py1) = c.min(0), c.max(0)
    ax = (px1 - px0) / (x1 - x0)
    ay = -(py1 - py0) / (y1 - y0)
    bx, by = px0 - ax * x0, py0 - ay * y1

    spacing = median_spacing(c)
    threshold = spacing * 0.9
    matched_idx = np.array([], int)
    for _ in range(iterations):
        mapped = np.column_stack([ax * h[:, 0] + bx, ay * h[:, 1] + by])
        d = np.linalg.norm(mapped[:, None, :] - c[None, :, :], axis=2)
        nearest = d.argmin(1)
        back = d.argmin(0)
        mutual = back[nearest] == np.arange(len(h))
        close = d[np.arange(len(h)), nearest] < threshold
        matched_idx = np.nonzero(mutual & close)[0]
        if len(matched_idx) < 4:
            break
        hx, hy = h[matched_idx, 0], h[matched_idx, 1]
        cx, cy = c[nearest[matched_idx], 0], c[nearest[matched_idx], 1]
        ax, bx = np.polyfit(hx, cx, 1)
        ay, by = np.polyfit(hy, cy, 1)
        threshold = max(spacing * 0.35, threshold * 0.85)

    mapped = np.column_stack([ax * h[:, 0] + bx, ay * h[:, 1] + by])
    d = np.linalg.norm(mapped[:, None, :] - c[None, :, :], axis=2).min(1)
    rms = float(np.sqrt(np.mean(d[matched_idx] ** 2))) if len(matched_idx) else float("inf")
    return Fit(float(ax), float(bx), float(ay), float(by), int(len(matched_idx)), len(h), rms)


def median_spacing(points: np.ndarray) -> float:
    d = np.linalg.norm(points[:, None, :] - points[None, :, :], axis=2)
    np.fill_diagonal(d, np.inf)
    return float(np.median(d.min(1)))
