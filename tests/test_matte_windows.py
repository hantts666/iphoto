"""Native inference must see real known references without moving output pixels."""
import cv2
import numpy as np
from PIL import Image
import pytest

from iphoto.matting import neural
from iphoto.matting.windows import context_box


@pytest.mark.parametrize('inverted', [False, True])
def test_missing_reference_moves_input_but_preserves_source_coordinates_and_known_alpha(inverted):
    rows, columns = np.indices((900, 900))
    values = ((columns+3*rows) % 256).astype(np.uint8)
    image = Image.fromarray(np.stack((values, values, values), axis=2))
    before = image.tobytes()
    guide = np.zeros(values.shape, np.uint8)
    guide[450:700, 200:510] = 128
    # Outside the first core's original 576px halo, but reachable at native size.
    guide[620:650, 620:650] = 255
    if inverted:
        guide = 255-guide
        guide[guide == 127] = 128
    snapshot = guide.copy()

    class ReferenceEngine:
        calls = 0

        def predict(self, pixels):
            self.calls += 1
            assert pixels.shape == (1, 4, 640, 640)
            # Official zero padding is not a background observation from Source.
            valid = (pixels[0, :3] != 0).any(axis=0)
            assert ((pixels[0, 3] == (0 if inverted else 1)) & valid).sum() >= 81
            return (pixels[0, 0]+1)/2

    engine = ReferenceEngine()
    result, tiles = neural.solve(image, guide, engine=engine)
    expected = np.where(guide == 128, values, guide).astype(np.uint8)
    # This checks unknown pixels even when the shifted input begins inside a core;
    # a whole-core slice would have negative offsets or place predictions wrongly.
    assert np.array_equal(result, expected) and tiles == engine.calls == 4
    assert image.tobytes() == before and np.array_equal(guide, snapshot)


def test_sufficient_references_keep_the_original_halo():
    guide = np.zeros((900, 900), np.uint8)
    guide[300:450, 300:450] = 128
    guide[460:500, 460:500] = 255
    assert context_box(guide, (0, 0, 512, 512), (300, 300, 450, 450),
                       limit=640, halo=64) == (0, 0, 576, 576)


@pytest.mark.parametrize('case', ['absent', 'isolated', 'distant', 'no_background'])
def test_unavailable_references_do_not_invent_constraints_or_move_the_window(case):
    guide = np.zeros((1200, 1200), np.uint8)
    guide[100:200, 100:200] = 128
    if case == 'isolated':
        guide[600, 600] = 255
    elif case == 'distant':
        guide[1000:1100, 1000:1100] = 255
    elif case == 'no_background':
        guide.fill(255)
        guide[100:200, 100:200] = 128
    before = guide.copy()
    assert context_box(guide, (0, 0, 512, 512), (100, 100, 200, 200),
                       limit=640, halo=64) == (0, 0, 576, 576)
    assert np.array_equal(guide, before)


@pytest.mark.parametrize('transpose', [False, True])
def test_narrow_source_and_border_core_can_recover_a_reference_without_resizing(transpose):
    guide = np.zeros((900, 80), np.uint8)
    guide[650:700, 20:60] = 128
    guide[285:310, 20:60] = 255
    core, active = (0, 384, 80, 896), (20, 650, 60, 700)
    if transpose:
        guide = guide.T.copy()
        core, active = (384, 0, 896, 80), (650, 20, 700, 60)
    box = context_box(guide, core, active, limit=640, halo=64)
    left, top, right, bottom = box
    assert left <= active[0] < active[2] <= right
    assert top <= active[1] < active[3] <= bottom
    assert right-left <= 640 and bottom-top <= 640
    assert (guide[top:bottom, left:right] == 255).sum() >= 81
    assert (guide[top:bottom, left:right] == 0).any()


def test_reference_search_on_a_60mp_guide_remains_bounded(monkeypatch):
    guide = np.zeros((7500, 8000), np.uint8)
    guide[3000, 3000] = 128
    guide[3500:3540, 3500:3540] = 255
    sizes = []
    erode = cv2.erode

    def record(area, *args, **kwargs):
        sizes.append(area.shape)
        assert max(area.shape) <= 1279
        return erode(area, *args, **kwargs)

    monkeypatch.setattr(cv2, 'erode', record)
    left, top, right, bottom = context_box(guide, (2688, 2688, 3200, 3200),
                                           (3000, 3000, 3001, 3001), limit=640, halo=64)
    assert left <= 3000 < right and top <= 3000 < bottom
    assert right-left <= 640 and bottom-top <= 640
    assert (guide[top:bottom, left:right] == 255).sum() >= 81
    assert sizes and max(height*width for height, width in sizes) <= 1279**2
