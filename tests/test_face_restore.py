"""Stored user scope, addition-only geometry and atomic AI publication."""
from copy import deepcopy
import json

import numpy as np
from PIL import Image, ImageDraw
import pytest

from iphoto.ai_mask_refinement import validate_request, validate_restoration
from iphoto.ai_mask_review import comparison_images
from iphoto.ai_protocol import build_payload
from iphoto.ai_settings import AISettings
from iphoto.controllers import mask_refinement
from iphoto.document import empty_mask, raster_mask, read_project, validate_mask
from iphoto.engine import Recipe
from iphoto.masks import encode_bitmap
from iphoto.segmentation.face_restore import restore
from iphoto.segmentation import face_precision, face_skin, service
from test_ai import configure, wait_for
from test_ai_mask_refinement import start_chat, finished, typed_mask, workspace
from test_canvas_ui import canvas  # noqa: F401
from test_editor import settled
from test_face_parts import rect


def restored(original, size=(2400,1600)):
    mask = deepcopy(original)
    pixels = raster_mask(original,size)
    ImageDraw.Draw(pixels).ellipse((970,610,1030,670),fill=255)
    mask.update(bitmap=encode_bitmap(pixels,preserve_resolution=True),ops=[],feather=0)
    return mask


@pytest.mark.parametrize('mode,color',[('new',False),('new',True),('bound',True),('existing',True)])
def test_restore_adds_coverage_once_and_preserves_original_colors(canvas,monkeypatch,tmp_path,mode,color):  # noqa: F811
    e = canvas.e
    mask,lid,jobs,api = start_chat(canvas,monkeypatch,mode,color,method='restore')
    before = deepcopy(e._layers);cursor=e._cursor
    with api as (url,requests):
        configure(e.ai,url);assert e.sendMessage('补回漏选，保留原有唇色','auto')
        wait_for(lambda:bool(jobs))
        assert len(requests)==1 and 'part_restore' in jobs[0]['jobs'][0]
        corrected=restored(mask)
        result={'items':[{'id':'target','mask':corrected,'quality':{'model':'controlled neural output','warnings':[]}}]}
        mask_refinement.complete(e,result,jobs[0]['context']);finished(e)
        assert len(requests)==2
        context=json.loads(requests[1][2]['messages'][1]['content'][0]['text'])
        assert context['mode']=='mask_restore_validate' and 'face_part_scope' not in str(context)
        assert 'face_part_scope' not in json.loads(requests[0][2]['messages'][1]['content'][0]['text'])['selection']
        if mode=='new' and not color:
            assert e._layers==before and e._cursor==cursor and e._candidate==corrected
            e.undo();wait_for(lambda:settled(e));assert e._candidate==mask
            e.redo();wait_for(lambda:settled(e));assert e._candidate==corrected
        else:
            after=deepcopy(e._layers)
            assert e._layer()['mask']==corrected and e._cursor==cursor+1
            assert e.parameters['hsl_red_lightness']==6 and not e.hasSelectionDraft
            if mode!='new':
                assert e.activeLayerId==lid and len(after)==len(before)
                assert e.parameters['warmth']==4 and e.parameters['exposure']==.12
                assert e.parameters['hsl_red_saturation']==7 and e._layer()['locked']==['warmth']
                assert [l for l in after if l['id']!=lid]==[l for l in before if l['id']!=lid]
            project=tmp_path/'restored.iphoto';e.saveProject(str(project));wait_for(lambda:not e.savingProject)
            assert read_project(project)['layers']==after
            e.undo();wait_for(lambda:settled(e));assert e._layers==before
            e.redo();wait_for(lambda:settled(e));assert e._layers==after


