"""Save hand-drawn fixes to the hold outlines.

    python scripts/import_hold_cuts.py traced.png spray      (or: mirror)

`traced.png` is data/outlines-<layout>.png (written by `python -m juglogger doctor`)
with bright cyan lines drawn where two touching holds should be separated. It must
be the same size as the original. The cyan lines are saved next to the layout image
as tb2-<layout>-12x12-cuts.png; the board graphic treats them as gaps between holds.
Lines drawn along a hold's outer edge are harmless. Importing again replaces the
earlier fixes for that layout, so keep drawing on the same traced file.
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
from PIL import Image

ASSETS = Path(__file__).resolve().parent.parent / "juglogger" / "assets"


def main() -> int:
    if len(sys.argv) != 3 or sys.argv[2] not in ("mirror", "spray"):
        print(__doc__)
        return 1
    name = f"tb2-{sys.argv[2]}-12x12"
    traced = np.asarray(Image.open(sys.argv[1]).convert("RGB"), int)
    size = Image.open(ASSETS / f"{name}.png").size
    if traced.shape[:2] != (size[1], size[0]):
        print(f"The traced image is {traced.shape[1]}x{traced.shape[0]}, but the layout image is {size[0]}x{size[1]}.")
        return 1
    r, g, b = traced[..., 0], traced[..., 1], traced[..., 2]
    cyan = (r < 120) & (g > 180) & (b > 180)
    Image.fromarray((cyan * 255).astype(np.uint8)).save(ASSETS / f"{name}-cuts.png", optimize=True)
    print(f"{name}-cuts.png: {int(cyan.sum())} pixels of cut lines")
    return 0


if __name__ == "__main__":
    sys.exit(main())
