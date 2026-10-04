"""Stable face associations in AI requests, independent of names and ordering."""
from copy import deepcopy
import json

import pytest

from iphoto.controllers import conversation,face_inventory
from iphoto.ai_layer_edits import validate_layer_edits,LAYER_EDITS_SCHEMA
from iphoto.document import new_layer
from test_ai import configure,mock_api,wait_for
from test_ai_layer_edits import response
from test_editor import settled
from test_selection_ui_modes import ui  # noqa: F401
from test_face_binding import layer,skin,REF
from test_face_inventory import face,owner


def context_owner():
    first=face('local-face-1',.2);second=face('local-face-2',.2)
    second['mask']['ops'][0]['points']=[[.2,.65],[.4,.65],[.4,.85],[.2,.85]]
    first['anchor']=[.3,.3];second['anchor']=[.3,.75]
    editor=owner([first,second]);editor._sha=REF['source_sha256']
    editor._width=500;editor._height=1500
    editor._layers=[layer(skin(label='手动选区'),'任意名称'),
                    layer(skin({**REF,'face_id':'local-face-2'},label='手动选区'),'另一个名称')]
    editor._layers[1]['mask']['ops'][0]['points']=[[.2,.65],[.4,.65],[.4,.85],[.2,.85]]
    return editor


def test_face_targets_stay_exact_after_layer_rename_reordering_and_manual_range_changes():
    e=context_owner();before=deepcopy(e._layers)
    targets=face_inventory.layer_targets(e)
    first,second=e._layers
    assert targets[first['id']]['id']=='local-face-1' and targets[second['id']]['id']=='local-face-2'
    assert targets[first['id']]['display_name'].endswith('上方') and targets[second['id']]['display_name'].endswith('下方')
    assert e._layers==before
    first['name']='完全改名';first['mask']['ops'].append({'kind':'brush','mode':'subtract','points':[[.3,.3]],'radius':.003})
    e._layers.reverse()
    assert face_inventory.layer_targets(e)==targets
    assert all(set(target)=={'id','name','display_name','mask_target'} for target in targets.values())
    assert e._sha not in json.dumps(targets)


@pytest.mark.parametrize('case',('stale','other_face','inverted','object','whole_face','heal','inpaint','group'))
def test_unverified_face_ranges_are_not_offered_to_ai(case):
    e=context_owner();item=e._layers[0]
    if case=='stale':item['mask']['face_binding']['source_sha256']='b'*64
    elif case=='other_face':item['mask']['face_binding']['face_id']='unknown'
    elif case=='inverted':item['mask']['inverted']=True
    elif case=='object':item['mask'].pop('semantic_target')
    elif case=='whole_face':item['mask']['semantic_target']='face'
    elif case=='group':item['kind']='group'
    else:item[case]={'method':'telea'}
    result=face_inventory.layer_targets(e)
    assert item['id'] not in result and len(result)==1


def test_unique_legacy_skin_label_can_supply_identity_but_partial_or_ambiguous_labels_cannot(monkeypatch):
    e=context_owner();item=e._layers[0];item['mask'].pop('face_binding')
    item['mask']['label']='local-face-1 · 面部皮肤'
    assert face_inventory.layer_targets(e)[item['id']]['id']=='local-face-1'
    item['mask']['label']='local-face-1 · 面部局部'
    assert item['id'] not in face_inventory.layer_targets(e)
    item['mask']['label']='local-face-1 · 面部皮肤'
    faces=deepcopy(face_inventory.current(e));faces[1]['mask']['label']=faces[0]['mask']['label']
    monkeypatch.setattr(face_inventory,'current',lambda _:faces)
    assert item['id'] not in face_inventory.layer_targets(e)


