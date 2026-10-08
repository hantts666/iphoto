"""Distant real anchors remain available without unbounded full-photo solves."""
import numpy as np
from PIL import Image
import pytest

from iphoto.matting import solver


def distant_reference_scene():
    truth = np.tile(np.clip((np.arange(768)-16)/688, 0, 1), (48, 1))
    # A continuous transparent surface whose only opaque anchor is several
    # tiles away. RGB quantization is included in the optical regression.
    pixels = truth[..., None]*np.array([40., 30., 20.]) + (1-truth[..., None])*np.array([140., 130., 120.])
    guide = np.full(truth.shape, .5)
    guide[:, :16] = 0
    guide[:, 704:] = 1
    return Image.fromarray(np.rint(pixels).astype('uint8')), guide, truth


def test_real_remote_references_recover_alpha_without_artificial_tile_constraints():
    image, guide, truth = distant_reference_scene()
    before, old_guide = image.tobytes(), guide.copy()
    alpha, tiles = solver.solve_alpha(image, guide, byte_output=True, linear=False)
    unknown = guide == .5
    assert tiles == 1 and np.abs(alpha[unknown]/255-truth[unknown]).mean() < .01
    assert np.all(alpha[guide == 0] == 0) and np.all(alpha[guide == 1] == 255)
    # One source RGB byte corresponds to 2.55 alpha bytes in this scene;
    # changes at the core seams must stay within that quantized gradient.
    for seam in (256, 512):
        assert np.max(np.abs(alpha[:, seam].astype(int)-alpha[:, seam-1])) <= 3
    assert image.tobytes() == before and np.array_equal(guide, old_guide)


def test_existing_nearby_references_keep_the_original_context():
    guide = np.full((600, 600), .5)
    guide[:8] = 0
    guide[300:308] = 1
    assert solver._reference_box(guide, (0, 0, 256, 256)) == ((0, 0, 320, 320), False)


def test_working_local_references_keep_the_original_solver_budget(monkeypatch):
    import pymatting
    guide = np.full((96, 768), .5)
    guide[:16] = 0
    guide[-16:] = 1
    fills, shapes = [], []
    def predict(_rgb, local, **kwargs):
        shapes.append(local.shape)
        assert kwargs['cg_kwargs']['maxiter'] == 250
        kwargs['preconditioner'](None)
        return np.full(local.shape, .4)
    monkeypatch.setattr(pymatting, 'estimate_alpha_cf', predict)
    monkeypatch.setattr(pymatting, 'ichol', lambda _matrix, *, max_nnz: fills.append(max_nnz))
    _, tiles = solver.solve_alpha(Image.new('RGB', (768, 96)), guide)
    assert tiles == 3 and shapes == [(96, 320), (96, 384), (96, 320)]
    assert fills == [4_000_000]*3


def test_whole_photo_reference_probe_does_not_allocate_full_photo_boolean_arrays():
    class LargeTrimap:
        shape = (6000, 10000)
        def __eq__(self, _other):
            pytest.fail('A full-photo boolean array was allocated')
    assert solver._joint_references(LargeTrimap(), 256) is False


def test_missing_real_reference_fails_without_inference(monkeypatch):
    import pymatting
    monkeypatch.setattr(pymatting, 'estimate_alpha_cf', lambda *_a, **_k: pytest.fail('No real reference'))
    guide = np.full((48, 1800), .5)
    guide[:, :16] = 0
    guide[:, -16:] = 1
    with pytest.raises(ValueError, match='缺少前景或背景参照'):
        solver.solve_alpha(Image.new('RGB', (1800, 48)), guide)


def test_expansion_bounds_unknown_pixels_and_reference_area():
    guide = np.full((300, 768), .5)
    guide[:, :16] = 0
    guide[:, 704:] = 1
    with pytest.raises(ValueError, match='透明区域过大'):
        solver._reference_box(guide, (0, 0, 256, 256))
    guide = np.full((1800, 1800), .5)
    guide[500:510, 100:110] = 0
    guide[500:510, 1150:1160] = 1
    with pytest.raises(ValueError, match='缺少前景或背景参照'):
        solver._reference_box(guide, (500, 500, 756, 756))


def test_expanded_solver_keeps_hard_anchors_and_uses_a_bounded_preconditioner(monkeypatch):
    import pymatting
    image, guide, _ = distant_reference_scene()
    fills, calls = [], []
    def preconditioner(_matrix, *, max_nnz):
        fills.append(max_nnz)
    def predict(rgb, local, **kwargs):
        calls.append((rgb.shape, local.shape, kwargs['cg_kwargs']['maxiter']))
        kwargs['preconditioner'](None)
        return np.full(local.shape, .63)
    monkeypatch.setattr(pymatting, 'estimate_alpha_cf', predict)
    monkeypatch.setattr(pymatting, 'ichol', preconditioner)
    alpha, _ = solver.solve_alpha(image, guide, byte_output=True, linear=False)
    assert fills == [solver.MAX_REFERENCE_FILL]
    assert all(c[2] == 500 and c[0][:2] == c[1] for c in calls)
    assert np.all(alpha[guide == .5] == 161)
    assert np.all(alpha[guide == 0] == 0) and np.all(alpha[guide == 1] == 255)


@pytest.mark.parametrize('failure', ['nonfinite', 'shape', 'fill_budget'])
def test_invalid_reference_solve_does_not_return_a_partial_result(failure, monkeypatch):
    import pymatting
    image, guide, _ = distant_reference_scene()
    before = guide.copy()
    def predict(_rgb, local, **_kwargs):
        if failure == 'fill_budget':
            raise ValueError('Bounded allocation failed')
        return np.full((1, 1) if failure == 'shape' else local.shape, np.nan if failure == 'nonfinite' else .5)
    monkeypatch.setattr(pymatting, 'estimate_alpha_cf', predict)
    with pytest.raises(ValueError, match='原选区保留'):
        solver.solve_alpha(image, guide, byte_output=True, linear=False)
    assert np.array_equal(guide, before)
