"""Visual review choices and exact alpha transactions, not accuracy fixtures."""
from copy import deepcopy
import json

import numpy as np
from PIL import Image, ImageDraw
import pytest

from iphoto.ai_mask_review import groups, parse_review, prepare_review, remove_regions
from iphoto.ai_protocol import build_payload
from iphoto.ai_settings import AISettings
from iphoto.controllers import mask_refinement
from iphoto.document import raster_mask
from iphoto.engine import Recipe
from iphoto.masks import encode_bitmap
import test_ai_mask_refinement as helper
from test_ai import configure, wait_for
from test_canvas_ui import canvas  # noqa: F401
from test_editor import settled


def scene():
    image = Image.new('RGB', (240, 180), (90, 130, 165))
    alpha = Image.new('L', image.size); draw = ImageDraw.Draw(alpha)
    draw.rectangle((20, 45, 95, 130), fill=255)
    draw.rectangle((19, 44, 96, 131), outline=45)
    draw.rectangle((155, 55, 166, 70), fill=255)
    draw.point((169, 62), fill=80)
    mask = helper.typed_mask(image.size)
    mask.update(bitmap=encode_bitmap(alpha, preserve_resolution=True), feather=.002)
    return image, mask


def plan(status='remove', ids=None):
    return {'status': status, 'summary': '复查后的明确区域判断', 'exclude_regions': [2] if ids is None else ids}


def test_geometry_only_offers_candidates_and_removal_preserves_all_other_alpha():
    image, mask = scene(); image.info['exif'] = b'private source'
    original, alpha, overlay, box, regions = prepare_review(image, mask)
    assert [r['can_remove'] for r in regions] == [False, True]
    assert not original.info and not alpha.info and not overlay.info
    assert original.tobytes() == image.crop(box).tobytes()
    assert overlay.tobytes() != original.tobytes()
    assert all(set(r) == {'id', 'box', 'can_remove'} for r in regions)
    before = np.asarray(raster_mask(mask, image.size)).copy()
    result = remove_regions(image, mask, [2], regions)
    after = np.asarray(raster_mask(result, image.size))
    labels, local_regions = groups(alpha)
    chosen = next(r['_label'] for r in local_regions if r['id'] == 2)
    expected = before.copy(); local = expected[box[1]:box[3], box[0]:box[2]]
    local[labels == chosen] = 0
    assert np.array_equal(after, expected) and np.count_nonzero(after) > 0
    assert not np.any((after > 0) & (before == 0))
    assert result['face_part'] == 'lips' and result['semantic_target'] == 'face' and result['label'] == mask['label']
    assert result['ops'] == [] and result['feather'] == 0
    # Geometry alone cannot delete even the smallest group.
    with pytest.raises(ValueError): remove_regions(image, mask, [], regions)
    with pytest.raises(ValueError): remove_regions(image, mask, [1], regions)
    changed = deepcopy(regions); changed[1]['box'][0] += 1
    with pytest.raises(ValueError, match='已变化'): remove_regions(image, mask, [2], changed)


@pytest.mark.parametrize('body', [plan(ids=[]), plan(ids=[1]), plan(ids=[3]), plan(ids=[True]),
    plan(ids=[2, 2]), plan(ids=[0]), plan(ids=[17]), plan(ids=[2.0]), plan('keep', [2]),
    plan('uncertain', [2]), plan('unknown', []), {**plan(), 'points': []}, {**plan(), 'summary': ''}])
def test_bad_review_cannot_delete_protected_or_unoffered_coverage(body):
    regions = [{'id': 1, 'can_remove': False}, {'id': 2, 'can_remove': True}]
    with pytest.raises(ValueError): parse_review(helper.response(body), {'regions': regions})


def test_keep_uncertain_empty_and_budget_rules():
    for status in ['keep', 'uncertain']:
        assert parse_review(helper.response(plan(status, [])), {'regions': []})['exclude_regions'] == []
    with pytest.raises(ValueError): groups(Image.new('L', (40, 40), 10))
    with pytest.raises(ValueError): groups(Image.new('L', (2500, 1800), 255))


