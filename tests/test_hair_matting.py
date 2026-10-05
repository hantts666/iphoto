"""Borrowing person alpha must not select clothes or change unpainted pixels."""
from copy import deepcopy
from types import SimpleNamespace
import hashlib
import json
import cv2
import numpy as np
from PIL import Image
import pytest

from iphoto.document import empty_mask, raster_mask
from iphoto.masks import encode_bitmap
from iphoto.matting.hair import HairContext, refine_local, refine_edges
from iphoto.matting.local import stroke_mask
from iphoto.matting import portrait_models
from iphoto.controllers.matte_process import _progress
from iphoto.ai_protocol import parse_auto
from iphoto.engine import Recipe


class Matte:
    provider='fixture'; fallback=''
    def predict(self, pixels): return np.full((640,640),.4,np.float32)


class Parser:
    def __init__(self, label=17): self.label=label
    def _scores(self, image):
        labels=np.full((image.height,image.width),self.label,np.uint8)
        labels[:,536:]=16
        labels=cv2.resize(labels,(512,512),interpolation=cv2.INTER_NEAREST)
        scores=np.zeros((19,512,512),np.float32)
        for label in range(19):scores[label][labels==label]=20
        return scores


def fixture():
    image=Image.new('RGB',(1000,900),(95,84,51))
    a=np.zeros((900,1000),np.uint8);a[240:600,280:480]=255
    mask={**empty_mask(),'label':'头发','bitmap':encode_bitmap(Image.fromarray(a),sampling='alpha',preserve_resolution=True)}
    parent=np.zeros_like(a);parent[200:650,250:700]=255;parent[300:550,480:535]=80
    portrait=SimpleNamespace(predict_image=lambda image:Image.fromarray(parent),fallback='')
    stroke={'points':[[530/999,430/899]],'radius':128/900}
    return image,mask,portrait,stroke


def test_hair_recovers_outward_partial_alpha_and_excludes_person_clothes():
    image,mask,portrait,stroke=fixture();before=deepcopy(mask);pixels=image.tobytes();phases=[]
    output,quality=refine_local(image,mask,stroke,portrait=portrait,parser=Parser(),engine=Matte(),
                                progress=lambda tile=None,tiles=None,phase=None:phases.append(phase))
    alpha=raster_mask(output,image.size);scope=np.asarray(stroke_mask(stroke,image.size))>0
    old=np.asarray(raster_mask(mask,image.size));new=np.asarray(alpha)
    assert np.array_equal(old[~scope],new[~scope])
    assert 0<alpha.getpixel((505,430))<255  # Outside the seed's opaque edge.
    assert alpha.getpixel((600,430))==0    # Person model includes this shirt.
    assert quality['hair_matting'] and quality['changed_pixels']>0
    assert mask==before and image.tobytes()==pixels
    assert all(phase in phases for phase in ('portrait','hair_partition','hair_outer','hair_split'))


def test_hair_declines_unrelated_semantic_targets_and_unconfirmed_head():
    image,mask,portrait,stroke=fixture()
    for target in ('face','face_skin','body_skin'):
        with pytest.raises(ValueError,match='皮肤或五官'):
            refine_local(image,{**mask,'semantic_target':target},stroke)
    with pytest.raises(ValueError,match='不能可靠确认为头发'):
        refine_local(image,mask,stroke,portrait=portrait,parser=Parser(1),engine=Matte())


def test_hair_rejects_a_positive_point_that_is_not_known_hair():
    image,mask,portrait,stroke=fixture();before=deepcopy(mask)
    with pytest.raises(ValueError,match='保留点不能确认为'):
        refine_local(image,mask,stroke,points=[[600/999,430/899,1]],portrait=portrait,parser=Parser(),engine=Matte())
    assert mask==before


def test_hair_does_not_silently_overwrite_contradictory_points():
    image,mask,portrait,stroke=fixture()
    point=[400/999,430/899]
    with pytest.raises(ValueError,match='保留点和排除点重叠'):
        refine_local(image,mask,stroke,points=[[*point,1],[*point,0]],portrait=portrait,parser=Parser(),engine=Matte())


def test_semantic_exclusion_cannot_be_reopened_by_partial_person_alpha():
    image,mask,portrait,_=fixture()
    original=raster_mask(mask,image.size);box=(240,200,720,650)
    context=HairContext(image,original,portrait=portrait,parser=Parser())
    previous=np.asarray(original.crop(box));hard=previous>127
    excluded,_=context.solve(image,previous,box,semantic=hard,semantic_radius=24,engine=Matte())
    unknown,_=context.solve(image,previous,box,semantic=hard,semantic_radius=40,engine=Matte())
    # This lies on soft person support outside the semantic target; it must
    # respect the requested 24px exclusion while staying unknown at 40px.
    assert excluded[230,265]==0 and 0<unknown[230,265]<255
    assert unknown[230,160]==255
    with pytest.raises(ValueError,match='边缘宽度无效'):
        context.solve(image,previous,box,semantic=hard,semantic_radius=True,engine=Matte())


