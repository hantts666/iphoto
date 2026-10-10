"""One connected transparent surface must not acquire independent tile edges."""
import numpy as np
from PIL import Image
import pytest

from iphoto.matting import solver


def surface_scene():
    y, x = np.mgrid[:400, :400]
    truth = np.clip(.5 + .65*np.sin(x/17)*np.cos(y/19), 0, 1)
    distance = np.minimum.reduce([x,y,399-x,399-y])
    truth *= np.clip((distance-3)/20,0,1)
    guide = np.full(truth.shape, .5, dtype=np.float32)
    guide[truth == 0], guide[truth == 1] = 0, 1
    # Neutral foreground/background keep quantized RGB exactly on one color
    # line, so this fixture measures spatial solve consistency rather than
    # the color-line model's response to independent channel quantization.
    rgb = np.repeat(np.rint((1-truth)[...,None]*255),3,axis=2)
    return Image.fromarray(np.rint(rgb).astype('uint8')), guide, truth


def test_broad_surface_shares_anchors_even_when_every_local_window_has_both_classes():
    image, guide, truth = surface_scene()
    assert np.count_nonzero(guide == .5) > 256**2
    for y in (0, 256):
        for x in (0, 256):
            local = guide[max(0,y-64):min(400,y+320),max(0,x-64):min(400,x+320)]
            assert np.any(local == 0) and np.any(local == 1)
    old_image, old_guide = image.tobytes(), guide.copy()
    result, tiles = solver.solve_alpha(image, guide, byte_output=True, linear=False)
    whole, _ = solver.solve_alpha(image, guide, tile_size=1024, byte_output=True, linear=False)
    assert tiles == 1
    assert np.max(np.abs(result.astype(int)-whole.astype(int))) <= 1
    assert np.abs(result/255-truth)[guide == .5].mean() < .025
    assert np.array_equal(result[guide != .5], np.uint8(guide[guide != .5]*255))
    assert image.tobytes() == old_image and np.array_equal(guide, old_guide)


def test_large_photo_uses_only_the_bounded_native_surface_window():
    patch, guide, truth = surface_scene()
    image = Image.new('RGB', (1800,1400), 'white')
    image.paste(patch,(810,470))
    full_guide = np.zeros((1400,1800),np.float32)
    full_guide[470:870,810:1210] = guide
    box = solver._joint_window(full_guide,256)
    assert box == (750,410,1270,930)
    result, tiles = solver.solve_alpha(image,full_guide,byte_output=True,linear=False)
    assert tiles == 1
    assert np.array_equal(result[full_guide != .5], np.uint8(full_guide[full_guide != .5]*255))
    assert np.abs(result[470:870,810:1210]/255-truth)[guide == .5].mean() < .025


def test_disconnected_small_unknowns_keep_the_working_local_solver():
    guide = np.zeros((400,400),np.float32)
    guide[::2,::2] = 1
    guide[1::2,1::2] = .5
    assert solver._joint_window(guide,256) is None


@pytest.mark.parametrize('shape,unknown_box', [
    ((1400,1800),(100,100,600,600)),
    ((1400,1800),(100,100,1400,120)),
])
def test_joint_window_never_exceeds_unknown_or_spatial_budget(shape,unknown_box):
    guide = np.zeros(shape,np.float32)
    x,y,x1,y1 = unknown_box
    guide[y:y1,x:x1] = .5
    assert solver._joint_window(guide,256) is None


def test_large_canvas_scan_does_not_allocate_whole_photo_labels():
    class LargeCanvas:
        shape = (6000,10000)
        def __eq__(self,_):
            pytest.fail('Whole photo comparison')
        def __getitem__(self,region):
            # Dense unknowns exhaust the fixed budget in the first strip.
            assert isinstance(region,slice) and region.stop-region.start <= 256
            return np.full((region.stop-region.start,10000),.5,np.float32)
    assert solver._joint_window(LargeCanvas(),256) is None