def test_review_payload_order_and_private_data():
    regions = [{'id': 1, 'box': [100, 100, 800, 800], 'can_remove': False}]
    payload = build_payload(AISettings(provider='openai'), '检查嘴唇', Recipe().to_dict(), [], 'original',
        'mask_review', {'face_part': 'lips', 'crop_size': [220, 190], 'selection_image': 'gray',
        'selection_overlay': 'numbered', 'regions': regions, '_mask_path': 'private', 'existing_layers': ['unrelated']})
    contents = payload['messages'][1]['content']
    assert [i['image_url']['url'] for i in contents if i['type'] == 'image_url'] == ['original', 'gray', 'numbered']
    context = json.loads(contents[0]['text']); assert context['regions'] == regions
    assert '_mask_path' not in context and 'existing_layers' not in context and 'current_recipe' not in context
    assert payload['response_format']['json_schema']['schema']['properties']['exclude_regions']['maxItems'] == 8


def fragmented_setup(ui, monkeypatch, mode, color, **kwargs):
    # Keep the helper's base function to avoid recursive monkeypatch calls.
    base = helper.typed_mask
    def parts(size=(2400, 1600)):
        mask = base(size); pixels = raster_mask(mask, size)
        ImageDraw.Draw(pixels).rectangle((size[0]//2+100, size[1]//3, size[0]//2+125, size[1]//3+25), fill=220)
        mask['bitmap'] = encode_bitmap(pixels, preserve_resolution=True)
        return mask
    monkeypatch.setattr(helper, 'typed_mask', parts)
    return helper.start_chat(ui, monkeypatch, mode, color, **kwargs)


def choose_error(context):
    return {**plan(ids=[region['id'] for region in context['regions'] if region['can_remove']]),
            'summary': '区域2是误选；区域1 can_remove=false，_label是内部编号'}


@pytest.mark.parametrize('mode,color', [('new', False), ('bound', True), ('existing', True)])
def test_review_applies_only_ai_selected_regions_with_one_transaction(canvas, monkeypatch, mode, color):  # noqa: F811
    ui = canvas; e = ui.e
    mask, lid, jobs, api = fragmented_setup(ui, monkeypatch, mode, color, review=choose_error)
    before, cursor = deepcopy(e._layers), e._cursor
    with api as (url, requests):
        configure(e.ai, url); e.sendMessage('修正误选并保留原调色', 'auto'); wait_for(lambda: bool(jobs))
        neural = helper.neural_result(jobs[0], mask); staged = neural['items'][0]['mask']
        mask_refinement.complete(e, neural, jobs[0]['context'])
        # The first model result is staged, never prematurely published.
        assert e._layers == before and e._candidate == (mask if mode != 'existing' else None)
        helper.finished(e)
        assert len(requests) == 3
        actual = e._candidate if mode == 'new' else e._layer()['mask']
        image = Image.new('RGB', (2400, 1600)); _, _, _, _, regions = prepare_review(image, staged)
        expected = remove_regions(image, staged, [r['id'] for r in regions if r['can_remove']], regions)
        assert raster_mask(actual, image.size).tobytes() == raster_mask(expected, image.size).tobytes()
        assert 'AI视觉复查' in e.selectionQuality and 'AI复查' in e.conversation[-1]['text']
        assert 'can_remove' not in e.conversation[-1]['text'] and '_label' not in e.conversation[-1]['text']
        assert '区域2' not in e.conversation[-1]['text']
        if mode == 'new':
            assert e._layers == before and e._cursor == cursor
            e.undo(); wait_for(lambda: settled(e)); assert e._candidate == mask
            e.redo(); wait_for(lambda: settled(e)); assert e._candidate == actual
        else:
            assert e.activeLayerId == lid and e.parameters['hsl_red_lightness'] == 6
            assert e.parameters['exposure'] == .12 and e.parameters['warmth'] == 4
            changed = deepcopy(e._layers); assert e._cursor == cursor+1
            e.undo(); wait_for(lambda: settled(e)); assert e._layers == before
            e.redo(); wait_for(lambda: settled(e)); assert e._layers == changed


@pytest.mark.parametrize('case', ['cancel', 'locked', 'invalid', 'uncertain'])
def test_review_wait_or_bad_reply_never_commits_half_changes(canvas, monkeypatch, case):  # noqa: F811
    ui = canvas; e = ui.e
    review = plan(ids=[1]) if case == 'invalid' else plan('uncertain', []) if case == 'uncertain' else choose_error
    mask, _, jobs, api = fragmented_setup(ui, monkeypatch, 'bound', True, review=review, delay=.2)
    before, cursor = deepcopy(e._layers), e._cursor
    with api as (url, requests):
        configure(e.ai, url); e.sendMessage('修范围和颜色', 'auto'); wait_for(lambda: bool(jobs))
        result = helper.neural_result(jobs[0], mask)
        mask_refinement.complete(e, result, jobs[0]['context'])
        wait_for(lambda: len(requests) == 3 and e.ai.busy)
        assert '复查修正范围' in e.ai.requestProgress
        assert ui.find('aiRequestProgress').isVisible() and ui.find('cancelAiRequest').isVisible()
        assert '复查修正范围' in ui.find('aiRequestProgressText').property('text')
        assert e._layers == before and e._candidate == mask and e._cursor == cursor
        if case == 'cancel': e.selection.cancelTask()
        elif case == 'locked': e._locked.add('hsl_red_lightness'); e._sync_layer(); before = deepcopy(e._layers)
        helper.finished(e)
        if case == 'uncertain':
            assert e._layer()['mask'] == result['items'][0]['mask'] and e.parameters['hsl_red_lightness'] == 6
            assert '无法可靠判明' in e.conversation[-1]['text']
        else:
            assert e._layers == before and e._candidate == mask and e._cursor == cursor
            if case == 'invalid': assert len(requests) == 4 and e.conversation[-1]['state'] == 'failed'


def test_region_apply_cancellation_and_late_previous_stage_cannot_publish(canvas, monkeypatch):  # noqa: F811
    ui = canvas; e = ui.e
    mask, _, jobs, api = fragmented_setup(ui, monkeypatch, 'bound', True, review=choose_error)
    calls = []; request = e._request
    def hold(op, **data):
        if op == 'mask_refinement_apply': calls.append(data); return True
        return request(op, **data)
    monkeypatch.setattr(e, '_request', hold)
    before = deepcopy(e._layers)
    with api as (url, _):
        configure(e.ai, url); e.sendMessage('修正范围', 'auto'); wait_for(lambda: bool(jobs))
        result = helper.neural_result(jobs[0], mask)
        mask_refinement.complete(e, result, jobs[0]['context']); wait_for(lambda: bool(calls))
        assert e.aiMaskPreparing and e.selection.taskCancellable and e._candidate == mask
        state = e._pending_request['mask_refinement']
        # A late neural result/error cannot overwrite or cancel the apply stage.
        mask_refinement.complete(e, result, jobs[0]['context'])
        mask_refinement.failed(e, 'old stage error', jobs[0]['context'])
        mask_refinement.points_unavailable(e, e._generation)
        assert e._pending_request['mask_refinement'] is state
        e.selection.cancelTask(); helper.finished(e)
        mask_refinement.applied_regions(e, {'mask': result['items'][0]['mask']}, calls[0]['context'], e._generation)
        assert e._layers == before and e._candidate == mask


@pytest.mark.parametrize('mode,status', [('new', 'keep'), ('bound', 'uncertain'), ('existing', 'keep')])
def test_rejected_points_review_original_without_publishing_color_or_history(canvas, monkeypatch, mode, status):  # noqa: F811
    ui = canvas; e = ui.e
    mask, _, jobs, api = helper.start_chat(ui, monkeypatch, mode, True, bad_points=True, review=plan(status, []))
    before, candidate, target, cursor, generation = deepcopy(e._layers), deepcopy(e._candidate), e._selection_target_id, e._cursor, e._generation
    with api as (url, requests):
        configure(e.ai, url); e.sendMessage('修正范围并调色', 'auto'); helper.finished(e)
        assert len(requests) == 4 and not jobs
        assert e._layers == before and e._candidate == candidate and e._selection_target_id == target
        assert e._cursor == cursor and e._generation == generation
        assert e.conversation[-1]['state'] == 'answered' and '原范围与参数保持' in e.conversation[-1]['text']
        assert '一步撤销' not in e.conversation[-1]['text'] and not e.ai.isError
        if status == 'uncertain': assert '保留原范围' in e.conversation[-1]['text'] and '当前修正' not in e.conversation[-1]['text']
        if mode != 'existing': assert e._candidate == mask


@pytest.mark.parametrize('mode,color', [('new', False), ('existing', True)])
def test_rejected_points_can_remove_ai_named_original_region_atomically(canvas, monkeypatch, mode, color):  # noqa: F811
    ui = canvas; e = ui.e
    mask, _, jobs, api = fragmented_setup(ui, monkeypatch, mode, color, bad_points=True, review=choose_error)
    before, cursor = deepcopy(e._layers), e._cursor
    with api as (url, requests):
        configure(e.ai, url); e.sendMessage('修正范围并保留已有参数', 'auto'); helper.finished(e)
        assert len(requests) == 4 and not jobs
        image = Image.new('RGB', (2400, 1600)); _, _, _, _, regions = prepare_review(image, mask)
        expected = remove_regions(image, mask, [r['id'] for r in regions if r['can_remove']], regions)
        actual = e._candidate if mode == 'new' else e._layer()['mask']
        assert raster_mask(actual, image.size).tobytes() == raster_mask(expected, image.size).tobytes()
        assert '已有五官分区' in e.selectionQuality and 'SAM2' not in e.selectionQuality
        assert 'can_remove' not in e.conversation[-1]['text'] and '_label' not in e.conversation[-1]['text']
        if mode == 'new': assert e._layers == before and e._cursor == cursor
        else: assert e._cursor == cursor+1 and e.parameters['hsl_red_lightness'] == 6 and e.parameters['warmth'] == 4
        e.undo(); wait_for(lambda: settled(e))
        assert e._layers == before and (e._candidate == mask if mode == 'new' else e._candidate is None)


def test_unsupported_point_plan_reviews_original_once(canvas, monkeypatch):  # noqa: F811
    ui = canvas; e = ui.e
    mask, _, jobs, api = helper.start_chat(ui, monkeypatch, 'new', False,
        points_reply={'status': 'unsupported', 'summary': '无法可靠给点', 'exclusions': []})
    before = deepcopy(e._layers)
    with api as (url, requests):
        configure(e.ai, url); e.sendMessage('检查当前范围', 'auto'); helper.finished(e)
        assert len(requests) == 3 and not jobs and e._layers == before and e._candidate == mask
        assert e.conversation[-1]['state'] == 'answered'


@pytest.mark.parametrize('case', ['cancel', 'locked', 'invalid'])
def test_original_review_cancel_or_failure_preserves_complete_document(canvas, monkeypatch, case):  # noqa: F811
    ui = canvas; e = ui.e
    mask, _, jobs, api = fragmented_setup(ui, monkeypatch, 'bound', True, bad_points=True,
        review=plan(ids=[1]) if case == 'invalid' else choose_error, delay=.15)
    before, cursor = deepcopy(e._layers), e._cursor
    with api as (url, requests):
        configure(e.ai, url); e.sendMessage('修正范围和参数', 'auto')
        wait_for(lambda: len(requests) == 4 and e.ai.busy)
        assert not jobs and e._pending_request['mask_refinement']['points_fallback']
        assert e._layers == before and e._candidate == mask
        if case == 'cancel': e.selection.cancelTask()
        elif case == 'locked': e._locked.add('hsl_red_lightness'); e._sync_layer(); before = deepcopy(e._layers)
        helper.finished(e)
        assert e._layers == before and e._candidate == mask and e._cursor == cursor
        if case == 'invalid': assert len(requests) == 5 and e.conversation[-1]['state'] == 'failed'


def test_malformed_points_do_not_trigger_semantic_fallback(canvas, monkeypatch):  # noqa: F811
    ui = canvas; e = ui.e
    mask, _, jobs, api = helper.start_chat(ui, monkeypatch, 'new', True,
        points_reply={'status': 'planned', 'summary': '坐标不合法', 'exclusions': [[False, 400]]})
    before = deepcopy(e._layers)
    with api as (url, requests):
        configure(e.ai, url); e.sendMessage('修正范围和参数', 'auto'); helper.finished(e)
        assert len(requests) == 3 and not jobs and e._layers == before and e._candidate == mask
        assert e.conversation[-1]['state'] == 'failed'


@pytest.mark.parametrize('status', [200, 400, 401])
def test_only_valid_point_location_failure_emits_original_review_signal(qt_app, ai_store, tmp_path, status):
    from iphoto.ai import AIController
    from test_ai import mock_api

    controller = AIController(store=ai_store); failures, unavailable = [], []
    controller.failure.connect(failures.append); controller.maskPointsUnavailable.connect(unavailable.append)
    photo, mask = tmp_path/'photo.png', tmp_path/'mask.png'
    Image.new('RGB', (40, 30)).save(photo); Image.new('L', (40, 30)).save(mask)
    with mock_api(helper.point_plan([[0, 0]]), status=status) as (url, requests):
        configure(controller, url)
        assert controller.plan('修正误选范围', Recipe().to_dict(), [], str(photo), 9, 'mask_points',
                               {'crop_size': [40, 30], '_mask_path': str(mask)})
        wait_for(lambda: not controller.busy and bool(failures or unavailable))
        if status == 200:
            assert unavailable == [9] and failures == [] and len(requests) == 2 and not controller.isError
        else:
            assert unavailable == [] and len(failures) == 1 and str(status) in failures[0] and len(requests) == 1
    controller.close()