def test_recognition_and_layer_context_share_exact_face_ids_without_exporting_masks_or_source_hashes():
    e=context_owner();e._layers.append(new_layer('全图',True))
    context=conversation._spatial_context(e)
    faces={f['id']:f for f in context['detected_faces']}
    for row in context['existing_layers']:
        if row['id']==e._layers[-1]['id']:
            assert 'face_target' not in row
        else:
            target=row['face_target']
            assert faces[target['id']]['display_name']==target['display_name']
            assert faces[target['id']]['name']==target['name']
    assert e._sha not in json.dumps(context) and 'bitmap' not in json.dumps(context)


@pytest.mark.parametrize('face_id',(None,True,'local-face-2','missing'))
def test_face_edit_must_confirm_the_exact_face_and_layer_pair_before_any_mutation(face_id):
    e=context_owner();targets=face_inventory.layer_targets(e)
    offered=[{**l,'face_target':targets[l['id']]} for l in e._layers]
    snapshot=deepcopy(e._layers)
    value=[{'layer_id':offered[0]['id'],'face_id':face_id,'recipe':None,'visible':False,'opacity':None}]
    with pytest.raises(ValueError,match='人脸身份'):validate_layer_edits(value,offered)
    assert e._layers==snapshot


def test_verified_face_edit_is_accepted_and_non_face_edit_keeps_legacy_compatibility():
    e=context_owner();item=e._layers[0]
    offered={**item,'face_target':face_inventory.layer_targets(e)[item['id']]}
    value={'layer_id':item['id'],'face_id':'local-face-1','recipe':None,'visible':False,'opacity':None}
    assert validate_layer_edits([value],[offered])[0]['visible'] is False
    with pytest.raises(ValueError,match='没有已验证'):validate_layer_edits([value],[item])
    value.pop('face_id')
    assert validate_layer_edits([value],[item])[0]['visible'] is False
    assert 'face_id' in LAYER_EDITS_SCHEMA['items']['required']


@pytest.mark.parametrize('recover',(True,False))
def test_wrong_face_layer_pair_is_retried_before_any_layer_is_changed(ui,recover):  # noqa: F811
    editor,_,_,warnings=ui
    model=context_owner()
    editor._face_hints=model._face_hints
    for item in model._layers:item['mask']['face_binding']['source_sha256']=editor._sha
    editor._layers.extend(model._layers);editor._commit();editor._change()
    wait_for(lambda:settled(editor))
    before=deepcopy(editor._layers);cursor=editor._cursor;upper=model._layers[0];lower=model._layers[1]
    calls=[]
    def reply(payload):
        # Both the bad response and the corrected retry must see unchanged layers.
        assert editor._layers==before
        context=json.loads(payload['messages'][1]['content'][0]['text'])
        offered=next(l for l in context['existing_layers'] if l['id']==upper['id'])
        assert offered['face_target']['id']=='local-face-1'
        assert 'face_binding' not in context['selection']
        calls.append(payload)
        target=upper if recover and len(calls)>1 else lower
        edit={'layer_id':target['id'],'face_id':'local-face-1',
              'recipe':{**target['recipe'],'exposure':.4},'visible':None,'opacity':None}
        return response(editor._recipe,[edit])
    with mock_api(reply) as (url,requests):
        configure(editor.ai,url)
        assert editor.sendMessage('只提亮上方已有的人脸层','auto')
        wait_for(lambda:not editor.ai.busy and editor._pending_request is None and settled(editor))
        assert len(requests)==2 and len(calls)==2
        if recover:
            assert upper['recipe']['exposure']==.4 and editor._cursor==cursor+1
            assert [l for l in editor._layers if l['id']!=upper['id']]==[l for l in before if l['id']!=upper['id']]
            assert upper['mask']==before[-2]['mask']
            editor.undo();wait_for(lambda:settled(editor));assert editor._layers==before
        else:
            assert editor._layers==before and editor._cursor==cursor
            assert editor._conversation[-1]['state']=='failed' and '人脸身份' in editor._conversation[-1]['text']
    assert not warnings,warnings
