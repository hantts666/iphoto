"""Facial edit limits and document transactions; fixtures are not accuracy tests."""
from copy import deepcopy
import json
from types import SimpleNamespace

import numpy as np
from PIL import Image
import pytest

from iphoto.ai_grounding import map_grounding
from iphoto.ai_protocol import build_payload
from iphoto.ai_settings import AISettings
from iphoto.ai_tasks import parse_selection, parse_regions
from iphoto.controllers import pixel_selections, face_inventory
from iphoto.document import empty_mask, raster_mask
from iphoto.engine import Recipe
from iphoto.segmentation.face_detection import match_hint
from iphoto.segmentation.face_skin import segment
from test_ai import configure, mock_api, wait_for
from test_canvas_ui import canvas  # noqa: F401
from test_face_detection import completion
from test_face_inventory import face


def rect(left=.2, top=.2, right=.4, bottom=.4):
    return {**empty_mask(), 'label': '局部', 'ops': [{'kind': 'rect', 'mode': 'add',
            'points': [[left, top], [right, bottom]]}]}


def test_partial_neural_face_uses_parent_nose_but_edits_only_the_requested_cheek():
    labels = np.zeros((600,800), np.uint8)
    labels[80:520,80:650] = 1
    labels[300:340,400:440] = 10  # Parent nose is outside the cheek edit.
    labels[150:180,190:220] = 4  # Protected eye inside the requested part.
    engine = SimpleNamespace(predict_native=lambda _: labels)
    image = Image.new('RGB',(800,600))
    hint = rect(.15,.2,.35,.5)
    result, quality = segment(image,hint,[[.525,.53,1]],crop=[0,0,1,1],engine=engine,
                              scope='region',context_hint=rect(.1,.1,.85,.9))
    alpha = np.asarray(raster_mask(result,image.size))
    extent = np.asarray(raster_mask(hint,image.size))
    assert np.count_nonzero(alpha)>1000
    assert not np.any(alpha[extent==0]) and not np.any(alpha[labels==4])
    assert alpha[320,420]==0 and quality['face_scope']=='region'
    assert 0<alpha[240,122]<alpha[240,160]  # Soft inward transition; no spill.
    assert result['label'].endswith('面部局部')


def test_empty_part_rejects_instead_of_returning_the_parent_face():
    labels=np.ones((100,100),np.uint8);labels[40:50,40:50]=10
    hint=rect(.1,.1,.3,.3);labels[10:32,10:32]=17
    with pytest.raises(ValueError,match='指定部位'):
        segment(Image.new('RGB',(100,100)),hint,[[.45,.45,1]],crop=[0,0,1,1],
                engine=SimpleNamespace(predict_native=lambda _:labels),scope='region')


def test_nose_semantics_exclude_cheek_pixels_even_inside_a_loose_spatial_box():
    labels=np.zeros((100,120),np.uint8);labels[10:90,10:110]=1;labels[35:65,55:70]=10
    result,quality=segment(Image.new('RGB',(120,100)),rect(.1,.1,.9,.9),[[.5,.5,1]],crop=[0,0,1,1],
                           engine=SimpleNamespace(predict_native=lambda _:labels),scope='region',part='nose')
    alpha=np.asarray(raster_mask(result,(120,100)))
    assert np.count_nonzero(alpha)>100 and not np.any(alpha[labels!=10])
    assert quality['face_part']=='nose'


@pytest.mark.parametrize('target,scope',[('face','region'),('face_skin','full'),('object','region')])
def test_nose_part_requires_facial_skin_and_a_bounded_edit(target,scope):
    value={'status':'selected','summary':'鼻部','mask_target':target,'face_scope':scope,
           'face_part':'nose','box':[200,200,300,300],'point':[250,250]}
    with pytest.raises(ValueError):parse_selection(completion(value))


