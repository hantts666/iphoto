"""AI point grounding and atomic range/color publication, not model accuracy."""
from copy import deepcopy
import base64
from io import BytesIO
import json
from types import SimpleNamespace

from PIL import Image, ImageDraw
import pytest

from iphoto.ai_mask_refinement import (eligible, map_points, mask_data_url,
    parse_points, prepare_crop, validate_request, validate_result)
from iphoto.ai_protocol import build_payload, parse_auto
from iphoto.ai_settings import AISettings
from iphoto.controllers import mask_refinement, worker_bridge
from iphoto.document import empty_mask, new_layer, raster_mask, read_project
from iphoto.engine import Recipe
from iphoto.masks import encode_bitmap
from test_ai import configure, mock_api, wait_for
from test_canvas_ui import canvas  # noqa: F401
from test_editor import settled


def response(plan):
    return {'choices': [{'finish_reason': 'stop', 'message': {'content': json.dumps(plan)}}]}


def proposal(current=None, scope='current_selection', lid=None, recipe=None):
    return {'action': 'refine_mask', 'scope': scope, 'summary': '检查已有误选范围',
            'recipe': current or Recipe().to_dict(), 'regions': [], 'layer_edits': [],
            'group': None, 'repairs': [], 'mask_refinement': {'layer_id': lid, 'recipe': recipe}}


def workspace():
    return {'mask_refinement_available': True, 'selection_mask_refinable': True,
            'selection': {'face_part': 'lips'}}


