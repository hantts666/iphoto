"""Accelerated strips compared against the independent vector reference."""
from dataclasses import replace
import os
from pathlib import Path
import subprocess
import sys

import numpy as np
from PIL import Image
import pytest

from iphoto import color_accel, color_mixer
from iphoto.color_mixer import COLORS, mix, mix_fast
from iphoto.engine import Recipe, render, render_reference


RECIPES = [
    Recipe(hsl_green_hue=20, hsl_green_saturation=-10, hsl_orange_lightness=8),
    Recipe(hsl_red_hue=179, hsl_green_saturation=-100, hsl_blue_lightness=65, hsl_orange_saturation=17),
    Recipe.from_dict({f"hsl_{name}_{field}": value for name, _, _ in COLORS
                      for field, value in (("hue", -180), ("saturation", 100), ("lightness", -33))}),
    Recipe(hsl_red_hue=-179.9, hsl_orange_hue=179.9, hsl_yellow_saturation=99.3,
           hsl_aqua_lightness=-99.1, hsl_magenta_lightness=99.7),
]


@pytest.mark.parametrize("recipe", RECIPES)
def test_compiled_large_strips_keep_reference_bytes_and_source(recipe):
    data = np.random.default_rng(192).random((160, 1024, 3)).astype(np.float32)
    # Include low saturation and near-white/black denominator limits.
    data[:20] = np.linspace(0, 1, 1024, dtype=np.float32)[None, :, None]
    data[20:40, :, 1] = data[20:40, :, 0]
    data[20:40, :, 2] = np.nextafter(data[20:40, :, 0], np.float32(1))
    before = data.copy()
    actual, expected = mix_fast(data, recipe), mix(data, recipe)
    assert color_accel._fused.signatures, "The compiled path must actually execute"
    assert not color_mixer._accelerator_failed
    assert np.array_equal(np.rint(actual * 255), np.rint(expected * 255))
    assert np.max(np.abs(actual - expected)) < 1e-5
    assert np.array_equal(data, before)


@pytest.mark.parametrize("separable", [True, False])
def test_channel_and_nonseparable_hsl_strips_are_exact_with_alpha_and_arbitrary_boundaries(separable):
    data = np.random.default_rng(75).integers(0, 256, (213, 317, 4), dtype=np.uint8)
    image = Image.fromarray(data)
    for recipe in RECIPES:
        recipe = replace(recipe, exposure=1.3, warmth=-27, red_channel=19, blue_channel=-83,
                         **({} if separable else {"vibrance":60, "saturation":60, "highlights":-43, "contrast":37}))
        actual = render(image, recipe, strip_height=37)
        expected = render_reference(image, recipe, strip_height=19)
        assert actual.tobytes() == expected.tobytes()
        assert actual.tobytes() == render(image, recipe, strip_height=91).tobytes()
        assert np.array_equal(np.asarray(actual)[..., 3], data[..., 3])
    assert image.tobytes() == data.tobytes()


def test_accelerator_failure_preserves_reference_and_only_attempts_once(monkeypatch):
    attempts = []
    def fail(*args):
        attempts.append(True)
        raise RuntimeError("compiled backend unavailable")
    monkeypatch.setattr(color_accel, "run", fail)
    monkeypatch.setattr(color_mixer, "_accelerator_failed", False)
    data = np.random.default_rng(28).random((10000, 3)).astype(np.float32)
    recipe = RECIPES[0]
    expected = mix(data, recipe)
    assert np.array_equal(mix_fast(data, recipe), expected)
    assert np.array_equal(mix_fast(data, recipe), expected)
    assert attempts == [True]


def test_small_region_avoids_compiler_and_parallel_pool(monkeypatch):
    def fail(*args):
        raise AssertionError("small region must stay on vector path")
    monkeypatch.setattr(color_accel, "run", fail)
    monkeypatch.setattr(color_mixer, "_accelerator_failed", False)
    data = np.random.default_rng(7).random((1024, 3)).astype(np.float32)
    assert np.array_equal(mix_fast(data, RECIPES[0]), mix(data, RECIPES[0]))
    assert not color_mixer._accelerator_failed


def test_compiler_is_lazy_and_thread_limit_does_not_change_caller():
    script = "import sys; import iphoto.engine; assert 'numba' not in sys.modules"
    environment = dict(os.environ, PYTHONPATH=str(Path(color_mixer.__file__).resolve().parents[1]))
    result = subprocess.run([sys.executable, "-c", script], env=environment, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    from numba import get_num_threads, set_num_threads
    previous = get_num_threads()
    try:
        set_num_threads(min(previous, 2))
        current = get_num_threads()
        color_accel.warm()
        assert get_num_threads() == current
    finally:
        set_num_threads(previous)