def test_partial_matching_does_not_require_nose_inside_cheek_and_rejects_ambiguous_faces():
    parent=face(left=.2)
    hint=rect(.21,.24,.25,.3)
    assert match_hint(hint,[parent]) is None
    assert match_hint(hint,[parent],partial=True,anchor=[.23,.27])==parent
    assert match_hint(hint,[parent,face('duplicate',.2)],partial=True,anchor=[.23,.27]) is None
    assert match_hint(rect(.05,.24,.25,.3),[parent],partial=True,anchor=[.23,.27]) is None


@pytest.mark.parametrize('target',['face','face_skin'])
def test_new_and_legacy_selection_scopes_are_validated(target):
    value={'status':'selected','summary':'局部','mask_target':target,
           'face_scope':'region','box':[200,200,300,300],'point':[250,250]}
    assert parse_selection(completion(value))['face_scope']=='region'
    value.pop('face_scope')
    assert parse_selection(completion(value))['face_scope']=='full'
    value['face_scope']='arbitrary'
    with pytest.raises(ValueError,match='范围'):
        parse_selection(completion(value))


@pytest.mark.parametrize('target',['object','body_skin'])
def test_nonface_plan_cannot_smuggle_a_facial_scope(target):
    value={'name':'部位','reason':'提亮','mask_target':target,'face_scope':'region',
           'box':[200,200,300,300],'point':[250,250],'recipe':Recipe(exposure=.2).to_dict()}
    data=completion({'status':'planned','summary':'局部','regions':[value]})
    with pytest.raises(ValueError,match='范围'):
        parse_regions(data)


def test_closeup_grounding_cannot_expand_a_preplanned_facial_part():
    region={'mask':rect(.3,.3,.4,.4),'face_scope':'region','recipe':Recipe(exposure=.2).to_dict()}
    selected={'mask':rect(.1,.1,.9,.9),'anchor':[.5,.5]}
    result=map_grounding(selected,[.2,.2,.6,.6],(1000,800),region)
    assert result['mask']==region['mask'] and result['recipe']==region['recipe']


def test_recognition_sees_known_faces_but_crop_local_requests_do_not_get_full_photo_coordinates():
    context={'detected_faces':[{'name':'人脸 1','anchor':[.4,.3],'skin_crop':[.2,.1,.6,.5]}]}
    for mode in ('selection','regions'):
        payload=build_payload(AISettings(),'只选脸颊',Recipe().to_dict(),[],'data:image/jpeg;base64,x',mode,context)
        text=json.loads(payload['messages'][1]['content'][0]['text'])
        assert text['detected_faces']==context['detected_faces']
        payload=build_payload(AISettings(),'只选脸颊',Recipe().to_dict(),[],'data:image/jpeg;base64,x',mode,
                              {**context,'_image_crop':[.2,.1,.6,.5]})
        assert 'detected_faces' not in json.loads(payload['messages'][1]['content'][0]['text'])


@pytest.mark.parametrize('target',['face','face_skin'])
def test_direct_partial_face_preserves_bounds_and_never_creates_a_full_face_identity(canvas,monkeypatch,target):  # noqa: F811
    e=canvas.e;e._face_hints=[face('local-face-1')]
    captured=[]
    monkeypatch.setattr(pixel_selections,'select_hint',lambda *args,**kw:captured.append((args,kw)) or True)
    before=deepcopy(e._layers)
    value={'status':'selected','summary':'只选小块','mask_target':target,'face_scope':'region',
           'box':[210,240,250,300],'point':[230,270]}
    with mock_api(completion(value)) as (url,requests):
        configure(e.ai,url);canvas.find('selectionDescriptionInput').setProperty('text','只选脸颊')
        canvas.click('aiSelectionButton');wait_for(lambda:not e.ai.busy and e._pending_request is None)
    args,kw=captured[0]
    assert args[1]['ops']==parse_selection(completion(value))['mask']['ops']
    assert kw['face_scope']=='region' and kw['face_context']==e._face_hints[0]['mask']
    assert kw['crop']==face_inventory.current(e)[0]['skin_crop'] and kw['face_hint'] is None
    assert e._layers==before and not e._scene.catalog and len(requests)==1


