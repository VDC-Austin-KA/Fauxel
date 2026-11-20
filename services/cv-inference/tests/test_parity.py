import numpy as np
import pytest

fx = pytest.importorskip("fauxel_api.vision.model_slot")
ladders = pytest.importorskip("fauxel_api.vision.ladders")

from fauxel_cv import crops
from fauxel_cv.ladders import LADDERS


def test_ladders_match_api():
    assert LADDERS == ladders.LADDERS


def test_crops_match_api():
    rng = np.random.default_rng(0)
    img = rng.integers(0, 255, (256, 512, 3), dtype=np.uint8)
    mask = np.zeros((256, 512), bool)
    mask[100:140, 480:512] = True  # touches the azimuth seam
    mask[100:140, 0:20] = True
    a = fx.crop_for_mask(img, mask, size=96)
    b = crops.crop_for_mask(img, mask, size=96)
    assert a.shape == b.shape == (96, 96, 3)
    assert np.abs(a.astype(int) - b.astype(int)).max() <= 1
