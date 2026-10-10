"""Missing source detail outside an editable core remains visible to reviewers."""
from copy import deepcopy
from io import BytesIO
import base64

import numpy as np
from PIL import Image
import pytest

from iphoto.ai_protocol import image_data_url
from iphoto.cutout import compose_cutout
from iphoto.document import empty_mask, new_layer, render_layers
from iphoto.engine import Recipe
from iphoto.masks import encode_bitmap
from iphoto.matte_review import point_bounds, point_frame, quality_frame, render_review


@pytest.mark.parametrize('core',[[180,160,692,672],[0,0,512,512],[488,388,1000,900]])
@pytest.mark.parametrize('final',[False,True])
def test_native_observation_sees_outside_core_without_changing_coordinates_or_image_budget(tmp_path,core,final):
    yy,xx=np.indices((900,1000))
    source=Image.fromarray(np.stack(((xx*3+yy)%256,(xx+yy*5)%256,(xx*7+yy*3)%256),axis=2).astype('uint8'))
    alpha=np.zeros((900,1000),'uint8')
    alpha[core[1]+80:core[3]-80,core[0]+80:core[2]-80]=255
    alpha[core[1]+80:core[3]-80,core[0]+40:core[0]+80]=80
    mask={**empty_mask(),'bitmap':encode_bitmap(Image.fromarray(alpha),sampling='alpha',preserve_resolution=True)}
    layer=new_layer('Existing exposure',True);layer['recipe']=Recipe(exposure=.3).to_dict()
    before=source.tobytes(),deepcopy((mask,layer))
    result=render_review(source,[layer],mask,tmp_path,'outside',[core],target_context=True,final_review=final)
    frame=quality_frame(core,source.size)
    w,h=frame[2]-frame[0],frame[3]-frame[1]
    assert result['boxes']==[core] and result['quality_boxes']==[frame] and frame!=core
    assert result['point_boxes']==[point_frame(core,source.size)]
    assert result['point_bounds']=={'1':point_bounds(core,point_frame(core,source.size))}
    panel_item=next(v for v in result['review_images'] if v['path'].endswith('-panel-0.png'))
    assert '外侧最多52px观察区' in panel_item['label']
    with Image.open(panel_item['path']) as panel:
        assert panel.size==(w*2,(h+24)*2) and max(panel.size)<=1280
        assert panel.crop((0,24,w,h+24)).tobytes()==source.crop(frame).tobytes()
        assert panel.crop((w,24,w*2,h+24)).tobytes()==Image.fromarray(alpha).crop(frame).convert('RGB').tobytes()
        cutout=compose_cutout(render_layers(source,[layer]),Image.fromarray(alpha)).crop(frame)
        white=Image.alpha_composite(Image.new('RGBA',(w,h),'white'),cutout).convert('RGB')
        assert panel.crop((0,h+48,w,h*2+48)).tobytes()==white.tobytes()
        wire=image_data_url(panel_item['path'],lossless=True)
        with Image.open(BytesIO(base64.b64decode(wire.split(',',1)[1]))) as sent:
            assert sent.size==panel.size and sent.tobytes()==panel.tobytes()
    black_item=next(v for v in result['review_images'] if v['path'].endswith('-quality-black-0.png'))
    black=Image.alpha_composite(Image.new('RGBA',(w,h),'black'),cutout).convert('RGB')
    with Image.open(black_item['path']) as actual:assert actual.tobytes()==black.tobytes()
    # The original core source and corrected-point locator retain their contract.
    with Image.open(next(v['path'] for v in result['images'] if v['path'].endswith('-source-0.png'))) as original:
        assert original.size==(512,512) and original.tobytes()==source.crop(core).tobytes()
    assert len(result['review_images'])==(5 if final else 6)
    assert (source.tobytes(),(mask,layer))==before
