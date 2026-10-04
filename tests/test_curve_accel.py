"""Compiled curve strips retain the float reference, bounds and fallback."""
from dataclasses import replace
import os
from pathlib import Path
import subprocess
import sys

import numpy as np
from PIL import Image
import pytest
from scipy.interpolate import PchipInterpolator

from iphoto import color_accel, tone_curves
from iphoto.document import new_layer, raster_mask, render_detail_tile, render_layers
from iphoto.engine import Recipe, render, render_reference


SHAPES = [
    [[0, 0], [64, 85], [128, 128], [192, 192], [255, 255]],
    [[0, 30], [120, 80], [121, 220], [255, 235]],
    [[0, 255], [20, 200], [180, 30], [255, 0]],
    [[0, 30], [40, 180], [80, 10], [130, 120], [200, 120], [255, 255]],
    [[0, 100], [255, 100]],
    [[0, 0], [1, 255], [2, 0], [254, 255], [255, 0]],
    [[0, 0], [64, 64], [128, 128], [255, 255]],
]


@pytest.mark.parametrize("points", SHAPES)
@pytest.mark.parametrize("master", [False, True])
def test_large_noncontiguous_strips_match_every_reference_float(points, master):
    data = np.random.default_rng(161).uniform(-.2, 1.2, (173, 217, 3)).astype(np.float32)
    data[:3, :, :] = np.linspace(0, 1, 217, dtype=np.float32)[None, :, None]
    data[3:7] = np.float32(.5)
    data = data[:, ::2]  # Includes a copy needed for the compiled flat input.
    before = data.copy()
    recipe = Recipe(curve_rgb=points if master else (), curve_red=SHAPES[1],
                    curve_green=points, curve_blue=SHAPES[3])
    actual = tone_curves.apply_fast(data, recipe)
    assert color_accel._curves.signatures, "The compiled kernel must execute"
    assert not tone_curves._accelerator_failed
    assert np.array_equal(actual, tone_curves.apply(data, recipe))
    assert np.array_equal(data, before)


@pytest.mark.parametrize("tone", [False, True])
@pytest.mark.parametrize("points", SHAPES)
def test_hsl_and_curves_final_bytes_match_reference_across_strips(points, tone):
    pixels = np.random.default_rng(143).integers(0, 256, (213, 317, 4), dtype=np.uint8)
    source = Image.fromarray(pixels)
    recipe = Recipe(hsl_red_hue=-179.9, hsl_orange_hue=17.4,
                    hsl_yellow_saturation=99.3, hsl_aqua_lightness=-99.1,
                    hsl_magenta_lightness=99.7, curve_rgb=points,
                    curve_red=SHAPES[0], curve_green=SHAPES[1], curve_blue=SHAPES[3])
    if tone:
        recipe = replace(recipe, contrast=37, exposure=.23, saturation=31, vibrance=62)
    expected = render_reference(source, recipe, strip_height=19)
    assert render(source, recipe, strip_height=37).tobytes() == expected.tobytes()
    assert render(source, recipe, strip_height=91).tobytes() == expected.tobytes()
    assert source.tobytes() == pixels.tobytes()


@pytest.mark.parametrize("points", SHAPES)
def test_mixed_tones_without_hsl_keep_reference_bytes(points):
    pixels = np.random.default_rng(54).integers(0, 256, (213, 317, 4), dtype=np.uint8)
    source = Image.fromarray(pixels)
    recipe = Recipe(curve_rgb=points, curve_blue=SHAPES[1], contrast=53,
                    shadows=25, saturation=-37, skin_smoothing=18)
    assert render(source, recipe, strip_height=37).tobytes() == render_reference(source, recipe).tobytes()


def test_random_up_to_sixteen_knots_keep_exact_reference_floats():
    rng = np.random.default_rng(815)
    data = rng.random((5000, 3)).astype(np.float32)
    for count in range(2, 17):
        x = np.r_[0, np.sort(rng.choice(np.arange(1, 255), count-2, replace=False)), 255]
        points = list(map(list, zip(map(int, x), map(int, rng.integers(0, 256, count)))))
        recipe = Recipe(curve_rgb=points, curve_red=SHAPES[3], curve_blue=points)
        assert np.array_equal(tone_curves.apply_fast(data, recipe), tone_curves.apply(data, recipe))


def test_local_curves_keep_mask_exterior_and_source_pixel_tile_exact():
    pixels = np.random.default_rng(72).integers(0, 256, (373, 417, 3), dtype=np.uint8)
    source = Image.fromarray(pixels)
    recipe = Recipe(hsl_red_hue=171.3, hsl_green_lightness=27, contrast=31,
                    curve_rgb=SHAPES[3], curve_green=SHAPES[1])
    layer = new_layer("Local curves")
    layer['recipe'] = recipe.to_dict()
    layer['mask'].update(feather=.013, ops=[{'kind':'ellipse', 'mode':'add',
                                            'points':[[.17,.23],[.84,.79]]}])
    mask = raster_mask(layer['mask'], source.size)
    expected = Image.composite(render_reference(source, recipe), source, mask)
    actual = render_layers(source, [layer])
    assert actual.tobytes() == expected.tobytes()
    assert np.array_equal(np.asarray(actual)[np.asarray(mask)==0], pixels[np.asarray(mask)==0])
    box = (113, 97, 306, 273)
    assert render_detail_tile(source, [layer], box).tobytes() == expected.crop(box).tobytes()
    assert source.tobytes() == pixels.tobytes()