@pytest.mark.parametrize('status',['reject','uncertain'])
def test_restore_quality_rejection_preserves_layer_and_history(canvas,monkeypatch,status):  # noqa: F811
    e=canvas.e
    mask,_,jobs,api=start_chat(canvas,monkeypatch,'existing',True,method='restore',
        verification={'status':status,'summary':'无法确认新增属于可见目标'})
    before=deepcopy(e._layers);cursor=e._cursor
    with api as (url,requests):
        configure(e.ai,url);e.sendMessage('补回漏选并提亮唇色','auto');wait_for(lambda:bool(jobs))
        mask_refinement.complete(e,{'items':[{'id':'target','mask':restored(mask)}]},jobs[0]['context']);finished(e)
        assert len(requests)==2 and e._layers==before and e._cursor==cursor
        assert e.conversation[-1]['state']=='answered' and '新增覆盖' in e.conversation[-1]['text']


def test_restore_cancel_and_late_neural_result_preserve_draft(canvas,monkeypatch):  # noqa: F811
    e=canvas.e;mask,_,jobs,api=start_chat(canvas,monkeypatch,'new',True,method='restore')
    before=deepcopy(e._layers);cursor=e._cursor
    with api as (url,requests):
        configure(e.ai,url);e.sendMessage('补回漏选','auto');wait_for(lambda:bool(jobs))
        mask_refinement.cancel(e)
        mask_refinement.complete(e,{'items':[{'id':'target','mask':restored(mask)}]},jobs[0]['context']);finished(e)
        assert len(requests)==1 and e._layers==before and e._candidate==mask and e._cursor==cursor


def test_restore_without_change_answers_without_another_cloud_request(canvas,monkeypatch):  # noqa: F811
    e=canvas.e;mask,_,jobs,api=start_chat(canvas,monkeypatch,'new',False,method='restore')
    with api as (url,requests):
        configure(e.ai,url);e.sendMessage('补回漏选','auto');wait_for(lambda:bool(jobs))
        mask_refinement.complete(e,{'items':[{'id':'target','mask':mask}]},jobs[0]['context']);finished(e)
        assert len(requests)==1 and e._candidate==mask and '未产生范围修改' in e.conversation[-1]['text']


def test_restore_cancel_quality_request_is_an_answer_not_failure(canvas,monkeypatch):  # noqa: F811
    e=canvas.e;mask,_,jobs,api=start_chat(canvas,monkeypatch,'existing',True,method='restore',delay=.6)
    before=deepcopy(e._layers);cursor=e._cursor
    with api as (url,requests):
        configure(e.ai,url);e.sendMessage('补回漏选并调唇色','auto');wait_for(lambda:bool(jobs))
        mask_refinement.complete(e,{'items':[{'id':'target','mask':restored(mask)}]},jobs[0]['context'])
        wait_for(lambda:e.ai.busy and (e._pending_request or {}).get('mask_refinement',{}).get('stage')=='verify')
        e.selection.cancelTask();finished(e)
        assert e._layers==before and e._cursor==cursor and e._candidate is None
        assert e.conversation[-1]['role']=='assistant' and e.conversation[-1]['state']=='answered'
        assert '取消' in e.conversation[-1]['text'] and 'failed' not in [m['state'] for m in e.conversation]


@pytest.mark.parametrize('case',['loss','scope','source','generation'])
def test_invalid_or_stale_restoration_cannot_publish_half_color(canvas,monkeypatch,case):  # noqa: F811
    e=canvas.e;mask,_,jobs,api=start_chat(canvas,monkeypatch,'bound',True,method='restore')
    with api as (url,requests):
        configure(e.ai,url);e.sendMessage('补回漏选并提亮','auto');wait_for(lambda:bool(jobs))
        result=restored(mask)
        if case=='loss':
            pixels=raster_mask(result,(2400,1600));ImageDraw.Draw(pixels).point((900,600),fill=0)
            result['bitmap']=encode_bitmap(pixels,preserve_resolution=True)
        elif case=='scope':result['face_part_scope']=empty_mask()
        elif case=='source':e._sha='changed'
        else:e._generation+=1
        before=deepcopy(e._layers);draft=deepcopy(e._candidate);cursor=e._cursor
        mask_refinement.complete(e,{'items':[{'id':'target','mask':result}]},jobs[0]['context']);finished(e)
        assert len(requests)==1 and e._layers==before and e._candidate==draft and e._cursor==cursor
        assert e.conversation[-1]['state']=='failed' and e.parameters['hsl_red_lightness']==0


