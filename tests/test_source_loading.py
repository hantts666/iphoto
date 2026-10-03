"""Decoded sources own their pixels and preserve orientation/color/alpha."""

from io import BytesIO

import numpy as np
import pytest
from PIL import Image, ImageCms, ImageOps

from iphoto.engine import SRGB_PROFILE, file_hash, load_source


def texture():
    return Image.fromarray(np.random.default_rng(64).integers(0, 256, (27, 45, 3), dtype=np.uint8))


@pytest.mark.parametrize("orientation", range(1, 9))
def test_all_exif_orientations_are_applied_once_with_owned_pixels(tmp_path, monkeypatch, orientation):
    path = tmp_path / "oriented.png"
    exif = Image.Exif()
    exif[274], exif[271] = orientation, "Test camera"
    texture().save(path, exif=exif)
    digest = file_hash(path)
    original_open = Image.open
    with original_open(path) as decoded:
        expected = ImageOps.exif_transpose(decoded).convert("RGB")
    opened = []

    def track_open(*args, **kwargs):
        image = original_open(*args, **kwargs)
        opened.append(image)
        return image

    monkeypatch.setattr(Image, "open", track_open)
    source = load_source(path)
    assert source.image is not opened[0]
    opened[0].close()
    assert source.image.size == expected.size
    assert source.image.tobytes() == expected.tobytes()
    kept = Image.Exif()
    kept.load(source.exif)
    assert kept[271] == "Test camera" and 274 not in kept
    assert source.digest == digest and file_hash(path) == digest
    # A file handle or lazy decoder must not be needed after the load returns.
    path.unlink()
    source.image.putpixel((0, 0), (1, 2, 3))
    assert source.image.getpixel((0, 0)) == (1, 2, 3)


@pytest.mark.parametrize("kind", ["rgb", "rgb-icc", "rgba", "rgba-icc", "palette-alpha", "rgb-key", "l-key", "cmyk"])
def test_color_alpha_and_file_ownership_match_independent_conversion(tmp_path, kind):
    image = texture()
    options = {}
    path = tmp_path / ("source.jpg" if kind == "cmyk" else "source.png")
    if kind.startswith("rgba"):
        image.putalpha(Image.fromarray(np.arange(27 * 45, dtype=np.uint8).reshape(27, 45)))
    elif kind == "palette-alpha":
        image = image.quantize(colors=16)
        options["transparency"] = bytes(range(0, 256, 16))
    elif kind == "rgb-key":
        options["transparency"] = image.getpixel((0, 0))
    elif kind == "l-key":
        image = image.convert("L")
        options["transparency"] = image.getpixel((0, 0))
    elif kind == "cmyk":
        image = image.convert("CMYK")
    if kind.endswith("icc"):
        options["icc_profile"] = SRGB_PROFILE
    image.save(path, **options)
    digest = file_hash(path)
    with Image.open(path) as decoded:
        upright = ImageOps.exif_transpose(decoded)
        alpha = upright.convert("RGBA").getchannel("A") if "A" in upright.getbands() or "transparency" in decoded.info else None
        if kind.endswith("icc"):
            expected = ImageCms.profileToProfile(upright.convert("RGB"), ImageCms.ImageCmsProfile(BytesIO(SRGB_PROFILE)), ImageCms.createProfile("sRGB"), outputMode="RGB")
        else:
            expected = upright.convert("RGB")
        if alpha is not None:
            expected.putalpha(alpha)
    source = load_source(path)
    assert source.image.mode == expected.mode
    assert source.image.tobytes() == expected.tobytes()
    assert bool(source.warning) == (kind == "cmyk")
    assert source.digest == digest and file_hash(path) == digest
    path.unlink()
    assert source.image.tobytes() == expected.tobytes()
