"""Native channel evidence and bounded AI controls remain one pending edit."""
from copy import deepcopy
import json
from uuid import uuid4

import numpy as np
from PIL import Image
import pytest

from iphoto.ai_protocol import build_payload
from iphoto.ai_settings import AISettings
from iphoto.channel_advisor import CONTROLS, parse_tune, render_evidence
from iphoto.controllers import channel_auto
from iphoto.engine import Recipe
from iphoto.masks import encode_bitmap
from iphoto.matting.channels import CHANNELS, channel_planes
from iphoto.workspace import Editor
from test_ai import configure, mock_api, wait_for
from test_channel_matting import completion, scene
from test_editor import settled


def proposal():
    return {'status':'propose','options':{'channel':'blue','black':35,'white':210,
            'gamma':1.4,'invert':False},'summary':'根据同位置原像素比较，保留灰阶后继续核对实际效果'}


@pytest.mark.parametrize('change',[
    {'radius':128},{'channel':'auto'},{'channel':'other'},{'black':False},
    {'black':210},{'white':300},{'gamma':float('nan')},{'gamma':True},{'invert':1}])
def test_channel_reply_cannot_change_scope_or_bypass_numeric_constraints(change):
    options=scene()[3];valid=proposal()
    assert parse_tune(completion(valid),{'options':options})==valid
    invalid={**valid,'options':{**valid['options'],**change}}
    with pytest.raises(ValueError):parse_tune(completion(invalid),{'options':options})


@pytest.mark.parametrize('result',[
    {'status':'accept','options':None,'summary':'完成'},
    {'status':'keep','options':proposal()['options'],'summary':'保持'},
    {'status':'uncertain','options':None,'summary':' '},
    {**proposal(),'recipe':{}}, {**proposal(),'options':None}])
def test_channel_advice_is_not_a_quality_acceptance(result):
    with pytest.raises(ValueError):parse_tune(completion(result),{'options':scene()[3]})


def test_incomplete_or_refused_channel_reply_is_not_executable():
    for reason in ('length','tool_calls'):
        reply=completion(proposal());reply['choices'][0]['finish_reason']=reason
        with pytest.raises(ValueError):parse_tune(reply,{'options':scene()[3]})
    reply=completion(proposal());reply['choices'][0]['message']['refusal']='refused'
    with pytest.raises(ValueError):parse_tune(reply,{'options':scene()[3]})


@pytest.mark.parametrize('provider',['openai','qwen'])
def test_channel_payload_uses_labeled_evidence_and_bounded_controls(provider):
    pictures=[{'label':'原片上下文','url':'data:image/jpeg;base64,a'},
              {'label':'原像素通道','url':'data:image/png;base64,b'}]
    options=scene()[3]
    context={'options':options,'current_options':{k:options[k] for k in CONTROLS},'review_images':pictures}
    body=build_payload(AISettings.validated(provider,'https://example.com/v1','vision'),
                       '修发丝',Recipe().to_dict(),[],'unused','channel_tune',context)
    content=body['messages'][1]['content']
    assert [v['image_url']['url'] for v in content if v['type']=='image_url']==[v['url'] for v in pictures]
    assert json.loads(content[0]['text'])['options']==options
    if provider=='openai':
        schema=body['response_format']['json_schema']['schema']
        assert set(schema['properties']['options']['anyOf'][1]['properties'])==set(CONTROLS)
    else:
        assert body['response_format']=={'type':'json_object'} and body['enable_thinking'] is False


def test_every_channel_atlas_cell_uses_identical_native_source_pixels(tmp_path):
    source,_,mask,options=scene();before=deepcopy((mask,options))
    # Distinct native RGB frequencies make resize-before-compute detectable.
    rgb=np.asarray(source).copy();rgb[:,:,1]=np.arange(source.width,dtype='uint8')[None,:]
    rgb[:,:,2]=np.arange(source.height,dtype='uint8')[:,None];source=Image.fromarray(rgb)
    pixels=source.tobytes()
    result=render_evidence(source,mask,options,tmp_path,'native')
    assert result['boxes'] and len(result['images'])==len(result['boxes'])+1
    for item,box in zip(result['images'][1:],result['boxes']):
        patch=source.crop(box);width,height=patch.size;atlas=Image.open(item['path'])
        assert item['lossless'] and max(width,height)<=256
        assert atlas.size==(width*3,(height+24)*3)
        expected=[patch]+[Image.fromarray(v).convert('RGB') for v in channel_planes(patch)]
        values=np.clip((np.asarray(patch)[:,:,0].astype('float32')-35)/175,0,1)
        expected.append(Image.fromarray(np.rint(values*255).astype('uint8')).convert('RGB'))
        for i,picture in enumerate(expected):
            x=i%3*width;y=i//3*(height+24)+24
            assert atlas.crop((x,y,x+width,y+height)).tobytes()==picture.tobytes()
    assert [item['channel'] for item in result['channel_suggestions']]==list(CHANNELS)
    assert all('score' not in item for item in result['channel_suggestions'])
    assert (mask,options)==before and source.tobytes()==pixels