@pytest.mark.parametrize('case',['absent','nested','semantic','untyped','invalid'])
def test_scope_validation_rejects_missing_or_nested_provenance(case):
    mask=typed_mask((300,200));scope=empty_mask(full=True)
    if case=='absent':
        with pytest.raises(ValueError,match='原始选择范围'):validate_restoration(mask,mask,(300,200))
        return
    if case=='nested':scope['face_part_scope']=deepcopy(scope)
    elif case=='semantic':scope['semantic_target']='face'
    elif case=='untyped':mask.pop('face_part')
    elif case=='invalid':scope['inverted']='false'
    with pytest.raises(ValueError):validate_mask({**mask,'face_part_scope':scope})


def test_scope_survives_validation_and_preserves_partial_intent():
    mask=typed_mask((300,200));scope=empty_mask()
    scope['ops']=[{'kind':'rect','mode':'add','points':[[.3,.3],[.5,.5]]}]
    mask['face_part_scope']=scope
    assert validate_mask(mask)['face_part_scope']==scope
    pixels=raster_mask(mask,(300,200));ImageDraw.Draw(pixels).point((95,80),fill=255)
    good={**mask,'bitmap':encode_bitmap(pixels,preserve_resolution=True)}
    validate_restoration(good,mask,(300,200))
    assert raster_mask(good,(300,200)).getpixel((95,80))>raster_mask(mask,(300,200)).getpixel((95,80))
    ImageDraw.Draw(pixels).point((190,90),fill=255)
    with pytest.raises(ValueError,match='超出'):validate_restoration({**mask,'bitmap':encode_bitmap(pixels,preserve_resolution=True)},mask,(300,200))
    ImageDraw.Draw(pixels).point((110,80),fill=0)
    with pytest.raises(ValueError):validate_restoration({**mask,'bitmap':encode_bitmap(pixels,preserve_resolution=True)},mask,(300,200))
    with pytest.raises(ValueError):validate_restoration({**good,'face_part_scope':empty_mask(full=True)},mask,(300,200))


def test_restore_method_requires_capability_and_keeps_legacy_exclusion():
    base=Recipe().to_dict();context=workspace()
    value={'layer_id':None,'recipe':None,'method':'restore'}
    with pytest.raises(ValueError):validate_request(value,'current_selection',base,[],context)
    context.update(selection_mask_restorable=True,mask_exclusion_available=False)
    assert validate_request(value,'current_selection',base,[],context)['method']=='restore'
    with pytest.raises(ValueError):validate_request({**value,'method':'exclude'},'current_selection',base,[],context)


def test_restoration_payload_uses_neutral_gain_task_without_user_claim_or_recipe():
    payload=build_payload(AISettings(provider='openai'),'核对新增lips',Recipe().to_dict(),[],
        'source','mask_restore_validate',{'selection_image':'mask','selection_overlay':'after',
        'reference_image':'before','changes_image':'gain','face_context_image':'face',
        'face_part':'lips','crop_size':[200,100],'face_part_scope':{'private':'data'},'recipe':{'secret':123}})
    contents=payload['messages'][1]['content']
    assert [x['image_url']['url'] for x in contents if x['type']=='image_url']==['source','before','after','gain','face']
    context=json.loads(contents[0]['text'])
    assert 'face_part_scope' not in context and 'recipe' not in context
    assert '蓝色' in payload['messages'][0]['content'] and '新增' in payload['messages'][0]['content']
    assert payload['response_format']['json_schema']['schema']['required']==['status','summary']


