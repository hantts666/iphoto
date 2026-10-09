import json
from copy import deepcopy

import numpy as np
import pytest
from PIL import Image, ImageDraw

from iphoto.ai_protocol import parse_auto, build_payload
from iphoto.ai_settings import AISettings
from iphoto.document import empty_mask, raster_mask, read_project
from iphoto.engine import Recipe
from iphoto.masks import encode_bitmap
from iphoto.photo_strategy import soften_effect_mask, parse_review
from iphoto.workspace import Editor
from test_ai import configure, mock_api, wait_for
from test_editor import settled


def response(value):
    return {'choices': [{'finish_reason': 'stop', 'message': {'content': json.dumps(value, ensure_ascii=False)}}]}


def develop():
    return {'action': 'develop', 'scope': 'whole_image', 'summary': '整体提亮，保持肤色自然',
            'strategy': '保留阴影层次，提高面部可读性，检查嘴周与下巴连续性',
            'recipe': Recipe(exposure=.3, shadows=12).to_dict(), 'regions': [],
            'layer_edits': [], 'repairs': [], 'group': None, 'mask_refinement': None}


def test_strategy_scope_budget_and_whole_image_smoothing():
    plan = develop()
    assert parse_auto(response(plan), Recipe().to_dict(), [])['strategy'] == plan['strategy']
    with pytest.raises(ValueError, match='不能丢弃范围'):
        parse_auto(response(plan), Recipe().to_dict(), [], current_scope='selection')
    with pytest.raises(ValueError, match='位置不足'):
        parse_auto(response(plan), Recipe().to_dict(), [], workspace={'max_new_layers': 0})
    plan['recipe']['skin_smoothing'] = 20
    with pytest.raises(ValueError, match='全图磨皮'):
        parse_auto(response(plan), Recipe().to_dict(), [])


def test_skin_transition_is_continuous_and_protects_holes_and_outside():
    alpha = Image.new('L', (440, 350))
    draw = ImageDraw.Draw(alpha)
    draw.ellipse((50, 30, 390, 310), fill=255)
    draw.ellipse((170, 200, 270, 225), fill=0)
    mask = {**empty_mask(), 'semantic_target': 'face_skin', 'bitmap': encode_bitmap(alpha)}
    result = soften_effect_mask(mask, alpha.size)
    before = np.asarray(alpha)
    after = np.asarray(raster_mask(result, alpha.size))
    assert np.all(after[before == 0] == 0)
    assert np.all(after <= before)
    assert after.max() == 255
    # Every boundary pixel reaches zero, whereas a clipped Gaussian stops at
    # roughly 50% and jumps to zero on the next pixel.
    assert np.max(np.abs(np.diff(after[100].astype(int)))) < 22
    assert result['bitmap']['sampling'] == 'alpha'
    assert result['bitmap']['width'] == alpha.width
    assert result['semantic_target'] == 'face_skin'


def test_review_sees_labeled_results_without_base64_in_json():
    payload = build_payload(AISettings.validated('qwen', 'https://dashscope.aliyuncs.com/compatible-mode/v1', 'qwen3.8-flash'), '肤色自然', Recipe().to_dict(), [],
                            'unused', 'photo_review', {'review_images': [
                                {'label': '修改前整图', 'url': 'data:image/jpeg;base64,AAA'},
                                {'label': '候选整图', 'url': 'data:image/jpeg;base64,BBB'}]})
    content = payload['messages'][1]['content']
    assert len([c for c in content if c['type'] == 'image_url']) == 2
    assert 'base64' not in content[0]['text']
    assert content[1]['text'] == '修改前整图'


def test_review_rejects_foreign_ids_and_second_revision():
    context = {'revision': 0, 'candidates': [{'layer_id': 'candidate', 'whole_image': True}]}
    plan = {'status': 'revise', 'summary': '曝光过强', 'edits': [
        {'layer_id': 'original', 'recipe': Recipe(exposure=.1).to_dict()}]}
    with pytest.raises(ValueError, match='候选层'):
        parse_review(response(plan), context)

    plan['edits'][0]['layer_id'] = 'candidate'
    assert parse_review(response(plan), context)['status'] == 'revise'
    with pytest.raises(ValueError, match='次数'):
        parse_review(response(plan), {**context, 'revision': 1})
    plan['edits'][0]['recipe']['skin_smoothing'] = 40
    with pytest.raises(ValueError, match='全图磨皮'):
        parse_review(response(plan), context)


def test_generate_reuses_selection_and_never_adds_regions_or_modifies_recipe():
    plan = {**develop(), 'action': 'generate', 'scope': 'current_selection', 'strategy': None,
            'edit_prompt': '选区内细腻精修，保留真实纹理和人物身份', 'recipe': Recipe().to_dict()}
    context = {'image_edit_available': True, 'max_new_layers': 1}
    result = parse_auto(response(plan), Recipe().to_dict(), [], current_scope='selection', workspace=context)
    assert result['action'] == 'generate' and result['regions'] == []
    with pytest.raises(ValueError, match='局部范围'):
        parse_auto(response({**plan, 'scope': 'regions'}), Recipe().to_dict(), [],
                   current_scope='selection', workspace=context)
    with pytest.raises(ValueError, match='能力不可用'):
        parse_auto(response(plan), Recipe().to_dict(), [], current_scope='selection',
                   workspace={**context, 'image_edit_available': False})


