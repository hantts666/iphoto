"""Complete-part replacement and legacy provenance; no accuracy claims."""
from copy import deepcopy
import json
from types import SimpleNamespace

import numpy as np
from PIL import Image, ImageDraw
import pytest

from iphoto.ai_mask_refinement import validate_request, validate_result, validate_reselection, prepare_crop
from iphoto.ai_mask_review import reselection_images
from iphoto.ai_protocol import build_payload
from iphoto.ai_settings import AISettings
from iphoto.controllers import mask_refinement, face_inventory
from iphoto.document import empty_mask, raster_mask, read_project
from iphoto.engine import Recipe
from iphoto.masks import encode_bitmap
from iphoto.segmentation.face_restore import reselect
from iphoto.segmentation import service
from test_ai import configure, wait_for
from test_ai_mask_refinement import start_chat, finished, typed_mask, workspace
from test_canvas_ui import canvas  # noqa: F401
from test_editor import settled


def replacement(mask,size=(2400,1600),scope=None):
    scope=scope or empty_mask(full=True)
    pixels=Image.new('L',size)
    ImageDraw.Draw(pixels).rectangle((int(size[0]*.36),int(size[1]*.35),int(size[0]*.54),int(size[1]*.54)),fill=230)
    result=deepcopy(mask)
    result.update(bitmap=encode_bitmap(pixels,preserve_resolution=True),ops=[],feather=0,face_part_scope=scope)
    return result


@pytest.mark.parametrize('mode,color',[('new',False),('bound',True),('existing',True)])
def test_legacy_complete_reselection_and_color_publish_once(canvas,monkeypatch,tmp_path,mode,color):  # noqa: F811
    e=canvas.e;mask,lid,jobs,api=start_chat(canvas,monkeypatch,mode,color,method='reselect',
        verification={'status':'accept','summary':'图2和图3保留完整可见上下唇，变化符合原图'})
    before=deepcopy(e._layers);cursor=e._cursor
    assert 'face_part_scope' not in mask
    with api as (url,requests):
        configure(e.ai,url);assert e.sendMessage('重新选择完整可见上下嘴唇，继续原唇色层','auto')
        wait_for(lambda:bool(jobs));assert len(requests)==1 and 'part_reselect' in jobs[0]['jobs'][0]
        candidate=replacement(mask)
        mask_refinement.complete(e,{'items':[{'id':'target','mask':candidate,'quality':{'model':'controlled output','warnings':[]}}]},jobs[0]['context'])
        finished(e);assert len(requests)==2
        assert '图2' not in e.conversation[-1]['text'] and '图3' not in e.conversation[-1]['text']
        contents=requests[1][2]['messages'][1]['content'];context=json.loads(contents[0]['text'])
        assert context['mode']=='mask_reselect_validate'
        assert len([item for item in contents if item['type']=='image_url'])==5
        assert 'face_part_scope' not in str(context) and 'recipe' not in context
        if mode=='new':
            assert e._layers==before and e._cursor==cursor and e._candidate==candidate
            e.undo();wait_for(lambda:settled(e));assert e._candidate==mask
            e.redo();wait_for(lambda:settled(e));assert e._candidate==candidate
            final=before
        else:
            final=deepcopy(e._layers)
            assert e.activeLayerId==lid and e._layer()['mask']==candidate and e._cursor==cursor+1
            assert len(final)==len(before) and e.parameters['hsl_red_lightness']==6
            assert e.parameters['hsl_red_saturation']==7 and e.parameters['warmth']==4 and e._layer()['locked']==['warmth']
            assert [l for l in final if l['id']!=lid]==[l for l in before if l['id']!=lid]
            e.undo();wait_for(lambda:settled(e));assert e._layers==before
            e.redo();wait_for(lambda:settled(e));assert e._layers==final
        project=tmp_path/'reselected.iphoto';e.saveProject(str(project));wait_for(lambda:not e.savingProject)
        saved=read_project(project);assert saved['layers']==final
        assert saved['selection_draft']==(candidate if mode=='new' else None)


