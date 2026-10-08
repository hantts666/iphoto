"""Protocol invariants for the research path; no claims about photo quality."""
import importlib.util
from pathlib import Path

import numpy as np
import pytest

spec = importlib.util.spec_from_file_location(
    'learned_trimap_benchmark', Path(__file__).parents[1] / 'scripts/benchmark_learned_trimap.py')
benchmark = importlib.util.module_from_spec(spec)
spec.loader.exec_module(benchmark)


def test_unknown_prompt_is_distinct_from_opaque_foreground():
    coordinates, labels = benchmark.prompts([[400, 200, 2], [200, 100, 1], [0, 0, 0]], (800, 400))
    assert coordinates.shape == (1, 5, 2)
    np.testing.assert_array_equal(coordinates[0, 2:], [[512, 512], [256, 256], [0, 0]])
    assert labels.tolist() == [[2, 3, 4, 1, 0]]


def test_empty_prompts_keep_single_author_padding_token():
    coordinates, labels = benchmark.prompts([], (800, 400))
    assert coordinates.shape == (1, 3, 2) and labels.tolist() == [[2, 3, -1]]


@pytest.mark.parametrize('points', [None, [[1, 1, True]], [[True, 1, 1]],
                                   [[float('nan'), 1, 1]], [[0, float('inf'), 0]],
                                   [[800, 1, 1]], [[1, 400, 1]], [[-1, 1, 0]],
                                   [[1, 1, 4]], [[1, 1, 1]] * 9, [[1, 1]]])
def test_bad_source_prompts_fail(points):
    with pytest.raises(ValueError):
        benchmark.prompts(points, (800, 400))


def test_native_classes_preserve_unknown_and_native_dimensions():
    probabilities = np.zeros((1, 3, 256, 256), np.float32)
    probabilities[:, 0, :, :80] = 1
    probabilities[:, 1, :, 80:160] = 1
    probabilities[:, 2, :, 160:] = 1
    result = benchmark.native_trimap(probabilities, (901, 879))
    assert result.shape == (879, 901) and result.dtype == np.uint8
    assert (result[400, 0], result[400, 400], result[400, 800]) == (0, 128, 255)


@pytest.mark.parametrize('failure', ['shape', 'nan', 'range', 'sum', 'dtype'])
def test_invalid_probabilities_fail(failure):
    value = np.full((1, 3, 256, 256), 1 / 3, np.float32)
    if failure == 'shape':
        value = value[:, :, :255]
    elif failure == 'nan':
        value[0, 0, 0, 0] = np.nan
    elif failure == 'range':
        value[0, 0, 0, 0] = -0.1
    elif failure == 'sum':
        value[0, 0, 0, 0] = 0
    else:
        value = value.astype(np.float64)
    with pytest.raises(ValueError):
        benchmark.native_trimap(value, (901, 879))