def test_portrait_model_digest_is_required_before_onnx_load(tmp_path,monkeypatch):
    data=b'changed';(tmp_path/portrait_models.NAME).write_bytes(data)
    monkeypatch.setattr(portrait_models,'MODEL_DIR',tmp_path)
    monkeypatch.setattr(portrait_models,'SIZE',len(data))
    monkeypatch.setattr(portrait_models,'DIGEST',hashlib.sha256(b'expected').hexdigest())
    with pytest.raises(ValueError,match='校验失败'):portrait_models.verified_path()


def test_hair_progress_resets_tile_counter_between_two_models():
    owner=SimpleNamespace(_status='',changed=SimpleNamespace(emit=lambda:None))
    active={'method':'hair'}
    for phase,total in [('hair_outer',4),('hair_split',5)]:
        _progress(owner,active,{'phase':phase})
        for tile in range(1,total+1):_progress(owner,active,{'phase':phase,'tile':tile,'tiles':total})
        assert f'{total}/{total} 块' in owner._status
    last=owner._status
    _progress(owner,active,{'phase':'hair_split','tile':1,'tiles':5})
    assert owner._status==last


def test_conversation_hair_strategy_requires_installed_capability():
    plan={'action':'channel_mask','scope':'current_selection','summary':'核对人物发丝',
          'recipe':Recipe().to_dict(),'regions':[],'layer_edits':[],'group':None,
          'repairs':[],'mask_refinement':None,'strategy':'hair_matte','edit_prompt':None}
    data={'choices':[{'finish_reason':'stop','message':{'content':json.dumps(plan,ensure_ascii=False)}}]}
    args=(data,Recipe().to_dict(),[])
    with pytest.raises(ValueError,match='人物发丝策略不可用'):
        parse_auto(*args,current_scope='selection',workspace={'channel_mask_available':True})
    result=parse_auto(*args,current_scope='selection',workspace={'channel_mask_available':True,'hair_matting_available':True})
    assert result['strategy']=='hair_matte'


def test_native_review_includes_outer_edges_when_interior_partial_alpha_dominates():
    from iphoto.matte_review import edge_boxes
    a=np.zeros((2400,2400),np.uint8);a[650:1900,500:1850]=255
    a[1200:1700,900:1700]=150
    boxes=edge_boxes(Image.fromarray(a))
    assert 3<=len(boxes)<=4
    assert any(box[1]<650 and box[2]>1700 for box in boxes)
    assert all(box[2]-box[0]==512 and box[3]-box[1]==512 for box in boxes)


def test_hair_evidence_regions_blend_in_overlap_and_preserve_unprocessed_pixels():
    image,mask,portrait,_=fixture()
    boxes=[[300,240,700,640],[400,330,800,730]]
    outputs=[]
    for ordered in (boxes,list(reversed(boxes))):
        result,quality=refine_edges(image,mask,boxes=ordered,portrait=portrait,parser=Parser(),engine=Matte())
        assert quality['edge_refinement'] and len(quality['regions'])==2
        outputs.append(np.asarray(raster_mask(result,image.size)))
    assert np.array_equal(*outputs)
    scope=np.zeros((900,1000),bool)
    for x0,y0,x1,y1 in boxes:scope[y0:y1,x0:x1]=True
    before=np.asarray(raster_mask(mask,image.size))
    assert np.array_equal(outputs[0][~scope],before[~scope])
    assert 0<outputs[0][430,505]<255 and outputs[0][430,600]==0


def test_hair_strategy_runs_native_model_before_review_without_publishing(monkeypatch):
    from iphoto.controllers import channel_auto
    image,mask,_,_=fixture();calls=[]
    editor=SimpleNamespace(_layers=[{'id':'original'}],_candidate=deepcopy(mask),_cursor=0,_sha='photo',
                           _request=lambda op,**data:calls.append((op,data)))
    state={'token':'same','hair':True,'revision':0,'bound':False}
    monkeypatch.setattr(channel_auto,'_current',lambda *args:({},state))
    initial={'mask':mask,'quality':{'warnings':[],'elapsed_ms':10}}
    snapshot=deepcopy((editor._layers,editor._candidate,editor._cursor))
    channel_auto.complete(editor,initial,'same')
    assert calls[0][0]=='matte' and calls[0][1]['method']=='hair'
    native={'mask':mask,'quality':{'edge_refinement':True,'warnings':[],'elapsed_ms':3}}
    channel_auto.complete(editor,native,'same')
    assert calls[1][0]=='matte_candidate'
    assert calls[1][1]['target_context'] is True
    assert state['result']['quality']['hair_refinement']['edge_refinement']
    assert state['result']['quality']['elapsed_ms']==13
    assert snapshot==(editor._layers,editor._candidate,editor._cursor)