@pytest.mark.parametrize('status',['reject','uncertain'])
def test_reselection_reject_preserves_legacy_mask_and_planned_color(canvas,monkeypatch,status):  # noqa: F811
    e=canvas.e;mask,_,jobs,api=start_chat(canvas,monkeypatch,'existing',True,method='reselect',
        verification={'status':status,'summary':'仍明显缺少可见上唇主体'})
    before=deepcopy(e._layers);cursor=e._cursor
    with api as (url,requests):
        configure(e.ai,url);e.sendMessage('完整重选嘴唇并提亮','auto');wait_for(lambda:bool(jobs))
        mask_refinement.complete(e,{'items':[{'id':'target','mask':replacement(mask)}]},jobs[0]['context']);finished(e)
        assert len(requests)==2 and e._layers==before and e._cursor==cursor
        assert '完整可见嘴唇' in e.conversation[-1]['text'] and e.conversation[-1]['state']=='answered'


def test_reselection_cancel_quality_keeps_original_scope_and_history(canvas,monkeypatch):  # noqa: F811
    e=canvas.e;mask,_,jobs,api=start_chat(canvas,monkeypatch,'bound',True,method='reselect',delay=.6)
    before=deepcopy(e._layers);cursor=e._cursor
    with api as (url,requests):
        configure(e.ai,url);e.sendMessage('完整重选嘴唇','auto');wait_for(lambda:bool(jobs))
        mask_refinement.complete(e,{'items':[{'id':'target','mask':replacement(mask)}]},jobs[0]['context'])
        wait_for(lambda:e.ai.busy and (e._pending_request or {}).get('mask_refinement',{}).get('stage')=='verify')
        e.selection.cancelTask();finished(e)
        assert e._layers==before and e._candidate==mask and e._cursor==cursor
        assert e.conversation[-1]['role']=='assistant' and e.conversation[-1]['state']=='answered'


def test_unchanged_pixels_can_record_complete_scope_without_claiming_pixel_repair(canvas,monkeypatch):  # noqa: F811
    e=canvas.e;mask,_,jobs,api=start_chat(canvas,monkeypatch,'new',False,method='reselect')
    with api as (url,requests):
        configure(e.ai,url);e.sendMessage('重新识别完整嘴唇','auto');wait_for(lambda:bool(jobs))
        updated={**mask,'face_part_scope':empty_mask(full=True)}
        mask_refinement.complete(e,{'items':[{'id':'target','mask':updated}]},jobs[0]['context']);finished(e)
        assert len(requests)==2 and e._candidate==updated
        assert '范围像素保持' in e.conversation[-1]['text']
        assert raster_mask(updated,(2400,1600)).tobytes()==raster_mask(mask,(2400,1600)).tobytes()


@pytest.mark.parametrize('case',['no-intent','wrong-intent','no-context','foreign-intent'])
def test_reselection_protocol_requires_complete_intent_and_capability(case):
    context={**workspace(),'selection_mask_reselectable':True,'mask_exclusion_available':False}
    value={'layer_id':None,'recipe':None,'method':'reselect','intent':'whole_visible_part'}
    if case=='no-intent':value.pop('intent')
    elif case=='wrong-intent':value['intent']='partial_corner'
    elif case=='no-context':context['selection_mask_reselectable']=False
    else:value['method']='restore';context['selection_mask_restorable']=True
    with pytest.raises(ValueError):validate_request(value,'current_selection',Recipe().to_dict(),[],context)


def test_reselection_can_change_alpha_but_not_part_person_or_expected_scope():
    size=(300,200);mask=typed_mask(size);result=replacement(mask,size)
    assert validate_reselection(result,mask,size,empty_mask(full=True))==result
    with pytest.raises(ValueError):validate_result(result,mask,size)
    with pytest.raises(ValueError):validate_reselection(result,mask,size,empty_mask())
    with pytest.raises(ValueError):validate_reselection({**result,'face_part':'nose'},mask,size,empty_mask(full=True))
    with pytest.raises(ValueError):validate_reselection(result,mask,(301,200),empty_mask(full=True))
    empty={**result,'bitmap':encode_bitmap(Image.new('L',size),preserve_resolution=True)}
    with pytest.raises(ValueError):validate_reselection(empty,mask,size,empty_mask(full=True))
    image=Image.new('RGB',size)
    assert prepare_crop(image,result,reference=mask,reselection_scope=empty_mask(full=True),opacity=.5)[1].mode=='L'
    with pytest.raises(ValueError):prepare_crop(image,result,reference=mask)


