"""Quality evidence must inspect real edges, not an opaque canvas frame."""
import numpy as np
from PIL import Image
import pytest

from iphoto.matte_review import _internal_edges, edge_boxes


@pytest.mark.parametrize('size',[(1,1),(1,600),(600,1),(900,1100)])
@pytest.mark.parametrize('value',[0,255])
def test_uniform_binary_alpha_has_no_invented_review_edges(size,value):
    alpha=Image.new('L',size,value)
    assert _internal_edges(alpha).getbbox() is None
    assert edge_boxes(alpha,side=256)==[]


def test_opaque_frame_does_not_displace_actual_central_contour():
    a=np.full((896,896),255,dtype='uint8')
    a[300:600,:400]=0
    alpha=Image.fromarray(a);before=alpha.tobytes()
    boxes=edge_boxes(alpha,side=256)
    assert any(x0<=400<x1 and y0<=450<y1 for x0,y0,x1,y1 in boxes[:2])
    assert all(np.count_nonzero(np.asarray(_internal_edges(alpha).crop(box))) for box in boxes[:2])
    assert boxes==edge_boxes(alpha,side=256) and len(boxes)<=4
    assert all(0<=x0<x1<=896 and 0<=y0<y1<=896 for x0,y0,x1,y1 in boxes)
    assert alpha.tobytes()==before


@pytest.mark.parametrize('side',['left','right','top','bottom'])
def test_one_pixel_strand_touching_frame_retains_its_real_inner_boundary(side):
    a=np.zeros((600,600),dtype='uint8')
    if side=='left':a[280:320,0]=255;point=(0,300)
    elif side=='right':a[280:320,-1]=255;point=(599,300)
    elif side=='top':a[0,280:320]=255;point=(300,0)
    else:a[-1,280:320]=255;point=(300,599)
    edges=_internal_edges(Image.fromarray(a))
    assert edges.getpixel(point)==255
    assert any(x0<=point[0]<x1 and y0<=point[1]<y1
               for x0,y0,x1,y1 in edge_boxes(Image.fromarray(a),side=128))


def test_strip_seams_observe_both_sides_without_extra_boundaries():
    a=np.zeros((770,43),dtype='uint8')
    a[256:512,:]=255
    a[510:515,20:23]=255
    expected=np.zeros(a.shape,dtype='uint8')
    # Independent pixel-neighborhood oracle includes all internal neighbors
    # and has no assumptions about the implementation's strip boundaries.
    for y in range(a.shape[0]):
        for x in range(a.shape[1]):
            if any(a[ny,nx]!=a[y,x] for ny,nx in ((y-1,x),(y+1,x),(y,x-1),(y,x+1))
                   if 0<=ny<a.shape[0] and 0<=nx<a.shape[1]):
                expected[y,x]=255
    assert np.array_equal(np.asarray(_internal_edges(Image.fromarray(a))),expected)


def test_partial_alpha_still_takes_priority_over_binary_contours():
    a=np.zeros((1200,1200),dtype='uint8');a[:800,:]=255
    a[850:1150,850:1150]=128
    box=edge_boxes(Image.fromarray(a),side=256)[0]
    assert box[0]>=768 and box[1]>=768