@pytest.mark.parametrize('verdict', ['accept', 'reject', 'uncertain', 'revise'])
def test_real_worker_review_before_atomic_commit(qt_app, ai_store, tmp_path, verdict):
    path = tmp_path / 'photo.png'
    Image.new('RGB', (320, 240), (78, 105, 125)).save(path)
    editor = Editor(ai_store=ai_store)
    observed = []
    def answer(payload):
        context = json.loads(payload['messages'][1]['content'][0]['text'])
        if context['mode'] == 'auto':
            return response(develop())
        assert context['mode'] == 'photo_review'
        observed.append((len(editor._layers), editor._cursor, editor.busy))
        status = verdict if not context['revision'] else 'accept'
        edits = [{'layer_id': context['candidates'][0]['layer_id'],
                  'recipe': Recipe(exposure=.12).to_dict()}] if status == 'revise' else []
        return response({'status': status, 'summary': '实图对照检查', 'edits': edits})
    try:
        with mock_api(answer) as (url, requests):
            configure(editor.ai, url)
            editor.openImage(str(path))
            wait_for(lambda: editor.hasImage and settled(editor), seconds=25)
            original = deepcopy(editor._layers)
            cursor = editor._cursor
            assert editor.sendMessage('一键自然人像', 'auto')
            wait_for(lambda: not editor.busy and settled(editor) and editor._pending_request is None, seconds=30)
            assert observed and all(count == 1 and c == cursor and busy for count, c, busy in observed)
            if verdict in ('accept', 'revise'):
                final = deepcopy(editor._layers)
                assert len(final) == 2 and editor._cursor == cursor + 1
                assert final[-1]['recipe']['exposure'] == (.12 if verdict == 'revise' else .3)
                project = tmp_path / 'checked.iphoto'
                editor.saveProject(str(project))
                wait_for(lambda: not editor.savingProject)
                assert read_project(project)['layers'] == final
                editor.undo()
                wait_for(lambda: settled(editor))
                assert editor._layers == original
                editor.redo()
                wait_for(lambda: settled(editor))
                assert editor._layers == final
            else:
                assert editor._layers == original and editor._cursor == cursor
            assert len(requests) == (3 if verdict == 'revise' else 2)
    finally:
        editor.close()


@pytest.mark.parametrize('semantic', [None, 'face_skin'])
def test_generated_pixels_commit_only_after_review_and_keep_selection_outside_exact(qt_app,ai_store,tmp_path,monkeypatch,semantic):
    from PySide6.QtCore import QTimer
    from iphoto.document import render_layers
    image=Image.new('RGB',(320,240),(78,105,125));path=tmp_path/'selected.png';image.save(path)
    editor=Editor(ai_store=ai_store)
    def answer(payload):
        context=json.loads(payload['messages'][1]['content'][0]['text'])
        if context['mode']=='auto':
            return response({'action':'generate','scope':'current_selection','summary':'在选区内生成淡紫色',
                             'edit_prompt':'选区内淡紫色','strategy':None,'recipe':Recipe().to_dict(),
                             'regions':[],'layer_edits':[],'repairs':[],'group':None,'mask_refinement':None})
        assert context['mode']=='photo_review' and len(editor._layers)==1
        return response({'status':'accept','summary':'检查选区内色彩，外部保持','edits':[]})
    original_plan=editor.ai.plan
    def plan(*args):
        arguments=list(args)
        if arguments[5]=='auto':arguments[6]={**arguments[6],'image_edit_available':True}
        return original_plan(*arguments)
    monkeypatch.setattr(editor.ai,'plan',plan)
    def generated(_url,_prompt,size,token,generation, *, scope_url):
        from iphoto.generation_scope import validate_scope_reference
        validate_scope_reference(_url, scope_url)
        QTimer.singleShot(0,lambda:editor._image_edit.completed.emit(Image.new('RGB',tuple(size),(160,120,185)),token,generation))
        return True
    monkeypatch.setattr(editor._image_edit,'start',generated)
    try:
        with mock_api(answer) as (url,requests):
            configure(editor.ai,url);editor.openImage(str(path));wait_for(lambda:editor.hasImage and settled(editor))
            editor.drawDraft('rect','replace',[[.3,.2],[.7,.8]],.025);wait_for(lambda:settled(editor))
            if semantic:
                editor._set_candidate({**editor._candidate,'semantic_target':semantic})
                wait_for(lambda:settled(editor))
            mask=deepcopy(editor._candidate);original=deepcopy(editor._layers);cursor=editor._cursor
            assert editor.sendMessage('直接生成选区内的淡紫色','auto')
            wait_for(lambda:not editor.busy and settled(editor) and editor._pending_request is None,seconds=25)
            assert len(editor._layers)==2 and editor._cursor==cursor+1 and not editor.hasSelectionDraft
            effect_mask=soften_effect_mask(mask,image.size)
            assert editor._layers[-1]['mask']==effect_mask and 'pixel_patch' in editor._layers[-1]
            effect_alpha=np.asarray(raster_mask(effect_mask,image.size))
            if semantic:
                assert np.any((effect_alpha>0)&(effect_alpha<255))
                assert np.all(effect_alpha<=np.asarray(raster_mask(mask,image.size)))
            before=np.asarray(image);after=np.asarray(render_layers(image,editor._layers));alpha=np.asarray(raster_mask(mask,image.size))
            assert np.array_equal(after[alpha==0],before[alpha==0]) and np.any(after[alpha>0]!=before[alpha>0])
            project=tmp_path/'pixels.iphoto';editor.saveProject(str(project));wait_for(lambda:not editor.savingProject)
            saved=read_project(project);assert saved['schema_version']=='1.11' and saved['layers']==editor._layers
            editor.undo();assert editor._layers==original
            editor.redo();assert editor._layers==saved['layers'] and len(requests)==2
    finally:editor.close()