def test_reselection_images_show_separate_clamped_loss_and_gain():
    size=(300,200);old=typed_mask(size);new=replacement(old,size)
    image=Image.new('RGB',size,(100,100,100));image.info['exif']=b'private'
    reference,loss,gain=reselection_images(image,new,old,(0,0,*size),empty_mask(full=True))
    assert all(item.size==size and not item.info for item in (reference,loss,gain))
    # (100,70) loses old coverage; (160,105) newly gains it.
    assert loss.getpixel((100,70))[0]>loss.getpixel((100,70))[2] and gain.getpixel((100,70))==(100,100,100)
    assert gain.getpixel((160,105))[2]>gain.getpixel((160,105))[0] and loss.getpixel((160,105))==(100,100,100)
    assert loss.getpixel((0,0))==gain.getpixel((0,0))==(100,100,100)


def test_reselection_payload_checks_complete_target_and_both_changes_neutrally():
    payload=build_payload(AISettings(provider='openai'),'核对完整可见lips',Recipe().to_dict(),[],
        'source','mask_reselect_validate',{'face_part':'lips','crop_size':[210,120],
        'reference_image':'before','selection_overlay':'after','changes_image':'loss','additions_image':'gain',
        'face_context_image':'face','face_part_scope':{'private':1},'recipe':{'secret':1}})
    contents=payload['messages'][1]['content']
    assert [x['image_url']['url'] for x in contents if x['type']=='image_url']==['source','before','after','loss','gain','face']
    assert set(json.loads(contents[0]['text']))=={'request','mode','face_part','crop_size','coordinate_system','has_face_context'}
    assert '仍明显缺少可见五官主体时reject' in payload['messages'][0]['content']
    assert payload['response_format']['json_schema']['schema']['required']==['status','summary']


@pytest.mark.parametrize('case',['valid','outside-face','second-face','missing-feature','missing-model'])
def test_reselection_context_requires_only_one_person_and_contained_previous_mask(monkeypatch,case):
    mask=typed_mask((300,200));face={'id':'one','mask':empty_mask(full=True),'skin_crop':[0,0,1,1],
        'face_features':{'eyes':[[.3,.2],[.6,.2]],'mouth':[[.3,.6],[.6,.6]]},'anchor':[.5,.4]}
    faces=[face]
    if case=='outside-face':face['mask']=empty_mask()
    if case=='second-face':faces.append({**face,'id':'two'})
    if case=='missing-feature':face.pop('face_features')
    monkeypatch.setattr(face_inventory,'grounding_context',lambda *args:face)
    monkeypatch.setattr(face_inventory,'current',lambda *args:faces)
    monkeypatch.setattr('iphoto.segmentation.face_precision.available',lambda:case!='missing-model')
    assert bool(mask_refinement.reselect_context(SimpleNamespace(),mask))==(case=='valid')


def test_neural_reselection_uses_complete_person_scope_and_replaces_not_unions(monkeypatch):
    size=(300,200);hint=typed_mask(size);context={'crop':[0,0,1,1],
        'features':{'eyes':[[.3,.2],[.6,.2]],'mouth':[[.3,.6],[.6,.6]]},
        'anchor':[.5,.4],'mask':empty_mask(full=True)}
    planned=replacement(hint,size)
    def segment(image,spatial_hint,points,**options):
        assert spatial_hint==context['mask'] and options['require_precision'] and options['scope']=='region'
        return planned,{'model':'controlled','warnings':[]}
    monkeypatch.setattr('iphoto.segmentation.face_skin.segment',segment)
    result,quality=reselect(Image.new('RGB',size),hint,context)
    before,after=np.asarray(raster_mask(hint,size)),np.asarray(raster_mask(result,size))
    assert np.any(after>before) and np.any(after<before) and result['face_part_scope']==context['mask']
    assert '完整可见五官重选' in quality['model']


@pytest.mark.parametrize('case',['proxy','background','points','two-methods','boundary'])
def test_reselection_worker_requires_foreground_native_source_and_one_method(case,monkeypatch):
    calls=[]
    monkeypatch.setattr('iphoto.segmentation.face_restore.reselect',lambda *args,**kwargs:calls.append(True))
    job={'id':'part','hint':typed_mask((300,200)),'part_reselect':{},'points':[]}
    if case=='points':job['points']=[[.5,.5,1]]
    if case=='two-methods':job['part_restore']={}
    if case=='boundary':job['part_boundary']={}
    image=Image.new('RGB',(300,200))
    if case=='background':
        assert service.segment_jobs(image,[job],source=image,tolerant=True)['items']==[]
    else:
        with pytest.raises(ValueError):service.segment_jobs(image,[job],source=None if case=='proxy' else image)
    assert not calls