def typed_mask(size=(2400, 1600)):
    alpha = Image.new('L', size)
    ImageDraw.Draw(alpha).rectangle((size[0]//3, size[1]//3, size[0]//2, size[1]//2), fill=255)
    mask = empty_mask()
    mask.update(bitmap=encode_bitmap(alpha, preserve_resolution=True), semantic_target='face',
                face_part='lips', label='嘴唇')
    return mask


def test_refinement_protocol_keeps_top_level_and_target_locks():
    current = Recipe(exposure=.4).to_dict()
    layer = {'id': 'lip-id', 'mask_refinable': True, 'mask_part': 'lips',
             'recipe': Recipe(exposure=.12, warmth=4).to_dict(), 'locked': ['warmth']}
    plan = proposal(current, 'existing_layers', 'lip-id', {**layer['recipe'], 'warmth': 40, 'hsl_red_lightness': 6})
    result = parse_auto(response(plan), current, [], 'whole_image', [layer], workspace=workspace())
    assert result['mask_refinement']['recipe']['warmth'] == 4
    assert result['mask_refinement']['recipe']['exposure'] == .12
    assert result['mask_refinement']['recipe']['hsl_red_lightness'] == 6
    assert validate_request({'layer_id': None, 'recipe': current}, 'current_selection', current, [], workspace())['recipe'] is None


@pytest.mark.parametrize('case', ['unavailable', 'whole-face', 'cross-target', 'scope', 'regions',
    'top-recipe', 'partial-recipe', 'smoothing', 'range', 'other-action', 'other-fields', 'unknown-layer'])
def test_bad_refinement_cannot_broaden_or_mix_actions(case):
    current = Recipe().to_dict(); plan = proposal(); context = workspace()
    if case == 'unavailable': context['mask_refinement_available'] = False
    elif case == 'whole-face': context['selection_mask_refinable'] = False
    elif case == 'cross-target': plan['mask_refinement']['layer_id'] = 'other'
    elif case == 'scope': plan['scope'] = 'existing_layers'
    elif case == 'regions': plan['regions'] = [{}]
    elif case == 'top-recipe': plan['recipe'] = Recipe(exposure=.2).to_dict()
    elif case == 'partial-recipe': plan['mask_refinement']['recipe'] = {'exposure': .2}
    elif case == 'smoothing': plan['mask_refinement']['recipe'] = Recipe(skin_smoothing=20).to_dict()
    elif case == 'range': plan['mask_refinement']['recipe'] = {**current, 'sharpness': 101}
    elif case == 'other-action': plan.update(action='adjust', scope='current_selection')
    elif case == 'other-fields': plan['repairs'] = [{}]
    elif case == 'unknown-layer':
        plan.update(scope='existing_layers'); plan['mask_refinement']['layer_id'] = 'missing'
    with pytest.raises(ValueError):
        parse_auto(response(plan), current, [], 'local' if case == 'unknown-layer' else 'selection', [], workspace=context)


def test_payload_orders_original_mask_overlay_and_removes_private_context():
    payload = build_payload(AISettings(provider='openai'), '去掉多选皮肤', Recipe().to_dict(), [],
        'original', 'mask_points', {'selection_image': 'mask', 'selection_overlay': 'overlay',
        '_mask_path': 'private-file', 'face_part': 'lips', 'crop_size': [201, 91], 'existing_layers': ['secret']})
    contents = payload['messages'][1]['content']
    assert [item['image_url']['url'] for item in contents if item['type'] == 'image_url'] == ['original', 'mask', 'overlay']
    context = json.loads(contents[0]['text'])
    assert set(context) == {'request', 'mode', 'face_part', 'crop_size', 'coordinate_system', 'has_face_context'}
    assert context['has_face_context'] is False
    assert context['coordinate_system'] == 'local_0_to_999'
    assert 'exclusions' in payload['response_format']['json_schema']['schema']['required']
    ordinary = build_payload(AISettings(), '调色', Recipe().to_dict(), [], 'original', 'auto', {'selection_image': 'mask'})
    assert [x['image_url']['url'] for x in ordinary['messages'][1]['content'] if x['type'] == 'image_url'] == ['original', 'mask']


def point_context(tmp_path):
    path = tmp_path/'mask.png'
    mask = Image.new('L', (101, 81)); ImageDraw.Draw(mask).rectangle((20, 15, 80, 65), fill=255)
    mask.save(path)
    return {'_mask_path': str(path), 'crop_size': list(mask.size)}


def point_plan(points, **extra):
    return response({'status': 'planned', 'summary': '排除误选皮肤', 'exclusions': points, **extra})


def test_points_validate_white_interior_and_map_pixel_centers(tmp_path):
    context = point_context(tmp_path)
    result = parse_points(point_plan([[500, 400]]), context)
    assert result['points'] == [[500/999, 400/999, 0]]
    assert map_points([[0, 0, 0], [1, 1, 0]], [10, 20, 61, 101], (201, 301)) == [[.05, 20/300, 0], [.3, 1/3, 0]]
    assert parse_points(point_plan([], status='unsupported'), context)['points'] == []
    with Image.open(BytesIO(base64.b64decode(mask_data_url(context['_mask_path']).split(',')[1]))) as image:
        assert image.mode == 'L' and list(image.size) == context['crop_size']


@pytest.mark.parametrize('points', [[], [[0, 0]], [[True, 400]], [[1000, 400]], [[float('nan'), 400]],
    [[500, 400], [500.01, 400.01]], [[500, 400, 0]], [[500, '400']], [[500, 400]]*6])
def test_invalid_or_outside_points_leave_mask_untouched(tmp_path, points):
    context = point_context(tmp_path); before = (tmp_path/'mask.png').read_bytes()
    with pytest.raises(ValueError): parse_points(point_plan(points), context)
    assert (tmp_path/'mask.png').read_bytes() == before


def test_crop_is_native_bounded_and_strips_metadata():
    image = Image.new('RGB', (300, 200), (90, 110, 130)); image.info['exif'] = b'private'
    mask = typed_mask(image.size)
    original, alpha, overlay, box = prepare_crop(image, mask)
    assert box == [36, 2, 215, 165]
    assert original.size == alpha.size == overlay.size == (179, 163)
    assert original.tobytes() == image.crop(box).tobytes()
    assert not original.info and not alpha.info and not overlay.info
    assert eligible(mask) and not eligible({**mask, 'inverted': True})
    assert validate_result(mask, mask, image.size) == mask
    with pytest.raises(ValueError): validate_result(mask, mask, (301, 200))
    with pytest.raises(ValueError): validate_result({**mask, 'face_part': 'nose'}, mask, image.size)
    large = {**mask, 'bitmap': encode_bitmap(Image.new('L', (2500, 2200), 255), preserve_resolution=True)}
    with pytest.raises(ValueError, match='过大'): prepare_crop(Image.new('RGB', (2500, 2200)), large)


def setup(ui, mode):
    e = ui.e; mask = typed_mask(); lid = None
    if mode != 'new':
        layer = new_layer('已改名的唇色'); layer['mask'] = mask
        layer['recipe'] = Recipe(exposure=.12, warmth=4, hsl_red_saturation=7).to_dict()
        layer['locked'] = ['warmth']; e._layers.append(layer)
        e._selected = layer['id']; e._load_layer(); e._commit(); e._change(); lid = layer['id']
        wait_for(lambda: settled(e))
    if mode in ('new', 'bound'):
        e.beginSelection('empty' if mode == 'new' else 'current')
        e._set_candidate(mask)
        if mode == 'bound': e._selection_target_id = lid
        wait_for(lambda: settled(e) and ui.w.property('selectionPreviewReady'))
    else:
        e._selected = e._layers[0]['id']; e._load_layer(); e._commit(); e._change()
        wait_for(lambda: settled(e))
    return mask, lid


def start_chat(ui, monkeypatch, mode, color, *, delay=0, bad_points=False, review=None, points_reply=None, verification=None):
    e = ui.e; mask, lid = setup(ui, mode)
    pending_jobs = []
    request = e._request
    def intercept(op, **data):
        if op == 'segment': pending_jobs.append(deepcopy(data)); return True
        return request(op, **data)
    monkeypatch.setattr(e, '_request', intercept)
    monkeypatch.setattr('iphoto.segmentation.precise_sam.available', lambda: True)
    monkeypatch.setattr('iphoto.controllers.pixel_selections.available', lambda: True)
    def body(payload):
        context = json.loads(payload['messages'][1]['content'][0]['text'])
        if context['mode'] == 'mask_points':
            return response(points_reply) if points_reply is not None else point_plan([[0, 0] if bad_points else [500, 400]])
        if context['mode'] == 'mask_validate':
            return response(verification or {'status': 'accept', 'summary': '本次减少保留真实目标'})
        if context['mode'] == 'mask_review': return response(review(context) if callable(review) else review or {'status':'keep', 'summary':'未发现可明确排除的残留', 'exclude_regions':[]})
        target = next((l for l in context['existing_layers'] if l['id'] == lid), None)
        recipe = {**(target['recipe'] if target else Recipe().to_dict()), 'hsl_red_lightness': 6, 'warmth': 40} if color else None
        return response(proposal(context['current_recipe'], 'existing_layers' if mode == 'existing' else 'current_selection',
                                 lid if mode == 'existing' else None, recipe))
    ui.w.setProperty('chatOpen', True)
    return mask, lid, pending_jobs, mock_api(body, delay=delay)


def finished(e):
    wait_for(lambda: not e.ai.busy and e._pending_request is None and settled(e))


def neural_result(job, original, size=(2400, 1600)):
    # Controlled result delivery tests publication; live QA separately uses SAM2.
    pixels = raster_mask(original, size)
    point = job['jobs'][0]['points'][0]; x, y = round(point[0]*(size[0]-1)), round(point[1]*(size[1]-1))
    ImageDraw.Draw(pixels).ellipse((x-18, y-18, x+18, y+18), fill=0)
    mask = deepcopy(original); mask['bitmap'] = encode_bitmap(pixels, preserve_resolution=True)
    return {'items': [{'id': 'target', 'mask': mask, 'quality': {'model': 'controlled result', 'warnings': []}}]}


@pytest.mark.parametrize('mode,color', [('new', False), ('new', True), ('existing', True), ('bound', True)])
def test_chat_range_and_color_are_one_undoable_transaction(canvas, monkeypatch, tmp_path, mode, color):  # noqa: F811
    ui = canvas; e = ui.e
    mask, lid, jobs, api = start_chat(ui, monkeypatch, mode, color)
    before = deepcopy(e._layers); cursor = e._cursor
    with api as (url, requests):
        configure(e.ai, url); assert e.sendMessage('把当前误选皮肤去掉并保留原有调色', 'auto')
        wait_for(lambda: bool(jobs))
        assert len(requests) == 2 and e._layers == before and e._candidate == (mask if mode != 'existing' else None)
        assert e._cursor == cursor
        context = json.loads(requests[1][2]['messages'][1]['content'][0]['text'])
        assert context['face_part'] == 'lips' and '_mask_path' not in context
        result = neural_result(jobs[0], mask)
        mask_refinement.complete(e, result, jobs[0]['context']); finished(e)
        corrected = result['items'][0]['mask']
        if mode == 'new' and not color:
            assert e._layers == before and e._cursor == cursor and e._candidate == corrected
            e.undo(); wait_for(lambda: settled(e)); assert e._candidate == mask
            e.redo(); wait_for(lambda: settled(e)); assert e._candidate == corrected
        else:
            changed = deepcopy(e._layers)
            assert e._layer()['mask'] == corrected and not e.hasSelectionDraft and e._cursor == cursor+1
            assert e.parameters['hsl_red_lightness'] == 6
            if mode != 'new':
                assert e.activeLayerId == lid and len(changed) == len(before)
                assert e.parameters['exposure'] == .12 and e.parameters['warmth'] == 4 and e._layer()['locked'] == ['warmth']
                assert [l for l in changed if l['id'] != lid] == [l for l in before if l['id'] != lid]
            else:
                assert changed[:-1] == before and e.parameters['exposure'] == 0
            project = tmp_path/'refined.iphoto'; e.saveProject(str(project)); wait_for(lambda: not e.savingProject)
            assert read_project(project)['layers'] == changed
            e.undo(); wait_for(lambda: settled(e)); assert e._layers == before
            e.redo(); wait_for(lambda: settled(e)); assert e._layers == changed


@pytest.mark.parametrize('mutation', ['candidate', 'lock', 'source', 'target', 'generation'])
def test_pending_result_rejects_changed_input_without_half_color(canvas, monkeypatch, mutation):  # noqa: F811
    ui = canvas; e = ui.e
    mask, lid, jobs, api = start_chat(ui, monkeypatch, 'bound', True)
    with api as (url, _):
        configure(e.ai, url); e.sendMessage('修范围并调唇色', 'auto'); wait_for(lambda: bool(jobs))
        if mutation == 'candidate': e._candidate = {**e._candidate, 'label': '已另改范围'}
        elif mutation == 'lock': e._locked.add('hsl_red_lightness'); e._sync_layer()
        elif mutation == 'source': e._sha = 'different source'
        elif mutation == 'target': e._selection_target_id = e._layers[0]['id']
        else: e._generation += 1
        before, draft = deepcopy(e._layers), deepcopy(e._candidate)
        mask_refinement.complete(e, neural_result(jobs[0], mask), jobs[0]['context']); finished(e)
        assert e._layers == before and e._candidate == draft and e._layer()['recipe']['hsl_red_lightness'] == 0
        assert e.conversation[-1]['state'] == 'failed'


def test_cancel_neural_stage_and_late_result_preserve_everything(canvas, monkeypatch):  # noqa: F811
    ui = canvas; e = ui.e
    mask, _, jobs, api = start_chat(ui, monkeypatch, 'bound', True)
    before, cursor = deepcopy(e._layers), e._cursor
    with api as (url, _):
        configure(e.ai, url); e.sendMessage('修范围和颜色', 'auto'); wait_for(lambda: bool(jobs))
        e.selection.cancelTask(); finished(e)
        mask_refinement.complete(e, neural_result(jobs[0], mask), jobs[0]['context'])
        assert e._layers == before and e._candidate == mask and e._cursor == cursor
        assert '取消' in e.conversation[-1]['text']


@pytest.mark.parametrize('case', ['stale', 'cancelled', 'failed'])
def test_worker_terminal_error_releases_ai_request_without_publishing(case):
    context = {'purpose': 'ai_mask_refinement', 'mask_token': 'token'}
    active = {'id': 7, 'op': 'segment', 'generation': 10, 'jobs': [], 'context': context, 'cancelled': case == 'cancelled'}
    message = {'id': 7, 'op': 'segment', 'generation': 9 if case == 'stale' else 10,
               'ok': case != 'failed', 'error': '模型失败', 'result': {'items': []}}
    mask, layers = typed_mask((300, 200)), [{'id': 'kept'}]
    messages = []
    owner = SimpleNamespace(_pixel_buffer=b'', _pixel_active=active, _generation=10, _closing=False,
        _pending_request={'mask_refinement': {'token': 'token'}}, _status='previous', _layers=layers,
        _candidate=mask, _warm_ready_sha='unprepared',
        _pixel_process=SimpleNamespace(readAllStandardOutput=lambda: (json.dumps(message)+'\n').encode()),
        _message=lambda *args, **kwargs: messages.append((args, kwargs)), _notify=lambda *args, **kwargs: None,
        changed=SimpleNamespace(emit=lambda: None), _pump_pixel=lambda: None)
    worker_bridge._pixel_read(owner)
    assert owner._pending_request is None and owner._pixel_active is None
    assert owner._layers == layers and owner._candidate == mask and messages[-1][1]['state'] == 'failed'


def test_cancel_cloud_detail_and_invalid_points_keep_original(canvas, monkeypatch):  # noqa: F811
    ui = canvas; e = ui.e
    mask, _, jobs, api = start_chat(ui, monkeypatch, 'new', False, delay=.15)
    before = deepcopy(e._layers)
    with api as (url, requests):
        configure(e.ai, url); e.sendMessage('修正已有范围', 'auto')
        wait_for(lambda: len(requests) == 2 and e.ai.busy)
        e.selection.cancelTask(); finished(e)
        assert not jobs and e._layers == before and e._candidate == mask
    # Rejected points are retried once, then the original mask is reviewed without guessing points.
    mask, _, jobs, api = start_chat(ui, monkeypatch, 'new', False, bad_points=True)
    with api as (url, requests):
        configure(e.ai, url); e.sendMessage('修正已有范围', 'auto'); finished(e)
        assert len(requests) == 4 and not jobs and e._layers == before and e._candidate == mask
        assert e.conversation[-1]['state'] == 'answered' and '原范围与参数保持' in e.conversation[-1]['text']


def test_first_cloud_reply_cannot_rebind_changed_draft(canvas, monkeypatch):  # noqa: F811
    ui = canvas; e = ui.e
    _, _, jobs, api = start_chat(ui, monkeypatch, 'new', False, delay=.15)
    before = deepcopy(e._layers)
    with api as (url, requests):
        configure(e.ai, url); e.sendMessage('修正当前误选范围', 'auto'); wait_for(lambda: len(requests) == 1)
        e._candidate = {**e._candidate, 'label': '另一个范围'}
        draft = deepcopy(e._candidate); finished(e)
        assert len(requests) == 1 and not jobs and e._layers == before and e._candidate == draft
        assert e.conversation[-1]['state'] == 'failed'


@pytest.mark.parametrize('case', ['empty', 'wrong-part', 'wrong-target'])
def test_invalid_local_result_never_publishes_planned_color(canvas, monkeypatch, case):  # noqa: F811
    ui = canvas; e = ui.e
    mask, _, jobs, api = start_chat(ui, monkeypatch, 'bound', True)
    before, cursor = deepcopy(e._layers), e._cursor
    with api as (url, _):
        configure(e.ai, url); e.sendMessage('修范围并调色', 'auto'); wait_for(lambda: bool(jobs))
        result = neural_result(jobs[0], mask)
        item = result['items'][0]
        if case == 'empty': item['mask']['bitmap'] = encode_bitmap(Image.new('L', (2400, 1600)), preserve_resolution=True)
        elif case == 'wrong-part': item['mask'].update(face_part='nose', semantic_target='face_skin')
        else: item['id'] = 'other target'
        mask_refinement.complete(e, result, jobs[0]['context']); finished(e)
        assert e._layers == before and e._candidate == mask and e._cursor == cursor
        assert e.conversation[-1]['state'] == 'failed'


def test_preparation_is_cancellable_and_late_crop_cannot_start_cloud(canvas, monkeypatch):  # noqa: F811
    ui = canvas; e = ui.e
    mask, _, jobs, api = start_chat(ui, monkeypatch, 'new', False)
    request = e._request; crops = []
    def hold(op, **data):
        if op == 'mask_refinement_crop': crops.append(data); return True
        return request(op, **data)
    monkeypatch.setattr(e, '_request', hold)
    before = deepcopy(e._layers)
    with api as (url, requests):
        configure(e.ai, url); e.sendMessage('修正误选范围', 'auto'); wait_for(lambda: bool(crops))
        assert e.aiMaskPreparing and e.selection.taskKind == 'ai' and e.selection.taskCancellable
        e.selection.cancelTask(); finished(e)
        mask_refinement.crop_ready(e, {}, crops[0]['context'], e._generation)
        assert len(requests) == 1 and not jobs and not e.aiMaskPreparing
        assert e._layers == before and e._candidate == mask
