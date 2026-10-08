"""Real preview/network workers around a controlled correction completion.

The source-resolution repair is covered separately. Here the risky boundary
is whether a second review, cancellation or stale completion can publish it.
"""
from copy import deepcopy
import json

import pytest
from iphoto.document import raster_mask
from iphoto.workspace import Editor
from iphoto.controllers import channel_auto
from test_ai import configure, mock_api, wait_for
from test_channel_matting import completion, plan
from test_editor import settled
from test_matte_correction import scene


@pytest.mark.parametrize('outcome',['accept','reject','cancel','stale','failure','again','invalid_final'])
def test_corrected_mask_requires_final_review_and_one_undo(qt_app,ai_store,tmp_path,monkeypatch,outcome):
    image,mask,_=scene();path=tmp_path/'hair.png';image.save(path)
    monkeypatch.setattr('iphoto.segmentation.precise_sam.available',lambda:True)
    editor=Editor(ai_store=ai_store)
    captured={}
    request=editor._request
    def dispatch(op,**data):
        if op=='matte' and data.get('method')=='correction':
            captured.update(data)
            return
        return request(op,**data)
    editor._request=dispatch
    try:
        editor.openImage(str(path));wait_for(lambda:editor.hasImage and settled(editor))
        editor._layer()['mask']=deepcopy(mask);editor._load_layer();editor._commit()
        original=deepcopy(editor._layers);cursor=editor._cursor
        def server(payload):
            if '独立抠图质量检查员' not in payload['messages'][0]['content']:
                return completion(plan())
            context=json.loads(payload['messages'][1]['content'][0]['text'])
            assert 'quality' not in context and 'previous_check' not in context
            if context['revision']==0 or outcome=='again':
                edge=next(int(key) for key,points in context['keep_candidates'].items() if points)
                keep=context['keep_candidates'][str(edge)][0]
                exclude=context['exclude_candidates'][str(edge)][0]
                return completion({'status':'revise','summary':'局部背景误选',
                                   'corrections':[{'edge':edge,'radius':24,'points':[[*keep,1],[*exclude,0]]}]})
            if outcome=='invalid_final':
                return {'choices':[{'finish_reason':'stop','message':{
                    'content':'复查仍有卷曲细丝缺失，需再补选；本次未返回结构化判断。'}}]}
            return completion({'status':'accept' if outcome=='accept' else 'reject',
                               'summary':'第二次检查实际输出','corrections':[]})
        with mock_api(server) as (endpoint,requests):
            configure(editor.ai,endpoint)
            assert editor.sendMessage('结合通道和AI修正当前范围','auto')
            wait_for(lambda:bool(captured),seconds=45)
            assert editor._layers==original and editor._cursor==cursor and editor._candidate is None
            assert editor._pending_request['channel_auto']['revision']==1
            if outcome=='cancel':editor.selection.cancelTask()
            elif outcome=='failure':editor._notify('纠错模型失败',True)
            else:
                if outcome=='stale':editor._generation+=1
                # Controlled candidate differs from the original; a late
                # completion must not cancel a newer transaction or commit it.
                result=deepcopy(mask)
                result['label']='已纠正的候选'
                channel_auto.complete(editor,{'mask':result,'quality':{'elapsed_ms':1.,'warnings':[]}},captured['auto_token'])
            wait_for(lambda:not editor.busy and settled(editor),seconds=45)
        if outcome=='accept':
            assert editor._layers!=original and len(editor._layers)==len(original)
            assert editor._layer()['recipe']==original[-1]['recipe'] and editor._cursor==cursor+1
            assert raster_mask(editor._layer()['mask'],image.size).tobytes()==raster_mask(mask,image.size).tobytes()
            final=deepcopy(editor._layers);editor.undo();assert editor._layers==original
            editor.redo();assert editor._layers==final
        else:
            assert editor._layers==original and editor._cursor==cursor
        assert editor._candidate is None and not editor.aiChannelPreparing
        assert len(requests)==(3 if outcome in ('accept','reject','again','invalid_final') else 2)
    finally:editor.close()
