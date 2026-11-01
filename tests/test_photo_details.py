from copy import deepcopy
import json

import pytest
from PIL import Image,ImageDraw

from iphoto.engine import Recipe
from iphoto.document import new_layer,empty_mask
from iphoto.masks import encode_bitmap
from iphoto.photo_details import parse_details,regions_for_details
from iphoto.photo_strategy import render_candidate,parse_review,soften_effect_mask
from test_photo_strategy import response,develop


def detail(part='lips'):
    return {'face_id':'face-1','part':part,'name':'唇色精调','reason':'近景唇色偏灰，改善血色而保留明暗',
            'recipe':Recipe(hsl_red_saturation=12).to_dict()}


@pytest.mark.parametrize('case',['unknown','repeat','budget','skin-color','lip-blur','empty','missing-reason'])
def test_details_bind_to_inspected_face_and_capabilities(case):
    item=detail();items=[item];context={'faces':[{'id':'face-1'}],'max_refinements':2}
    if case=='unknown':item['face_id']='elsewhere'
    elif case=='repeat':items.append(deepcopy(item))
    elif case=='budget':context['max_refinements']=0
    elif case=='skin-color':item['part']='skin_texture'
    elif case=='lip-blur':item['recipe']['skin_smoothing']=10
    elif case=='empty':item['recipe']=Recipe().to_dict()
    else:item['reason']=''
    with pytest.raises(ValueError):parse_details(response({'summary':'检查实际近景','refinements':items}),context)


def test_detail_texture_permission_is_scoped_and_review_preserves_it():
    recipe=Recipe(skin_smoothing=12).to_dict();item={**detail('skin_texture'),'recipe':recipe}
    assert parse_details(response({'summary':'可见粗糙纹理','refinements':[item]}),{'faces':[{'id':'face-1'}],'max_refinements':1})['refinements']
    context={'natural_development':True,'candidates':[{'layer_id':'skin','whole_image':False,'mask_target':'face_skin','detail_part':'skin_texture'}]}
    edit=response({'status':'revise','summary':'减轻纹理处理','edits':[{'layer_id':'skin','recipe':recipe}]})
    assert parse_review(edit,context)['edits']
    recipe['exposure']=.3
    with pytest.raises(ValueError):parse_review(response({'status':'revise','summary':'brighten','edits':[{'layer_id':'skin','recipe':recipe}]}),context)


def test_native_detail_evidence_has_identity_and_does_not_refade_existing_layers(tmp_path):
    from test_face_inventory import face
    obj=face('face-1');obj['skin_crop']=[.1,.1,.5,.5]
    image=Image.new('RGB',(1000,800),(120,90,80))
    layer=new_layer('face');layer['mask']={**obj['mask'],'semantic_target':'face'}
    first=render_candidate(image,[new_layer('source',True)],[layer],[obj],tmp_path,'first',True,inspect_details=True)
    assert [f['id'] for f in first['detail_faces']]==['face-1'] and len(first['detail_images'])==4
    box=tuple(first['detail_faces'][0]['crop_box'])
    with Image.open(first['detail_images'][-2]['path']) as crop:assert crop.tobytes()==image.crop(box).tobytes()
    lip=new_layer('lips');lip['mask']={**obj['mask'],'semantic_target':'face','face_part':'lips'}
    second=render_candidate(image,[new_layer('source',True)],first['proposed']+[lip],[obj],tmp_path,'second',True,soften_ids=[lip['id']])
    assert second['proposed'][0]==first['proposed'][0]
    regions=regions_for_details([detail()],[obj])
    assert regions[0]['face_part']=='lips' and regions[0]['face_context']==obj['mask']


def test_lip_color_transition_keeps_mouth_and_surrounding_skin_protected():
    import numpy as np
    from iphoto.document import raster_mask
    alpha=Image.new('L',(320,220));draw=ImageDraw.Draw(alpha)
    draw.ellipse((35,50,285,180),fill=255);draw.ellipse((60,95,260,125),fill=0)
    mask={**empty_mask(),'semantic_target':'face','face_part':'lips','bitmap':encode_bitmap(alpha,sampling='alpha')}
    before=deepcopy(mask);result=np.asarray(raster_mask(soften_effect_mask(mask,alpha.size),alpha.size));original=np.asarray(alpha)
    assert mask==before and np.all(result<=original) and not result[original==0].any()
    assert result[70,160]==255 and 0<result[53,160]<255 and result[110,160]==0


