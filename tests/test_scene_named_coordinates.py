"""Named scene geometry stays strict and uses the original Qt request lifecycle."""
from copy import deepcopy
import json

import pytest

from iphoto.ai_protocol import build_payload
from iphoto.ai_settings import AISettings
from iphoto.engine import Recipe
from iphoto.scene import parse_scene
from test_ai import configure, mock_api, wait_for
from test_canvas_ui import canvas  # noqa: F401
from test_editor import settled


def body():
    return {'status':'analyzed','summary':'定位一个对象，随后生成像素范围','objects':[
        {'name':'目标','category':'物体','box':{'left':100,'top':150,'right':800,'bottom':900},
         'point':{'x':450,'y':550}}]}


def completion(value):
    return {'choices':[{'finish_reason':'stop','message':{'content':json.dumps(value)}}]}


def invalid():
    value=body()
    value['objects'][0]['box']['left']=-20
    return value


def snapshot(editor):
    return deepcopy((editor._layers,editor._history,editor._cursor,editor._generation))


def done(editor):
    wait_for(lambda:not editor.ai.busy and editor._pending_request is None and settled(editor))


def test_named_geometry_matches_legacy_geometry_and_does_not_mutate_reply():
    named=completion(body())
    before=deepcopy(named)
    legacy=body()
    legacy['objects'][0].update(box=[100,150,800,900],point=[450,550])
    assert parse_scene(named)==parse_scene(completion(legacy))
    assert named==before
    assert parse_scene(named)['objects'][0]['anchor']==pytest.approx([450/999,550/999])


@pytest.mark.parametrize('bad',['negative','overflow','bool','nan','string','missing_box','extra_box',
                               'missing_point','extra_point','hybrid','reversed','outside','empty'])
def test_invalid_named_geometry_is_rejected_without_partial_catalog(bad):
    value=body()
    item=deepcopy(value['objects'][0])
    value['objects'].append(item)
    box,point=item['box'],item['point']
    if bad=='negative':box['left']=-1
    elif bad=='overflow':box['right']=1000
    elif bad=='bool':point['x']=True
    elif bad=='nan':point['y']=float('nan')
    elif bad=='string':box['top']='150'
    elif bad=='missing_box':box.pop('top')
    elif bad=='extra_box':box['width']=700
    elif bad=='missing_point':point.pop('y')
    elif bad=='extra_point':point['confidence']=.9
    elif bad=='hybrid':item['point']=[450,550]
    elif bad=='reversed':box.update(left=800,right=100)
    elif bad=='outside':point['x']=900
    else:item['box']={}
    with pytest.raises(ValueError,match='定位坐标'):
        parse_scene(completion(value))


def test_named_schema_and_no_target_reply_keep_bounded_contract():
    payload=build_payload(AISettings(provider='openai'),'分析画面',Recipe().to_dict(),[],
                          'data:image/jpeg;base64,fixture','scene')
    properties=payload['response_format']['json_schema']['schema']['properties']['objects']['items']['properties']
    for field,keys in [('box',{'left','top','right','bottom'}),('point',{'x','y'})]:
        schema=properties[field]
        assert schema['type']=='object' and not schema['additionalProperties']
        assert set(schema['required'])==keys
        assert all(value['minimum']==0 and value['maximum']==999 for value in schema['properties'].values())
    assert parse_scene(completion({'status':'unsupported','summary':'目标不可见','objects':[]}))['status']=='unsupported'


def test_first_scene_accepts_named_reply_once_without_editing_document(canvas,pixel_protocol_stub):  # noqa: F811
    ui,editor=canvas,canvas.e
    before=snapshot(editor)
    with mock_api(completion(body())) as (url,requests):
        configure(editor.ai,url)
        ui.click('analyzeSceneButton')
        done(editor)
        assert len(requests)==1 and snapshot(editor)==before
        assert editor._scene.catalog['objects'][0]['anchor']==pytest.approx([450/999,550/999])
        assert editor.conversation[-1]['state']=='catalog'


def test_invalid_named_first_reply_is_corrected_once_with_visible_progress(canvas,pixel_protocol_stub):  # noqa: F811
    ui,editor=canvas,canvas.e
    before=snapshot(editor)
    def reply(payload):return completion(invalid() if len(requests)==1 else body())
    with mock_api(reply,delay=.4) as (url,requests):
        configure(editor.ai,url)
        ui.click('analyzeSceneButton')
        wait_for(lambda:len(requests)==2 and editor.ai.busy)
        assert ui.find('aiRequestProgress').isVisible() and ui.find('cancelAiRequest').isVisible()
        assert '校验' in requests[1][2]['messages'][0]['content']
        done(editor)
        assert len(requests)==2 and editor._scene.catalog and snapshot(editor)==before


def test_repeated_invalid_refresh_preserves_existing_catalog_cache_and_checks(canvas,pixel_protocol_stub):  # noqa: F811
    editor=canvas.e
    parsed=parse_scene(completion(body()))
    editor._scene.set(parsed)
    editor._scene.selected.add('object-1')
    old=deepcopy((editor._scene.catalog,editor._scene.precise,editor._scene.selected))
    before=snapshot(editor)
    with mock_api(completion(invalid())) as (url,requests):
        configure(editor.ai,url)
        editor.analyzeScene(True)
        done(editor)
        assert len(requests)==2 and snapshot(editor)==before
        assert (editor._scene.catalog,editor._scene.precise,editor._scene.selected)==old
        assert editor.conversation[-1]['state']=='failed'


def test_cancel_during_coordinate_correction_keeps_document_and_empty_catalog(canvas,pixel_protocol_stub):  # noqa: F811
    ui,editor=canvas,canvas.e
    before=snapshot(editor)
    def reply(payload):return completion(invalid() if len(requests)==1 else body())
    with mock_api(reply,delay=.6) as (url,requests):
        configure(editor.ai,url)
        ui.click('analyzeSceneButton')
        wait_for(lambda:len(requests)==2 and editor.ai.busy)
        ui.click('cancelAiRequest')
        done(editor)
        assert len(requests)==2 and editor._scene.catalog is None and snapshot(editor)==before
        assert '取消' in editor.status