def test_channel_evidence_includes_outward_edges_beyond_dense_partial_regions(tmp_path):
    alpha=np.zeros((1000,1200),dtype='uint8');alpha[200:800,200:1000]=255
    alpha[700:850,100:400]=128;alpha[700:850,800:1100]=128
    source=Image.fromarray(np.repeat(np.where(alpha[:,:,None]>0,40,180).astype('uint8'),3,axis=2))
    _,_,mask,options=scene();mask={**mask,'bitmap':encode_bitmap(Image.fromarray(alpha),sampling='alpha',preserve_resolution=True)}
    result=render_evidence(source,mask,options,tmp_path,'distributed')
    assert len(result['boxes'])==4 and len(result['images'])==5
    assert sum(box[1]<300 for box in result['boxes'])==2
    assert all(Image.open(item['path']).size==(768,840) for item in result['images'][1:])


def test_ai_evidence_reuses_native_preview_samples_but_invalidates_changed_scope(tmp_path,monkeypatch):
    from iphoto.matting import channels
    source,_,mask,options=scene();cache={};calls=[];original=channels._fields
    def fields(*args):calls.append(1);return original(*args)
    monkeypatch.setattr(channels,'_fields',fields)
    channels.native_preview(source,mask,options,cache=cache)
    first=render_evidence(source,mask,options,tmp_path,'first',cache=cache)
    changed=render_evidence(source,mask,{**options,'gamma':1.4},tmp_path,'levels',cache=cache)
    assert len(calls)==1 and first['channel_suggestions']==changed['channel_suggestions']
    assert Image.open(first['images'][1]['path']).tobytes()!=Image.open(changed['images'][1]['path']).tobytes()
    render_evidence(source,mask,{**options,'radius':10},tmp_path,'radius',cache=cache)
    assert len(calls)==2
    render_evidence(source,{**mask,'feather':1},options,tmp_path,'scope',cache=cache)
    assert len(calls)==3
    render_evidence(source.copy(),mask,options,tmp_path,'source',cache=cache)
    assert len(calls)==4


@pytest.mark.parametrize('outcome',['propose','keep','uncertain','cancel','stale','failure','scope_override'])
def test_real_evidence_and_network_keep_changes_pending_until_matte_review(qt_app,ai_store,tmp_path,monkeypatch,outcome):
    source,_,mask,options=scene();path=tmp_path/'original.png';source.save(path)
    editor=Editor(ai_store=ai_store)
    try:
        editor.openImage(str(path));wait_for(lambda:editor.hasImage and settled(editor))
        editor._layer()['mask']=deepcopy(mask);editor._load_layer();editor._commit()
        before=deepcopy((editor._layers,editor._candidate,editor._cursor))
        token=uuid4().hex
        state={'token':token,'generation':editor._generation,'candidate':deepcopy(editor._candidate),
               'target_id':editor._selection_target_id,'hair':True,'interior':False,'revision':0,
               'bound':True,'mask':deepcopy(mask)}
        editor._pending_request={'text':'用通道和AI修头发','binding':editor._document_signature(),
                                 'layer_id':editor._selected,'layer_snapshot':deepcopy(editor._layers),'channel_auto':state}
        original_request=editor._request;extractions=[]
        def request(op,**data):
            if op=='matte':extractions.append(deepcopy(data));return
            return original_request(op,**data)
        monkeypatch.setattr(editor,'_request',request)
        def server(payload):
            if outcome=='failure':return completion({'status':'propose'})
            if outcome=='scope_override':return completion({**proposal(),'options':{**proposal()['options'],'radius':128}})
            return completion(proposal() if outcome=='propose' else {'status':'uncertain' if outcome=='uncertain' else 'keep',
                               'options':None,'summary':'继续核对实际透明度，未确认效果'})
        with mock_api(server,delay=.15) as (endpoint,requests):
            configure(editor.ai,endpoint)
            channel_auto.ready(editor,{'options':{**options,'detail':True}},{'token':token},editor._generation)
            wait_for(lambda:state.get('tuning') and editor.ai.busy)
            assert '比较原像素通道参数' in editor.ai.requestProgress and '可取消' in editor.ai.requestProgress
            if outcome=='cancel':editor.selection.cancelTask()
            elif outcome=='stale':editor._generation+=1
            wait_for(lambda:bool(extractions) or editor._pending_request is None,seconds=15)
        assert (editor._layers,editor._candidate,editor._cursor)==before
        if outcome in ('propose','keep','uncertain'):
            assert len(extractions)==1 and len(requests)==1
            actual=extractions[0]['channel_options']
            assert actual['radius']==options['radius'] and actual['interior'] is False
            assert actual['ai'] and actual['color'] and actual['detail'] is False
            assert actual['gamma']==(1.4 if outcome=='propose' else options['gamma'])
            assert actual['channel']==('blue' if outcome=='propose' else options['channel'])
            assert state['channel_tuning']['status']==outcome and editor._pending_request
            editor.selection.cancelTask()
        else:
            assert not extractions
            if outcome=='cancel':assert len(requests)<=1
            else:assert len(requests)==(2 if outcome in ('failure','scope_override') else 1)
        wait_for(lambda:not editor.busy and settled(editor))
        assert source.tobytes()==Image.open(path).tobytes()
    finally:editor.close()
