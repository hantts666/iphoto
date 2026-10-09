"""Real source evidence and network callbacks around a controlled pixel result."""
from copy import deepcopy
import json
from threading import Event

import pytest

from iphoto.controllers import channel_auto
from iphoto.matte_review import point_bounds,point_frame
from iphoto.workspace import Editor
from test_ai import mock_api,wait_for
from test_editor import settled
from test_matte_correction import scene
from test_matte_points import completion


@pytest.mark.parametrize('outcome',['keep','revise','reject','cancel','stale','failure','malformed'])
def test_strand_preflight_cannot_publish_without_pixel_result_and_final_review(qt_app,ai_store,tmp_path,outcome):
    image,mask,boxes=scene();path=tmp_path/'source.png';image.save(path)
    editor=Editor(ai_store=ai_store);captured={};expected={};pixel_calls=[];gate=Event();request=editor._request
    def dispatch(op,**data):
        if op=='matte' and data.get('method')=='correction':captured.update(data);pixel_calls.append(deepcopy(data));return
        return request(op,**data)
    editor._request=dispatch
    try:
        editor.openImage(str(path));wait_for(lambda:editor.hasImage and settled(editor))
        editor._layer()['mask']=deepcopy(mask);editor._load_layer();editor._commit()
        original=deepcopy(editor._layers);cursor=editor._cursor
        frame=point_frame(boxes[0],image.size);bounds={'1':point_bounds(boxes[0],frame)}
        patch={'edge':1,'radius':24,'points':[[375,375,1],[750,425,0],[450,500,2]]}
        state={'token':'preflight','generation':editor._generation,'candidate':deepcopy(editor._candidate),
               'target_id':editor._selection_target_id,'hair':True,'hair_prepared':True,'bound':True,'mask':deepcopy(mask),
               'revision':0,'reviewing':True,'correction_available':True,'context_points':True,
               'point_bounds':bounds,'review_boxes':boxes,'keep_candidates':{'1':[[375,375]]},
               'result':{'mask':deepcopy(mask),'quality':{'channel':'red','elapsed_ms':1.,'warnings':[]}}}
        editor._pending_request={'text':'修正发丝','layer_id':editor._selected,'binding':editor._document_signature(),
                                 'layer_snapshot':deepcopy(editor._layers),'channel_auto':state}
        def server(payload):
            context=json.loads(payload['messages'][1]['content'][0]['text'])
            content=payload['messages'][1]['content'][1:]
            for label,picture in zip(content[::2],content[1::2]):
                url=picture['image_url']['url']
                native=(context['mode']=='matte_points' or '质量对照' in label['text'] or '定位图（' in label['text'])
                assert url.startswith('data:image/png;base64,' if native else 'data:image/jpeg;base64,')
            if context['mode']=='matte_points':
                if '上次回复未通过程序校验' in payload['messages'][0]['content']:
                    assert payload['enable_thinking'] is False and 'thinking_budget' not in payload
                    assert payload['response_format']=={'type':'json_object'}
                else:
                    assert payload['thinking_budget']==512 and 'response_format' not in payload
                assert payload['max_tokens']==4096 and 'reasoning_effort' not in payload
                assert len([v for v in payload['messages'][1]['content'] if v['type']=='image_url'])==2+(len(context['strand_candidates']['1'])+7)//8+bool(context['strand_candidates']['1'])
                assert [r['coordinate'] for r in context['strand_structures']['1']]==context['strand_candidates']['1']
                if outcome in ('cancel','stale'):gate.wait(6)
                corrections=deepcopy(context['corrections'])
                if outcome=='revise':
                    offered=context['strand_candidates']['1']
                    if offered:corrections[0]['points'][-1]=[*offered[0],2]
                    else:corrections[0]['points'].pop()
                    expected['corrections']=deepcopy(corrections)
                if outcome=='malformed':corrections[0]['points'][0]=[400,375,1]
                return completion({'status':'revise' if outcome in ('revise','malformed') else 'reject' if outcome=='reject' else 'keep',
                                   'summary':'控制身份检查结果','corrections':corrections if outcome in ('revise','malformed') else []})
            assert context['mode']=='matte_review' and context['revision']==1
            corrected=expected.get('corrections',[patch])
            details=sum(point[2]==2 for item in corrected for point in item['points'])
            assert context['strand_detail_count']==details
            assert payload['thinking_budget']==512 and payload['max_tokens']==4096
            assert editor.ai.timer.interval()==60_000
            assert editor.ai._reply.request().transferTimeout()==editor.ai.timer.interval()
            if details:
                assert '原片发丝细节复查' in editor.ai.requestProgress and '可取消' in editor.ai.requestProgress
            assert 'keep_candidates' not in context and 'point_bounds' not in context
            assert '不能再提供取点或工具计划' in payload['messages'][0]['content']
            assert all('定位图（' not in value['text'] for value in payload['messages'][1]['content'][1:] if value['type']=='text')
            assert 'response_format' not in payload and 'reasoning_effort' not in payload
            return completion({'status':'accept','summary':'控制最终实际效果检查','corrections':[]})
        with mock_api(server,status=400 if outcome=='failure' else 200) as (endpoint,requests):
            assert editor.ai.save('qwen',endpoint,'qwen3.8-max','sk-test-only-not-a-real-key',False,True)
            channel_auto.reviewed(editor,{'status':'revise','summary':'控制首次效果检查','corrections':[patch]})
            assert state['revision']==0 and editor._layers==original and editor._cursor==cursor
            if outcome in ('cancel','stale'):
                wait_for(lambda:editor.ai.busy and bool(requests))
                if outcome=='cancel':editor.selection.cancelTask();assert not editor.ai.busy
                else:editor._generation+=1
                gate.set()
            elif outcome in ('keep','revise'):
                wait_for(lambda:bool(captured))
                assert editor._layers==original and editor._cursor==cursor and state['revision']==1
                assert captured['corrections']==(expected['corrections'] if outcome=='revise' else [patch])
                assert captured['source_point_proposal']==[patch]
                assert 'strand_candidates' in state['point_workspace']
                assert 'strand_structures' in state['point_workspace']
                assert state['point_workspace']['point_budget']==8
                count=len(pixel_calls)
                channel_auto.points_reviewed(editor,{'status':'keep','summary':'重复回调','corrections':[]})
                assert len(pixel_calls)==count and editor._layers==original
                result=deepcopy(mask);result['label']='已纠错的候选'
                channel_auto.complete(editor,{'mask':result,'quality':{'elapsed_ms':1.,'warnings':[]}},'preflight')
            wait_for(lambda:not editor.busy and settled(editor))
        if outcome in ('keep','revise'):
            assert editor._layers!=original and len(editor._layers)==len(original) and editor._cursor==cursor+1
            assert editor._layer()['recipe']==original[-1]['recipe']
            editor.undo();assert editor._layers==original
            assert len(requests)==2
        else:
            assert not captured and editor._layers==original and editor._cursor==cursor
            assert len(requests)==(2 if outcome=='malformed' else 1)
        assert editor._candidate is None and not editor.aiChannelPreparing
    finally:
        gate.set();editor.close()