def test_gain_comparison_rejects_alpha_loss_and_old_loss_mode_rejects_gain():
    size=(300,200);mask=typed_mask(size);mask['face_part_scope']=empty_mask(full=True)
    pixels=raster_mask(mask,size);ImageDraw.Draw(pixels).point((160,90),fill=255)
    after={**mask,'bitmap':encode_bitmap(pixels,preserve_resolution=True)}
    image=Image.new('RGB',size,(100,100,100));box=(0,0,*size)
    _,gain=comparison_images(image,after,mask,box,restore=True)
    assert gain.getpixel((160,90))[2]>gain.getpixel((160,90))[0]
    assert gain.getpixel((120,80))==(100,100,100)
    with pytest.raises(ValueError):comparison_images(image,after,mask,box)
    with pytest.raises(ValueError):comparison_images(image,mask,after,box,restore=True)


def test_neural_restore_uses_original_scope_and_does_not_reduce_old_pixels(monkeypatch):
    size=(300,200);hint=typed_mask(size);hint['face_part_scope']=empty_mask(full=True)
    context={'crop':[0,0,1,1],'features':{'eyes':[[.3,.2],[.6,.2]],'mouth':[[.3,.6],[.6,.6]]},
        'anchor':[.5,.4],'mask':empty_mask(full=True)}
    def segment(image,spatial_hint,points,**options):
        assert spatial_hint==hint['face_part_scope'] and options['require_precision'] is True
        assert options['context_hint']==context['mask'] and options['part']=='lips'
        pixels=Image.new('L',size);ImageDraw.Draw(pixels).rectangle((120,80,180,100),fill=200)
        return {**hint,'bitmap':encode_bitmap(pixels,preserve_resolution=True)}, {'model':'controlled','warnings':[]}
    monkeypatch.setattr('iphoto.segmentation.face_skin.segment',segment)
    mask,quality=restore(Image.new('RGB',size),hint,context)
    before=np.asarray(raster_mask(hint,size));after=np.asarray(raster_mask(mask,size))
    assert np.all(after>=before) and np.any(after>before) and mask['face_part_scope']==hint['face_part_scope']
    assert '原始范围内补选' in quality['model']


def test_restoration_requires_precision_and_cannot_silently_use_basic_parser(monkeypatch):
    phases=[]
    monkeypatch.setattr(face_precision,'available',lambda:True)
    def unavailable(**kwargs):raise ValueError('broken precision model')
    monkeypatch.setattr(face_precision,'backend',unavailable)
    monkeypatch.setattr(face_skin,'backend',lambda **kwargs:pytest.fail('Restoration must not use a basic fallback'))
    with pytest.raises(ValueError,match='连续范围'):
        face_skin.segment(Image.new('RGB',(240,120)),rect(),[[.23,.4,1]],crop=[0,0,1,1],
            target='face',scope='region',part='lips',features={'eyes':[[.1,.2],[.4,.2]],
            'mouth':[[.15,.6],[.3,.6]]},progress=phases.append,require_precision=True)
    assert 'face_fallback' not in phases


@pytest.mark.parametrize('case',['proxy','background','external-points','mixed-method'])
def test_restore_worker_route_requires_foreground_source_and_own_context(case,monkeypatch):
    calls=[]
    monkeypatch.setattr('iphoto.segmentation.face_restore.restore',lambda *args,**kwargs:calls.append(True))
    job={'id':'part','hint':typed_mask((300,200)),'part_restore':{},'points':[]}
    if case=='external-points':job['points']=[[.5,.5,1]]
    if case=='mixed-method':job['part_boundary']={}
    image=Image.new('RGB',(300,200))
    if case=='background':
        assert service.segment_jobs(image,[job],source=image,tolerant=True)['items']==[]
    else:
        with pytest.raises(ValueError):service.segment_jobs(image,[job],source=None if case=='proxy' else image)
    assert not calls
