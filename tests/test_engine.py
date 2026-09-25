import json
import os
from pathlib import Path
import subprocess
import sys

import numpy as np
from PIL import Image, ImageCms
import pytest

from iphoto.engine import (PRESETS, Recipe, export_image, file_hash, interpret_local,
                           load_source, render, render_reference)


@pytest.fixture
def source_file(tmp_path):
    data = np.random.default_rng(42).integers(0, 255, (73, 119, 3), dtype=np.uint8)
    path = tmp_path / "测试照片.png"
    Image.fromarray(data).save(path)
    return path


@pytest.mark.parametrize("data", [{"exposure": float("nan")}, {"shadows": float("inf")},
                                   {"unknown": 0}, {"warmth": True}, {"exposure": 9}, []])
def test_invalid_recipes_rejected(data):
    with pytest.raises(ValueError): Recipe.from_dict(data)


def test_zero_recipe_preserves_pixels_and_alpha():
    image = Image.fromarray(np.random.default_rng(5).integers(0, 255, (19, 17, 4), dtype=np.uint8))
    assert np.array_equal(np.asarray(render(image, Recipe())), np.asarray(image))
    adjusted = render(image, Recipe(exposure=.5))
    assert np.array_equal(np.asarray(adjusted)[:, :, 3], np.asarray(image)[:, :, 3])


def test_exposure_is_linear_light_not_gamma_multiplication():
    image = Image.new("RGB", (10, 10), (80, 80, 80))
    value = render(image, Recipe(exposure=1)).getpixel((0, 0))[0]
    expected = round((1.055 * (2 * ((80 / 255 + .055) / 1.055) ** 2.4) ** (1 / 2.4) - .055) * 255)
    assert abs(value - expected) <= 1 and value != 160


def test_strip_size_does_not_change_pixels(source_file):
    source = load_source(source_file)
    recipe = Recipe.from_dict(PRESETS["warm"])
    assert np.array_equal(np.asarray(render_reference(source.image, recipe, 7)), np.asarray(render_reference(source.image, recipe, 300)))


@pytest.mark.parametrize("name", list(PRESETS))
def test_lut_agrees_with_float_reference(name):
    image = Image.fromarray(np.random.default_rng(9).integers(0, 256, (200, 300, 3), dtype=np.uint8))
    recipe = Recipe.from_dict(PRESETS[name])
    delta = np.abs(np.asarray(render(image, recipe), dtype=np.int16) - np.asarray(render_reference(image, recipe), dtype=np.int16))
    assert np.mean(delta) < .3
    assert np.quantile(delta, .999) <= 2
    assert np.max(delta) <= 5


def test_exif_orientation_and_srgb(tmp_path):
    path = tmp_path / "rotated.jpg"
    exif = Image.Exif(); exif[274] = 6; exif[271] = "Test camera"
    icc = ImageCms.ImageCmsProfile(ImageCms.createProfile("sRGB")).tobytes()
    Image.new("RGB", (40, 20), (110, 130, 100)).save(path, exif=exif, icc_profile=icc)
    source = load_source(path)
    assert source.image.size == (20, 40)
    target = tmp_path / "export.jpg"
    export_image(source, Recipe(), target)
    with Image.open(target) as result:
        assert result.size == (20, 40)
        assert result.getexif().get(274, 1) == 1
        assert result.getexif().get(271) == "Test camera"
        assert result.info.get("icc_profile")


def test_export_protects_source_and_existing_files(source_file, tmp_path):
    source = load_source(source_file)
    original_hash = file_hash(source_file)
    with pytest.raises(ValueError): export_image(source, Recipe(exposure=.5), source_file)
    target = tmp_path / "export.png"
    export_image(source, Recipe(exposure=.5), target)
    first_hash = file_hash(target)
    with pytest.raises(ValueError): export_image(source, Recipe(exposure=-.5), target)
    assert file_hash(source_file) == original_hash and file_hash(target) == first_hash
    with Image.open(target) as output:
        assert output.size == source.image.size
        assert np.array_equal(np.asarray(output), np.asarray(render(source.image, Recipe(exposure=.5))))


def test_bad_icc_rejected(tmp_path):
    path = tmp_path / "invalid.png"
    Image.new("RGB", (20, 20)).save(path, icc_profile=b"not-a-profile")
    with pytest.raises(ValueError, match="ICC"): load_source(path)


def test_manual_parameter_lock_survives_description():
    recipe, summary = interpret_local("提亮暗部，让色调温暖一点", Recipe(exposure=-.5), ["exposure"])
    assert recipe.exposure == -.5 and recipe.shadows > 0 and recipe.warmth > 0
    assert "关键词" in summary


@pytest.mark.parametrize("text", ["不要提亮", "移除路人并让照片自然", "", "foobar"])
def test_unsupported_text_is_not_silently_applied(text):
    with pytest.raises(ValueError): interpret_local(text, Recipe())


def test_worker_recovers_from_bad_request_and_exports(source_file, tmp_path):
    root = Path(__file__).resolve().parents[1]
    requests = [
        {"id": 1, "op": "open", "path": str(source_file)},
        {"id": 2, "op": "render", "generation": 7, "recipe": {"exposure": 99}},
        {"id": 3, "op": "render", "generation": 8, "recipe": {"shadows": 20}},
        {"id": 4, "op": "export", "recipe": {}, "path": str(tmp_path / "result.png")},
        {"id": 5, "op": "shutdown"},
    ]
    result = subprocess.run([sys.executable, str(root / "run.py"), "--worker", str(tmp_path / "cache")],
                            input="\n".join(json.dumps(x) for x in requests) + "\n", capture_output=True,
                            text=True, encoding="utf-8", timeout=30)
    assert result.returncode == 0, result.stderr
    responses = [json.loads(line) for line in result.stdout.splitlines()]
    assert [r["ok"] for r in responses] == [True, False, True, True]
    assert responses[2]["generation"] == 8
    assert (tmp_path / "result.png").exists()
