"""Guard against misleading quality averages and mismatched native references."""
import importlib.util
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

spec = importlib.util.spec_from_file_location('alpha_truth_benchmark', Path(__file__).parents[1] / 'scripts/benchmark_alpha_truth.py')
benchmark = importlib.util.module_from_spec(spec)
spec.loader.exec_module(benchmark)


def test_low_opacity_loss_is_visible_despite_small_whole_image_error():
    truth = np.zeros((100, 100), np.uint8)
    truth[50, 10:90] = 32
    trimap = np.zeros_like(truth)
    trimap[50, 10:90] = 128
    result = benchmark.measure(np.zeros_like(truth), truth, trimap)
    assert result['unknown_mae'] == pytest.approx(32/255)
    assert result['strata']['low_alpha']['pixels'] == 80
    assert result['strata']['low_alpha']['signed_bias'] == pytest.approx(-32/255)
    assert result['strata']['middle_alpha']['mae'] is None


def test_exact_unknown_prediction_still_reports_broken_hard_constraints():
    truth = np.array([[0, 64, 255]], np.uint8)
    trimap = np.array([[0, 128, 255]], np.uint8)
    predicted = np.array([[128, 64, 128]], np.uint8)
    result = benchmark.measure(predicted, truth, trimap)
    assert result['unknown_mae'] == 0
    assert result['known_constraint_violations'] == 2


@pytest.mark.parametrize('predicted,truth,trimap', [
    (np.zeros((2, 3), np.uint8), np.zeros((3, 2), np.uint8), np.full((3, 2), 128, np.uint8)),
    (np.zeros((2, 2), float), np.zeros((2, 2), np.uint8), np.full((2, 2), 128, np.uint8)),
    (np.zeros((2, 2), np.uint8), np.zeros((2, 2), np.uint8), np.full((2, 2), 127, np.uint8)),
    (np.zeros((2, 2), np.uint8), np.zeros((2, 2), np.uint8), np.zeros((2, 2), np.uint8)),
])
def test_invalid_or_empty_reference_is_rejected(predicted, truth, trimap):
    with pytest.raises(ValueError):
        benchmark.measure(predicted, truth, trimap)


def test_exact_gray_rgb_and_palette_preserve_reference_bytes(tmp_path):
    values = np.array([[0, 64, 128, 255]], np.uint8)
    for mode in ('RGB', 'P'):
        path = tmp_path / (mode+'.png')
        picture = Image.fromarray(values).convert(mode)
        picture.save(path)
        assert np.array_equal(benchmark.read_alpha(path), values)


def test_color_is_rejected_instead_of_silently_converted_to_gray(tmp_path):
    path = tmp_path / 'color.png'
    Image.new('RGB', (2, 2), (128, 64, 32)).save(path)
    with pytest.raises(ValueError, match='contains color'):
        benchmark.read_alpha(path)
