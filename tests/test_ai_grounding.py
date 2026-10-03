"""Close-up localization keeps crop and full-photo coordinates consistent."""

import base64
from io import BytesIO

import pytest
from PIL import Image, ImageDraw, ImageOps

from iphoto.ai_grounding import crop_pixels, map_grounding, region_crop
from iphoto.ai_protocol import image_data_url
from iphoto.document import empty_mask
from iphoto.engine import Recipe


def test_crop_local_mask_maps_back_without_changing_adjustments():
    region = {"name": "面部", "reason": "磨皮", "recipe": Recipe(skin_smoothing=30).to_dict(),
              "mask": {**empty_mask(), "label": "面部", "ops": [
                  {"kind": "rect", "mode": "add", "points": [[.3, .2], [.5, .4]]}
              ]}}
    size = (600, 420)
    crop = region_crop(region["mask"], size)
    left, top, right, bottom = crop_pixels(crop, size)
    selected = {"mask": {**empty_mask(), "ops": [
        {"kind": "polygon", "mode": "add", "points": [[.2, .3], [.8, .3], [.8, .9]]}
    ]}, "anchor": [.6, .5]}
    mapped = map_grounding(selected, crop, size, region)
    assert mapped["recipe"] == region["recipe"]
    assert mapped["mask"]["label"] == "面部"
    assert mapped["anchor"] == pytest.approx([(left + .6 * (right-left))/600,
                                              (top + .5 * (bottom-top))/420])
    assert mapped["mask"]["ops"][0]["points"][0] == pytest.approx(
        [(left + .2 * (right-left))/600, (top + .3 * (bottom-top))/420])
    assert selected["anchor"] == [.6, .5]


@pytest.mark.parametrize("crop", [[0, 0, 0, 1], [-.1, 0, .5, 1], [0, 0, float('nan'), 1]])
def test_invalid_crop_is_rejected(crop):
    with pytest.raises(ValueError):
        crop_pixels(crop, (600, 420))


def test_photo_orientation_and_crop_match_the_displayed_photo(tmp_path):
    source = Image.new("RGB", (120, 80), (35, 70, 100))
    ImageDraw.Draw(source).rectangle((0, 0, 59, 79), fill=(210, 80, 45))
    exif = Image.Exif()
    exif[274] = 8
    path = tmp_path / "rotated.jpg"
    source.save(path, exif=exif, quality=95)
    box = [.1, .2, .8, .7]
    with Image.open(path) as opened:
        upright = ImageOps.exif_transpose(opened)
        expected = upright.crop(crop_pixels(box, upright.size))
    encoded = image_data_url(path, box).split(",", 1)[1]
    with Image.open(BytesIO(base64.b64decode(encoded))) as actual:
        assert actual.size == expected.size == (56, 60)
        assert not actual.getexif()
        assert actual.getpixel((28, 10)) == pytest.approx(expected.getpixel((28, 10)), abs=4)
