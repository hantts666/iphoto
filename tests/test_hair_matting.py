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


def test_joint_hair_uncertainty_map_preserves_each_core_exclusion_width():
    image,mask,portrait,_=fixture();original=raster_mask(mask,image.size);box=(240,200,720,650)
    context=HairContext(image,original,portrait=portrait,parser=Parser())
    previous=np.asarray(original.crop(box));hard=previous>127
    band=np.full(previous.shape,40,np.uint8);band[:200]=24;snapshot=band.copy()
    pixels,_=context.solve(image,previous,box,semantic=hard,semantic_radius=40,semantic_band=band,engine=Matte())
    assert pixels[160,265]==0 and 0<pixels[230,265]<255
    assert np.array_equal(band,snapshot)
    for invalid in (band.astype(float),band[:20],np.full(band.shape,0,np.uint8)):
        with pytest.raises(ValueError,match='宽度图无效'):
            context.solve(image,previous,box,semantic=hard,semantic_band=invalid,engine=Matte())
    with pytest.raises(ValueError,match='宽度图无效'):
        context.solve(image,previous,box,semantic_band=band,engine=Matte())


def test_eight_reviewed_hair_points_reach_both_alpha_passes_with_roles_preserved():
    image,mask,portrait,_=fixture();original=raster_mask(mask,image.size);box=(240,200,720,650)
    context=HairContext(image,original,portrait=portrait,parser=Parser())
    points=[[x/999,y/899,role] for x,y,role in ((400,300,1),(650,300,0),(650,360,0),(505,330,2),
                                               (400,500,1),(650,500,0),(650,560,0),(505,490,2))]
    previous=np.asarray(original.crop(box));photo=image.tobytes();snapshot=deepcopy(mask)
    pixels,detail=context.solve(image,previous,box,semantic=previous>127,semantic_radius=40,points=points,engine=Matte())
    for x,y,role in ((400,300,1),(650,300,0),(505,330,2),(400,500,1),(650,500,0),(505,490,2)):
        value=int(pixels[y-box[1],x-box[0]])
        assert value==255 if role==1 else value==0 if role==0 else 0<value<255
    assert detail['outer_tiles']>0 and detail['split_tiles']>0
    with pytest.raises(ValueError,match='最多 6'):
        context.solve(image,previous,box,points=points,engine=Matte())
    with pytest.raises(ValueError,match='最多 8'):
        context.solve(image,previous,box,semantic=previous>127,points=points+[[.6,.6,0]],engine=Matte())
    assert image.tobytes()==photo and mask==snapshot


def test_strand_identity_can_recover_missing_alpha_without_becoming_opaque():
    image,mask,portrait,_=fixture();photo=image.tobytes();snapshot=deepcopy(mask)
    original=raster_mask(mask,image.size);box=(240,200,720,650)
    context=HairContext(image,original,portrait=portrait,parser=Parser())
    previous=np.asarray(original.crop(box));semantic=previous>127;semantic[230,265]=True
    points=[[400/999,430/899,1],[690/999,430/899,0],[505/999,430/899,2]]
    pixels,_=context.solve(image,previous,box,semantic=semantic,points=points,engine=Matte())
    assert previous[230,265]==0 and 0<pixels[230,265]<128
    assert pixels[230,160]==255 and pixels[230,450]==0
    assert image.tobytes()==photo and mask==snapshot
    with pytest.raises(ValueError,match='分割提示点'):
        context.solve(image,previous,box,points=points,engine=Matte())
    with pytest.raises(ValueError,match='皮肤、帽子或衣物'):
        context.solve(image,previous,box,semantic=semantic,points=[*points[:2],[600/999,430/899,2]],engine=Matte())


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


@pytest.mark.parametrize('hair',[False,True])
def test_strand_capability_and_correction_roles_survive_the_controller_boundary(monkeypatch,hair):
    from iphoto.controllers import channel_auto
    image,mask,_,_=fixture();calls=[];plans=[]
    state={'token':'same','hair':hair,'revision':0,'mask':mask,'result':{'mask':mask},'bound':False}
    pending={'text':'核对头发'}
    editor=SimpleNamespace(_layers=[{'id':'original'}],_candidate=deepcopy(mask),_cursor=0,_generation=0,_sha='photo',
                           _request=lambda op,**data:calls.append((op,data)),
                           ai=SimpleNamespace(plan=lambda *args:plans.append(args)),
                           changed=SimpleNamespace(emit=lambda:None),_notify=lambda *args:calls.append(('error',args)))
    snapshot=deepcopy((editor._layers,editor._candidate,editor._cursor))
    monkeypatch.setattr(channel_auto,'_current',lambda *args:(pending,state))
    monkeypatch.setattr('iphoto.segmentation.precise_sam.available',lambda:True)
    monkeypatch.setattr(channel_auto,'image_data_url',lambda path:'image')
    result={'boxes':[[100,100,612,612]],'images':[{'path':'source.png'}],
            'context_points':True,'point_bounds':{'1':[195,195,805,805]},
            'review_images':[{'label':'原片','path':'source.png'}],
            'keep_candidates':{'1':[[700,800]]},'exclude_candidates':{'1':[[470,470]]}}
    state['result']['quality']={}
    channel_auto.review_ready(editor,result,{'token':'same'},0)
    assert plans[0][6]['strand_points'] is hair
    assert plans[0][6]['context_points'] is hair
    assert plans[0][6]['point_bounds']==(result['point_bounds'] if hair else None)
    assert plans[0][6]['correction_method']==('hair' if hair else 'semantic')
    editor._pending_request={'channel_auto':state}
    patch={'edge':1,'radius':24,'points':[[700,800,1],[470,470,0],[320,640,2]]}
    channel_auto.reviewed(editor,{'status':'revise','summary':'补细丝','corrections':[patch]})
    if hair:
        assert calls[0][0]=='matte_point_evidence' and state['revision']==0
        assert calls[0][1]['context_points'] is True and calls[0][1]['corrections']==[patch]
        evidence={'images':[{'label':'参照原片','path':'source.png'}],
                  'point_bounds':result['point_bounds'],'windows':[]}
        channel_auto.points_ready(editor,evidence,{'token':'same'},0)
        assert plans[1][5]=='matte_points' and plans[1][6]['corrections']==[patch]
        assert state['revision']==0
        channel_auto.points_reviewed(editor,{'status':'keep','summary':'细丝身份可见','corrections':[]})
        assert calls[1][0]=='matte' and calls[1][1]['hair'] is True
        assert calls[1][1]['corrections']==[patch] and state['revision']==1
    else:
        assert calls[0][0]=='error' and state['revision']==0
    assert snapshot==(editor._layers,editor._candidate,editor._cursor)