@pytest.mark.parametrize("points", SHAPES)
def test_analytic_gain_bounds_independent_derivative_and_composed_rounding(points):
    curve = tuple(map(tuple, points))
    derivative = PchipInterpolator(*np.asarray(points).T).derivative()
    measured = np.abs(derivative(np.linspace(0, 255, 20001))).max()
    assert tone_curves._max_slope(curve) >= measured - 1e-12
    recipe = Recipe(curve_rgb=points, curve_green=SHAPES[1])
    window = tone_curves.rounding_window(recipe)
    assert window[1] >= .003*measured*tone_curves._max_slope(tuple(map(tuple, SHAPES[1])))
    assert not window.flags.writeable


def test_failure_retries_once_and_retains_reference(monkeypatch):
    attempts = []
    def fail(*args):
        attempts.append(True)
        raise RuntimeError("Optional compiled curves unavailable")
    monkeypatch.setattr(color_accel, "run_curves", fail)
    monkeypatch.setattr(tone_curves, "_accelerator_failed", False)
    data = np.random.default_rng(3).random((5000, 3)).astype(np.float32)
    recipe = Recipe(curve_blue=SHAPES[0])
    for _ in range(2):
        assert np.array_equal(tone_curves.apply_fast(data, recipe), tone_curves.apply(data, recipe))
    assert attempts == [True]


def test_small_float64_and_empty_curves_avoid_backend(monkeypatch):
    def fail(*args):
        raise AssertionError("Must not compile these inputs")
    monkeypatch.setattr(color_accel, "run_curves", fail)
    monkeypatch.setattr(tone_curves, "_accelerator_failed", False)
    for size, dtype, recipe in [(1000, np.float32, Recipe(curve_blue=SHAPES[0])),
                               (5000, np.float64, Recipe(curve_blue=SHAPES[0])),
                               (5000, np.float32, Recipe())]:
        data = np.random.default_rng(7).random((size, 3)).astype(dtype)
        assert np.array_equal(tone_curves.apply_fast(data, recipe), tone_curves.apply(data, recipe))
    assert not tone_curves._accelerator_failed


def test_packed_cache_is_bounded_immutable_and_shared_between_recipes():
    tone_curves._packed_coefficients.cache_clear()
    for value in range(20):
        points = ((0, 0), (128, value), (255, 255))
        tone_curves._packed_coefficients((points, (), (), ()))
    assert tone_curves._packed_coefficients.cache_info().currsize == 16
    curves = tuple(getattr(Recipe(curve_blue=SHAPES[0]), key) for key in tone_curves.FIELDS)
    packed, counts = tone_curves._packed_coefficients(curves)
    assert packed.shape == (4, 5, 16) and packed.nbytes + counts.nbytes < 3000
    assert not packed.flags.writeable and not counts.flags.writeable
    assert tone_curves._packed_coefficients(curves)[0] is packed


def test_curve_kernel_restores_thread_count_even_on_failure(monkeypatch):
    from numba import get_num_threads, set_num_threads
    previous = get_num_threads()
    coefficients = tone_curves._packed_coefficients(((), (), (), tuple(map(tuple, SHAPES[0]))))
    data = np.zeros((4096, 3), np.float32)
    observed = []
    def fail(*args):
        observed.append(get_num_threads())
        raise RuntimeError("kernel failed")
    try:
        set_num_threads(min(previous, 2))
        current = get_num_threads()
        color_accel.run_curves(data, *coefficients)
        assert get_num_threads() == current
        monkeypatch.setattr(color_accel, "_curves", fail)
        with pytest.raises(RuntimeError):
            color_accel.run_curves(data, *coefficients)
        assert get_num_threads() == current and observed == [min(current, 4)]
    finally:
        set_num_threads(previous)


def test_pure_byte_curves_and_import_keep_compiler_lazy():
    script = ("import sys; from PIL import Image; from iphoto.engine import Recipe, render; "
              "render(Image.new('RGB', (100, 100)), Recipe(curve_blue=[[0,0],[128,140],[255,255]])); "
              "assert 'numba' not in sys.modules")
    environment = dict(os.environ, PYTHONPATH=str(Path(tone_curves.__file__).resolve().parents[1]))
    result = subprocess.run([sys.executable, "-c", script], env=environment, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


def test_disabled_jit_keeps_large_combined_render_available():
    script = ("import sys; from PIL import Image; import numpy as np; "
              "from iphoto.engine import Recipe, render, render_reference; "
              "from iphoto import color_mixer, tone_curves; "
              "image=Image.fromarray(np.random.default_rng(13).integers(0,256,(101,103,3),dtype='uint8')); "
              "recipe=Recipe(hsl_orange_hue=-6, curve_blue=[[0,0],[128,140],[255,255]]); "
              "assert render(image,recipe).tobytes()==render_reference(image,recipe).tobytes(); "
              "assert color_mixer._accelerator_failed and tone_curves._accelerator_failed")
    environment = dict(os.environ, NUMBA_DISABLE_JIT="1", PYTHONPATH=str(Path(tone_curves.__file__).resolve().parents[1]))
    result = subprocess.run([sys.executable, "-c", script], env=environment, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