def test_auto_partial_plan_keeps_edit_mask_while_using_full_face_context(canvas,monkeypatch):  # noqa: F811
    e=canvas.e;e._face_hints=[face('local-face-1')]
    captured=[]
    monkeypatch.setattr(pixel_selections,'select_regions',lambda *args,**kw:captured.append((args,kw)) or True)
    value={'name':'左脸颊','reason':'局部气色','mask_target':'face_skin','face_scope':'region',
           'parts':[],'box':[210,240,250,300],'point':[230,270],'recipe':Recipe(exposure=.15).to_dict()}
    plan={'action':'layers','scope':'regions','summary':'只调整左脸颊','recipe':Recipe().to_dict(),
          'regions':[value],'layer_edits':[],'group':None,'repairs':[]}
    with mock_api(completion(plan)) as (url,requests):
        configure(e.ai,url);assert e.sendMessage('只给左脸颊增加气色','auto')
        wait_for(lambda:not e.ai.busy and e._pending_request is None)
    region=captured[0][0][1][0]
    assert region['mask']['ops'][0]['points'][1][0]<.26
    assert region['face_scope']=='region' and region['face_context']==e._face_hints[0]['mask']
    assert region['skin_crop']==face_inventory.current(e)[0]['skin_crop'] and len(requests)==1


def test_partial_face_relocalizes_once_on_a_unique_parent_and_maps_crop_coordinates(canvas,monkeypatch):  # noqa: F811
    e=canvas.e;e._face_hints=[face('local-face-1')]
    captured=[]
    monkeypatch.setattr(pixel_selections,'select_hint',lambda *args,**kw:captured.append((args,kw)) or True)
    initial={'status':'selected','summary':'局部','mask_target':'face_skin','face_scope':'region',
             'box':[150,240,250,300],'point':[230,270]}
    refined={**initial,'box':[300,300,450,450],'point':[375,375]}
    responses=iter([completion(initial),completion(refined)])
    with mock_api(lambda _:next(responses)) as (url,requests):
        configure(e.ai,url);assert e.sendMessage('只选脸颊','selection')
        wait_for(lambda:not e.ai.busy and e._pending_request is None)
    args,kw=captured[0]
    from iphoto.ai_grounding import region_crop
    crop=region_crop(e._face_hints[0]['mask'],(384,384))
    # Canvas is parametrized over sizes; crop mapping is normalized apart from
    # the integer source-pixel rounding used by the real document dimensions.
    expected=map_grounding(parse_selection(completion(refined)),crop,(e._width,e._height),{'mask':rect()})
    assert args[1]['ops']==expected['mask']['ops'] and kw['face_scope']=='region'
    assert kw['face_context']==e._face_hints[0]['mask'] and len(requests)==2


def test_unsupported_closeup_keeps_existing_layers_and_selection(canvas):  # noqa: F811
    e=canvas.e;e._face_hints=[face('local-face-1')]
    before=deepcopy(e._layers);draft=deepcopy(e._candidate);cursor=e._cursor
    initial={'status':'selected','summary':'局部','mask_target':'face_skin','face_scope':'region',
             'box':[150,240,250,300],'point':[230,270]}
    unsupported={'status':'unsupported','summary':'被遮挡看不清','mask_target':'face_skin',
                 'face_scope':'region','box':[],'point':[]}
    responses=iter([completion(initial),completion(unsupported)])
    with mock_api(lambda _:next(responses)) as (url,requests):
        configure(e.ai,url);assert e.sendMessage('只选脸颊','selection')
        wait_for(lambda:not e.ai.busy and e._pending_request is None)
    assert e._layers==before and e._candidate==draft and e._cursor==cursor and len(requests)==2
