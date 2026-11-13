import json

import numpy as np
import pytest
from PIL import Image

from juglogger.graphics.board import ASSETS, BoardRenderer
from juglogger.graphics.calibrate import fit


@pytest.mark.parametrize("name", ["tb2-mirror-12x12", "tb2-spray-12x12"])
def test_layout_images_are_transparent(name):
    image = Image.open(ASSETS / f"{name}.png")
    assert image.mode == "RGBA"
    alpha = np.asarray(image.getchannel("A"))
    h, w = alpha.shape
    for y, x in [(0, 0), (0, w - 1), (h - 1, 0), (h - 1, w - 1)]:
        assert alpha[y, x] == 0
    assert (alpha == 0).mean() > 0.4          # mostly see-through between holds
    rgb = np.asarray(image.convert("RGB"), int)
    assert rgb[alpha == 255].mean() > 120     # light holds, in their real colors
    assert len(json.loads((ASSETS / f"{name}.json").read_text())["holds"]) > 350


def test_calibration_recovers_known_transform():
    centers = np.array(json.loads((ASSETS / "tb2-mirror-12x12.json").read_text())["holds"])[:, :2]
    rng = np.random.default_rng(1)
    # Board holes: the inverse of px = 11.5*x + 820, py = -11.5*y + 1700, with noise,
    # a few holes missing from the image and a few extra image blobs.
    holes = np.column_stack([(centers[:, 0] - 820) / 11.5, (centers[:, 1] - 1700) / -11.5])
    holes += rng.normal(0, 0.4, holes.shape)
    result = fit(holes[10:].tolist(), centers[:-5].tolist())
    assert result.matched > 0.9 * (len(holes) - 15)
    assert abs(result.ax - 11.5) < 0.2 and abs(result.ay + 11.5) < 0.2
    assert result.rms_px < 10


def test_demo_board_lines_up(board):
    renderer = BoardRenderer(board)
    for layout in board.layouts():
        _, calibration, _ = renderer.layout_image(layout.id)
        assert calibration.matched > 0.9 * calibration.total
        assert calibration.rms_px < 12
