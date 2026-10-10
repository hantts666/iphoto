"""Provider rejection is distinct from compatibility errors and never applies edits."""

from copy import deepcopy

from PIL import Image
from PySide6.QtCore import QTimer
import pytest

from iphoto.ai import AIController
from iphoto.ai_errors import content_inspection_failed
from iphoto.ai_protocol import parse_auto
from iphoto.engine import Recipe
from iphoto.workspace import Editor
from test_ai import configure, mock_api, wait_for
from test_editor import settled
from test_photo_strategy import develop, response


@pytest.mark.parametrize('error', [
    {'error': {'code': 'data_inspection_failed'}},
    {'error': {'type': 'data_inspection_failed'}},
    {'code': 'DataInspectionFailed'},
])
def test_documented_content_error_is_reported_without_upstream_text(qt_app, ai_store, tmp_path, error):
    error['message'] = 'sk-test-only-not-a-real-key https://private.invalid/?signature=secret data:image/png;base64,PRIVATE'
    photo=tmp_path/'photo.png'
    Image.new('RGB',(80,80)).save(photo)
    with mock_api(error,status=400) as (url,requests):
        ai=AIController(store=ai_store)
        try:
            configure(ai,url)
            failures,plans=[],[]
            ai.failure.connect(failures.append)
            ai.planReady.connect(lambda *args:plans.append(args))
            assert ai.plan('选择皮肤',Recipe().to_dict(),[],str(photo),7,'selection')
            context=ai._context
            wait_for(lambda:not ai.busy)
            assert len(requests)==1 and not plans and len(failures)==1
            assert '内容检查未通过' in ai.message and '模型不兼容' not in ai.message
            assert failures[0]==ai.message and ai.isError
            assert all(value not in ai.message for value in ('sk-test','private.invalid','signature','base64','PRIVATE'))
            assert context['secret']=='' and not context['body']
        finally:
            ai.close()


@pytest.mark.parametrize('body', [b'not json',b'[]',b'null',b'{"error":[]}',
    b'{"error":{"code":123}}',b'{"error":{"message":"data_inspection_failed"}}',
    b'{"error":{"code":"invalid_parameter"}}',b'['*2000,b' '*2_000_001],
    ids=['not-json','array','null','bad-error-shape','numeric-code','message-only','unknown-code','deep-json','oversized'])
def test_unknown_or_invalid_error_does_not_claim_content_rejection(body):
    assert not content_inspection_failed(body)


@pytest.mark.parametrize('stage',['auto','photo_review'])
def test_content_rejection_releases_transaction_without_partial_layers(qt_app,ai_store,tmp_path,monkeypatch,stage):
    photo=tmp_path/'portrait.png'
    Image.new('RGB',(320,240),(110,85,70)).save(photo)
    original_bytes=photo.read_bytes()
    editor=Editor(ai_store=ai_store)
    try:
        with mock_api({'error':{'code':'data_inspection_failed','message':'opaque upstream text'}},status=400) as (url,requests):
            configure(editor.ai,url)
            editor.openImage(str(photo))
            wait_for(lambda:editor.hasImage and settled(editor))
            before=deepcopy(editor._layers)
            cursor,generation=editor._cursor,editor._generation
            candidate,target=deepcopy(editor._candidate),editor._selection_target_id
            if stage=='photo_review':
                original_plan=editor.ai.plan
                def plan(*args):
                    if args[5]!='auto':return original_plan(*args)
                    result=parse_auto(response(develop()),args[1],args[2],workspace=args[6])
                    result['mode']='auto'
                    QTimer.singleShot(0,lambda:editor.ai.planReady.emit(result,args[4]))
                    return True
                monkeypatch.setattr(editor.ai,'plan',plan)
            assert editor.sendMessage('自然人像精修','auto')
            wait_for(lambda:not editor.busy and settled(editor) and editor._pending_request is None,seconds=25)
            assert len(requests)==1 and editor._layers==before and editor._cursor==cursor
            assert editor._generation==generation and editor._candidate==candidate and editor._selection_target_id==target
            assert '内容检查未通过' in editor.status
            assert editor._conversation[-1]['state']=='failed'
            assert photo.read_bytes()==original_bytes
    finally:
        editor.close()