@pytest.mark.parametrize('outcome',['apply','none','reject','invalid','cancel'])
def test_real_render_detail_stage_is_atomic_and_one_undo(qt_app,ai_store,tmp_path,monkeypatch,outcome):
    from PySide6.QtCore import QTimer
    from iphoto.workspace import Editor
    from iphoto.controllers import pixel_selections,photo_strategy
    from test_ai import mock_api,configure,wait_for
    from test_editor import settled
    from test_face_inventory import face
    path=tmp_path/'portrait.png';Image.new('RGB',(640,480),(130,90,80)).save(path)
    editor=Editor(ai_store=ai_store);phases=[];delayed=[]
    def answer(payload):
        context=json.loads(payload['messages'][1]['content'][0]['text']);mode=context['mode'];phases.append(mode)
        assert len(editor._layers)==1
        if mode=='auto':return response(develop())
        if mode=='photo_details':
            assert context['faces'][0]['id']=='face-1' and context['max_refinements']==3
            evidence=[p for p in payload['messages'][1]['content'] if p['type']=='image_url']
            assert len(evidence)==4 and evidence[-1]['image_url']['url'].startswith('data:image/png;')
            if outcome=='invalid':return response({'summary':'bad','refinements':[{'face_id':'unknown'}]})
            return response({'summary':'近景唇色偏灰，补充血色','refinements':[] if outcome=='none' else [detail()]})
        assert mode=='photo_review' and 'direction' not in context
        assert any(c.get('detail_part')=='lips' for c in context['candidates'])==(outcome!='none')
        return response({'status':'reject' if outcome=='reject' else 'accept','summary':'检查真实合成','edits':[]})
    def segment(owner,regions,summary,auto_apply=False,*,origin):
        assert auto_apply and regions[0]['face_context']==face('face-1')['mask']
        alpha=Image.new('L',(640,480));ImageDraw.Draw(alpha).ellipse((160,160,210,180),fill=255)
        layer=new_layer(regions[0]['name']);layer['recipe']=regions[0]['recipe']
        layer['mask']={**empty_mask(),'semantic_target':'face','face_part':'lips','bitmap':encode_bitmap(alpha,sampling='alpha')}
        callback=lambda:photo_strategy.prepare(owner,[layer],origin)
        if outcome=='cancel':delayed.append(callback)
        else:QTimer.singleShot(0,callback)
        return True
    monkeypatch.setattr(pixel_selections,'select_regions',segment)
    try:
        with mock_api(answer) as (url,requests):
            configure(editor.ai,url);editor.openImage(str(path));wait_for(lambda:editor.hasImage and settled(editor))
            editor._face_hints=[];editor._scene.set({'summary':'one face','objects':[face('face-1')]})
            before=deepcopy(editor._layers);cursor=editor._cursor
            assert editor.sendMessage('大胆的暖光电影风，细致修好嘴唇','auto')
            if outcome=='cancel':
                wait_for(lambda:bool(delayed),seconds=30)
                assert editor._layers==before and editor._cursor==cursor
                editor.selection.cancelTask();delayed[0]()
            wait_for(lambda:not editor.busy and settled(editor) and editor._pending_request is None,seconds=40)
            assert phases==(['auto','photo_details'] if outcome=='cancel' else ['auto','photo_details','photo_details'] if outcome=='invalid' else ['auto','photo_details','photo_review'])
            if outcome in ('reject','invalid','cancel'):assert editor._layers==before and editor._cursor==cursor
            else:
                final=deepcopy(editor._layers);assert len(final)==(2 if outcome=='none' else 3) and editor._cursor==cursor+1
                editor.undo();wait_for(lambda:settled(editor));assert editor._layers==before
                editor.redo();wait_for(lambda:settled(editor));assert editor._layers==final
    finally:editor.close()
