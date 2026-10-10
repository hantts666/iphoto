"""One prepared native scope follows refinement, choice and actual review."""
from copy import deepcopy
import io
import json
from types import SimpleNamespace
from uuid import uuid4

import numpy as np
from PIL import Image
import pytest
from PySide6.QtCore import QTimer

from iphoto.controllers import channel_auto
from iphoto.document import raster_mask
from iphoto.matte_compare import comparison_boxes, compose_regions
from iphoto.workspace import Editor
from test_ai import configure, mock_api, wait_for
from test_channel_matting import completion
from test_editor import settled
from test_matte_regions import large_candidates


def test_evidence_planning_uses_both_actual_candidates_and_does_not_mutate_them():
    source,_,masks=large_candidates()
    before=deepcopy(masks)
    planned=comparison_boxes(masks,source.size)
    from iphoto.matte_review import edge_boxes
    union=Image.fromarray(np.maximum(*(np.asarray(raster_mask(masks[name],source.size)) for name in ('channel','hair'))))
    assert planned==[list(box) for box in edge_boxes(union)] and planned
    assert masks==before


@pytest.mark.parametrize('boxes',[[],[[-1,0,200,200]],[[0,0,600,400]]])
def test_hair_worker_does_not_ignore_invalid_prepared_regions(monkeypatch,boxes):
    from iphoto.matting import worker
    source,seed,_=large_candidates()
    class Stream(io.StringIO):
        def reconfigure(self,**_):pass
    request={'op':'matte','method':'hair','id':3,'generation':7,'source_path':'source.png',
             'source_sha':'same','mask':seed,'review_boxes':boxes}
    output=Stream()
    monkeypatch.setattr(worker,'load_source',lambda _:SimpleNamespace(image=source,digest='same'))
    monkeypatch.setattr(worker.sys,'stdin',Stream(json.dumps(request)+'\n'))
    monkeypatch.setattr(worker.sys,'stdout',output)
    worker.main()
    result=json.loads(output.getvalue())
    assert result['id']==3 and result['generation']==7 and not result['ok']
    assert '原像素范围无效' in result['error']


@pytest.mark.parametrize('outcome',['native','hair','regional','mismatch','mismatch_final','cancel','stale'])
def test_same_regions_reach_hair_comparison_and_selected_review(qt_app,ai_store,tmp_path,monkeypatch,outcome):
    source,seed,old=large_candidates()
    masks={'native':deepcopy(old['channel']),**old}
    path=tmp_path/'source.png';source.save(path)
    editor=Editor(ai_store=ai_store)
    boxes=[[20,30,420,430],[500,100,900,500]]
    calls=[]
    try:
        editor.openImage(str(path));wait_for(lambda:editor.hasImage and settled(editor))
        editor._layer()['mask']=deepcopy(seed);editor._load_layer();editor._commit()
        before=deepcopy((editor._layers,editor._candidate,editor._cursor))
        token=uuid4().hex
        state={'token':token,'generation':editor._generation,'candidate':deepcopy(editor._candidate),
               'target_id':editor._selection_target_id,'hair':True,'revision':0,'bound':True,'mask':deepcopy(seed)}
        editor._pending_request={'text':'处理同一组发丝','binding':editor._document_signature(),
            'layer_id':editor._selected,'layer_snapshot':deepcopy(editor._layers),'channel_auto':state}
        actual_request=editor._request
        def request(op,**data):
            calls.append((op,deepcopy(data)))
            if op=='matte' and data.get('method')=='hair':
                assert data['review_boxes']==boxes
                QTimer.singleShot(0,lambda:channel_auto.complete(editor,{'mask':masks['hair'],
                    'quality':{'warnings':[],'elapsed_ms':2.,'edge_refinement':True}},token))
                return
            if op=='matte_candidate':assert data['review_boxes']==boxes
            return actual_request(op,**data)
        monkeypatch.setattr(editor,'_request',request)
        actual_ready=channel_auto.review_ready
        def ready(owner,result,context,generation):
            assert result['boxes']==boxes
            if (outcome=='mismatch' and result.get('comparison_candidates')
                    or outcome=='mismatch_final' and not result.get('comparison_candidates')):
                result={**result,'boxes':[[21,30,421,430],boxes[1]]}
            return actual_ready(owner,result,context,generation)
        monkeypatch.setattr(channel_auto,'review_ready',ready)
        regions=[{'edge':1,'candidate':'native'},{'edge':2,'candidate':'hair'}]
        def server(payload):
            if '抠图候选比较员' in payload['messages'][0]['content']:
                return completion({'status':'select','candidate':outcome if outcome in ('native','hair','regional') else 'native',
                    'regions':regions if outcome=='regional' else [],'summary':'受控范围连续性检查'})
            return completion({'status':'accept','summary':'受控最终事务检查，不是照片质量验收','corrections':[]})
        with mock_api(server,delay=.15) as (endpoint,requests):
            configure(editor.ai,endpoint)
            channel_auto.complete(editor,{'mask':masks['channel'],'review_boxes':deepcopy(boxes),
                'quality':{'warnings':[],'elapsed_ms':1.,'channel':'red'},
                'native_candidate':{'mask':masks['native'],'quality':{'warnings':[],'elapsed_ms':1.,'channel':'red','ai_refined':False}}},token)
            if outcome in ('cancel','stale'):
                wait_for(lambda:state.get('comparing') and editor.ai.busy,seconds=25)
                if outcome=='cancel':editor.selection.cancelTask()
                else:editor._generation+=1
            wait_for(lambda:not editor.busy and settled(editor),seconds=30)
        scoped=[data for op,data in calls if op in ('matte','matte_candidate')]
        assert state['candidate_boxes']==boxes and all(data['review_boxes']==boxes for data in scoped)
        if outcome in ('native','hair','regional'):
            expected=compose_regions(masks,boxes,regions,source.size) if outcome=='regional' else masks[outcome]
            assert editor._layers[-1]['mask']==expected and editor._cursor==before[2]+1
            assert state['review_boxes']==boxes and len(requests)==2
            assert len(scoped)==3
            editor.undo();assert (editor._layers,editor._candidate,editor._cursor)==before
        else:
            assert (editor._layers,editor._candidate,editor._cursor)==before
            if outcome in ('mismatch','mismatch_final'):
                assert '位置与本次细化范围不一致' in editor.status
                assert len(requests)==(0 if outcome=='mismatch' else 1)
        assert Image.open(path).tobytes()==source.tobytes() and not editor._pending_request
    finally:
        editor.close()
