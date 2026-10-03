"""Detail filters may read the original but must return independently editable pixels."""

import numpy as np
from PIL import Image
import pytest

from iphoto.engine import Recipe, render


@pytest.mark.parametrize("mode", ["RGB", "RGBA"])
@pytest.mark.parametrize("field", ["skin_smoothing", "softness", "sharpness"])
def test_detail_only_result_does_not_mutate_or_share_original(mode, field):
    channels = 4 if mode == "RGBA" else 3
    pixels = np.random.default_rng(81).integers(0, 256, (143, 177, channels), dtype="uint8")
    source = Image.fromarray(pixels)
    before = source.tobytes()
    result = render(source, Recipe(**{field: 40}))
    assert result.mode == source.mode and result.size == source.size
    assert result.tobytes() != before
    assert source.tobytes() == before
    if mode == "RGBA":
        assert result.getchannel("A").tobytes() == source.getchannel("A").tobytes()
    result.putpixel((0, 0), (0,) * channels)
    assert source.tobytes() == before
