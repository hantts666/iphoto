"""Explicit generated-content scope, real worker transactions and alpha evidence."""
import base64
from copy import deepcopy
from io import BytesIO
import json
from types import SimpleNamespace

from PIL import Image, ImageDraw
import pytest
from PySide6.QtCore import QPointF, Qt, QTimer
from PySide6.QtTest import QTest

from iphoto.ai_protocol import parse_auto
from iphoto.ai_settings import AISettings
from iphoto.document import read_project
from iphoto.engine import Recipe
from iphoto.generated_foreground import white_guide, white_foreground, extract, review_images, FOREGROUND_PROMPT
from iphoto.generation_scope import SCOPE_PROMPT, validate_scope_reference
from iphoto.image_edit import ImageEditController
from test_ai import mock_api, wait_for
from test_editor import settled
from test_generation_scope import url
from test_import_export import ui as shared_ui

ui = shared_ui


def plan():
    return {'action': 'foreground', 'scope': 'whole_image', 'summary': 'AI重建人物，可能改变细节',
            'edit_prompt': '只保留主要人物及随身衣物，移除背景，保持位置与身份',
            'inspection_box': [200, 300, 800, 700],
            'strategy': None, 'recipe': Recipe().to_dict(), 'regions': [], 'layer_edits': [],
            'repairs': [], 'group': None, 'mask_refinement': None}


def response(value):
    return {'choices': [{'finish_reason': 'stop', 'message': {'content': json.dumps(value, ensure_ascii=False)}}]}


def paired(size=(300, 200)):
    rgba = Image.new('RGBA', size, (32, 55, 72, 0))
    d = ImageDraw.Draw(rgba)
    d.rectangle((50, 20, 240, 190), fill=(96, 58, 34, 255))
    d.rectangle((40, 20, 49, 190), fill=(124, 75, 42, 96))
    d.ellipse((130, 70, 150, 90), fill=(20, 40, 50, 0))
    return rgba


def test_observable_white_and_semantics_both_required_for_background():
    rgb = Image.new('RGB', (128, 128), 'white')
    coarse = Image.new('L', rgb.size)
    d = ImageDraw.Draw(coarse)
    d.rectangle((25, 15, 100, 120), fill=255)  # opaque white garment
    d.rectangle((20, 15, 24, 120), fill=120)   # uncertain white edge
    rgb.putpixel((5, 60), (180, 190, 180))    # coarse misses a coloured wisp
    before = rgb.tobytes(), coarse.tobytes()
    guide = white_guide(rgb, coarse)
    assert guide[60, 60] == 255 and guide[60, 21] == 128
    assert guide[60, 5] == 128 and guide[2, 2] == 0
    assert (rgb.tobytes(), coarse.tobytes()) == before


@pytest.mark.parametrize('case', ['no_background', 'no_person', 'wrong_mode', 'wrong_size'])
def test_invalid_white_input_does_not_make_an_alpha(case):
    rgb = Image.new('RGB', (128,128), 'white' if case != 'no_background' else '#557755')
    coarse = Image.new('L', rgb.size)
    ImageDraw.Draw(coarse).rectangle((30,20,100,120), fill=255 if case != 'no_person' else 0)
    if case == 'wrong_mode': rgb = rgb.convert('RGBA')
    if case == 'wrong_size': coarse = coarse.resize((64,64))
    with pytest.raises(ValueError): white_guide(rgb, coarse)


def test_real_rgba_keeps_own_colours_and_coverage_without_rematting():
    original = paired()
    rgba, quality = extract(original)
    assert rgba is not original and rgba.tobytes() == original.tobytes()
    assert quality['method'] == 'generated_rgba' and not quality['geometry_verified']
    with pytest.raises(ValueError, match='确定背景'):
        extract(Image.new('RGBA', (128,128), (80,30,20,100)))


def test_white_inverse_reproduces_known_foreground_and_preserves_hard_support():
    # Choose byte-exact known-white composites, with an independently specified
    # expected foreground rather than checking the implementation's formula.
    rgb = Image.new('RGB', (5, 1))
    rgb.putdata([(211, 221, 231), (231, 241, 251), (199, 207, 241), (255,255,255), (12,45,88)])
    alpha = Image.new('L', rgb.size)
    alpha.putdata([51, 51, 0, 255, 255])
    before = rgb.tobytes(), alpha.tobytes()
    rgba, count = white_foreground(rgb, alpha)
    assert [rgba.getpixel((x,0)) for x in range(5)] == [(35,85,135,51), (135,185,235,51), (199,207,241,0),
                                    (255,255,255,255), (12,45,88,255)]
    assert count == 0 and (rgb.tobytes(), alpha.tobytes()) == before


def test_white_inverse_constrains_partial_coverage_without_growing_background():
    rgb = Image.new('RGB', (4, 1))
    rgb.putdata([(15,135,255), (252,253,254), (0,50,80), (250,251,252)])
    alpha = Image.new('L', rgb.size)
    alpha.putdata([40, 0, 1, 1])
    rgba, count = white_foreground(rgb, alpha)
    # The first pixel requires at least 240/255 coverage; clipping F at alpha40
    # would lose the observed dark strand. A near-white zero stays zero.
    assert rgba.getpixel((0,0)) == (0,128,255,240)
    assert rgba.getpixel((1,0)) == (252,253,254,0)
    assert rgba.getpixel((2,0)) == (0,50,80,255)
    assert rgba.getpixel((3,0)) == (0,51,102,5)
    assert count == 3
    white = Image.alpha_composite(Image.new('RGBA', rgba.size, 'white'), rgba).convert('RGB')
    assert all(max(abs(a-b) for a,b in zip(white.getpixel((x,0)),rgb.getpixel((x,0)))) <= 1
               for x in (0,2,3))


@pytest.mark.parametrize('coverage', [0,255])
def test_white_inverse_without_partial_pixels_is_exact(coverage):
    rgb = Image.new('RGB', (80,80), '#aa7643')
    rgba, count = white_foreground(rgb, Image.new('L', rgb.size, coverage))
    assert rgba.convert('RGB').tobytes() == rgb.tobytes() and rgba.getchannel('A').getextrema() == (coverage,coverage)
    assert count == 0


@pytest.mark.parametrize('case', ['rgb_mode','alpha_mode','size','budget'])
def test_white_inverse_declines_invalid_inputs(case, monkeypatch):
    from iphoto import generated_foreground
    rgb, alpha = Image.new('RGB',(80,80)), Image.new('L',(80,80))
    if case == 'rgb_mode': rgb = rgb.convert('RGBA')
    elif case == 'alpha_mode': alpha = alpha.convert('RGB')
    elif case == 'size': alpha = alpha.resize((81,80))
    else: monkeypatch.setattr(generated_foreground, 'MAX_PIXELS', 6399)
    with pytest.raises(ValueError): white_foreground(rgb, alpha)


def test_rgb_extraction_uses_white_constraints_on_its_actual_model_output(monkeypatch):
    import numpy as np
    from iphoto.matting import portrait, neural
    rgb, coarse = Image.new('RGB',(128,128),'white'), Image.new('L',(128,128))
    ImageDraw.Draw(coarse).rectangle((25,15,100,120),fill=255)
    rgb.putpixel((5,60),(15,135,255))
    monkeypatch.setattr(portrait,'PortraitMatte',lambda:SimpleNamespace(provider='fixture',predict_image=lambda _:coarse))
    monkeypatch.setattr(neural,'NativeMatte',lambda:SimpleNamespace(provider='fixture'))
    def solve(image,guide,*,engine):
        assert image.tobytes() == rgb.tobytes() and guide[60,5] == 128
        pixels = np.where(guide==255,255,0).astype(np.uint8)
        pixels[60,5] = 40
        return pixels, 1
    monkeypatch.setattr(neural,'solve',solve)
    rgba, quality = extract(rgb)
    assert rgba.getpixel((5,60)) == (0,128,255,240)
    assert rgba.getpixel((60,60)) == (255,255,255,255) and rgba.getpixel((2,2))[3] == 0
    assert quality['colour_recovery'] == 'known_white' and quality['coverage_adjusted_pixels'] == 1
    assert not quality['geometry_verified']


def test_review_evidence_has_actual_white_black_and_native_coverage():
    rgba = paired((900,600))
    baseline = Image.new('RGB', (1800,1200), '#537c45')
    before = rgba.tobytes(), baseline.tobytes()
    pictures = review_images(baseline, rgba, [200, 300, 800, 700])
    assert 6 <= len(pictures) <= 8
    assert all(picture.mode == 'RGB' for picture, _ in pictures)
    assert pictures[1][0].getpixel((0,0)) == (255,255,255)
    assert pictures[2][0].getpixel((0,0)) == (0,0,0)
    expected = Image.alpha_composite(Image.new('RGBA', rgba.size, 'black'), rgba).convert('RGB')
    assert pictures[2][0].tobytes() == expected.tobytes()
    assert pictures[5][0].tobytes() == expected.crop((0,44,512,556)).tobytes()
    assert all(max(picture.size) <= 512 for picture, _ in pictures[3:])
    assert (rgba.tobytes(), baseline.tobytes()) == before


@pytest.mark.parametrize('case', ['available','unavailable','selection','scope','budget','regions','recipe','no_focus','invalid_focus','wrong_units'])
def test_generated_foreground_is_not_a_scope_escape(case):
    value = plan()
    workspace = {'image_edit_available': True, 'foreground_edit_available': True, 'max_new_layers': 1}
    scope = 'whole_image'
    if case == 'unavailable': workspace['foreground_edit_available'] = False
    elif case == 'selection': scope = 'selection'
    elif case == 'scope': value['scope'] = 'current_layer'
    elif case == 'budget': workspace['max_new_layers'] = 0
    elif case == 'regions': value['regions'] = [{}]
    elif case == 'recipe': value['recipe']['exposure'] = .4
    elif case == 'no_focus': value.pop('inspection_box')
    elif case == 'invalid_focus': value['inspection_box'] = [.4, .6, .4, float('nan')]
    elif case == 'wrong_units': value['inspection_box'] = [.2, .3, .8, .7]
    if case == 'available':
        assert parse_auto(response(value), Recipe().to_dict(), [], workspace=workspace)['action'] == 'foreground'
    else:
        with pytest.raises(ValueError):
            parse_auto(response(value), Recipe().to_dict(), [], current_scope=scope, workspace=workspace)


def test_foreground_transport_uses_white_contract_and_declines_local_scope_before_key(qt_app):
    photo = url(Image.new('RGB', (640,512), '#778855'))
    scope = url(Image.new('L', (640,512), 255))
    sent, key_reads = [], []
    ai = SimpleNamespace(settings=AISettings.validated('qwen', 'https://dashscope.aliyuncs.com/compatible-mode/v1', 'qwen3.8-max'),
        store=SimpleNamespace(resolve_key=lambda *_: key_reads.append(1) or 'fixture'), changed=SimpleNamespace(emit=lambda:None))
    controller = ImageEditController(ai)
    controller._send = lambda request, body, stage: sent.append(json.loads(body))
    try:
        assert controller.start(photo, '只保留人物', [640,512], 'token', 7, scope_url=scope, intent='foreground')
        content = sent[0]['input']['messages'][0]['content']
        assert len(content) == 2 and content[0] == {'image': photo}
        assert FOREGROUND_PROMPT in content[1]['text'] and SCOPE_PROMPT not in content[1]['text']
        local = Image.new('L',(640,512),255); local.putpixel((0,0),0)
        assert not controller.start(photo, '抠图', [640,512], 'token', 7, scope_url=url(local), intent='foreground')
        assert len(key_reads) == 1 and len(sent) == 1
    finally:
        controller.timer.stop()


def click(window, item):
    assert item.isVisible() and item.property('enabled')
    QTest.mouseClick(window, Qt.LeftButton, Qt.NoModifier,
        item.mapToScene(QPointF(item.width()/2, item.height()/2)).toPoint())


@pytest.mark.parametrize('case,size', [('accept',(1440,930)),('accept',(1080,700)),
    ('chat',(1080,700)),
    ('reject',(1440,930)),('cancel',(1080,700)),('alpha_cancel',(1080,700)),
    ('failure',(1440,930)),('retry',(1440,930)),('frame_mismatch',(1080,700))])
def test_explicit_menu_real_worker_review_export_and_atomic_undo(ui, monkeypatch, case, size):
    from iphoto import generated_foreground
    from iphoto.controllers import worker_bridge, photo_strategy
    editor, window, find, warnings, folder = ui
    window.resize(*size)
    monkeypatch.setattr(generated_foreground, 'available', lambda: True)
    original, cursor = deepcopy(editor._layers), editor._cursor
    rgba = paired()
    callbacks, stages = [], []
    def answer(payload):
        context = json.loads(payload['messages'][1]['content'][0]['text'])
        assert editor._layers == original and editor._cursor == cursor and editor.busy
        stages.append(context['mode'])
        if context['mode'] == 'auto':
            assert context['foreground_edit_available'] is True
            if case == 'chat':
                assert '去掉其他人物' in editor._pending_request['text']
            return response(plan())
        assert context['mode'] == 'photo_review' and context['foreground'] is True
        assert context['frame_facts'] == {'original_size':[300,200], 'generated_size':[300,200],
                                          'geometry_verified':False}
        images = [part['image_url']['url'] for part in payload['messages'][1]['content'] if part['type'] == 'image_url']
        assert len(images) == 6
        decoded = [Image.open(BytesIO(base64.b64decode(value.partition(',')[2]))) for value in images]
        assert decoded[1].getpixel((0,0)) == (255,255,255) and decoded[2].getpixel((0,0)) == (0,0,0)
        return response({'status':'reject' if case == 'reject' else 'accept', 'summary':'检查了实际黑白底与边缘', 'edits':[]})
    def generate(photo, prompt, size, token, generation, *, scope_url, intent):
        validate_scope_reference(photo, scope_url, whole_frame=True)
        assert intent == 'foreground'
        callback = lambda: editor._image_edit.completed.emit(rgba, token, generation)
        callbacks.append(callback)
        if case == 'failure' or case == 'retry' and len(callbacks) == 1:
            QTimer.singleShot(0, lambda: editor._image_edit.failure.emit('测试生成失败，照片未改变'))
        elif case != 'cancel':
            QTimer.singleShot(0, callback)
        return True
    monkeypatch.setattr(editor._image_edit, 'start', generate)
    original_read = worker_bridge._read
    if case == 'frame_mismatch':
        original_ready = photo_strategy.ready
        def wrong_frame(owner,result,context,generation):
            result['frame_facts']['generated_size'][0] += 1
            return original_ready(owner,result,context,generation)
        monkeypatch.setattr(photo_strategy,'ready',wrong_frame)
    if case == 'alpha_cancel':
        def held_read(owner):
            if owner._active and owner._active['op'] == 'foreground_alpha':
                return
            return original_read(owner)
        monkeypatch.setattr(worker_bridge, '_read', held_read)
    with mock_api(answer) as (api, requests):
        assert editor.ai.save('qwen', api, 'test-vision', 'fixture', False, True)
        QTest.qWait(100)
        if case == 'chat':
            window.setProperty('chatOpen', True)
            find('descriptionInput').setProperty('text', '去掉其他人物，保留帽子和随身包')
            QTest.qWait(100)
            button = find('chatGeneratedForegroundButton')
            point = button.mapToScene(QPointF(button.width()/2,button.height()/2))
            assert 0 <= point.y() <= window.height()
            click(window, button)
            assert find('descriptionInput').property('text') == ''
        else:
            click(window, find('cutoutToolsButton'))
            wait_for(lambda: find('generatedForegroundAction').isVisible())
            click(window, find('generatedForegroundAction'))
        if case == 'cancel':
            wait_for(lambda: bool(callbacks))
            click(window, find('cancelAiRequest'))
            callbacks[0]()
        if case == 'alpha_cancel':
            wait_for(lambda: editor._active and editor._active['op'] == 'foreground_alpha')
            assert editor.busy
            click(window, find('cancelAiRequest'))
            monkeypatch.setattr(worker_bridge, '_read', original_read)
            editor._read()
        if case == 'retry':
            wait_for(lambda: not editor.busy and editor._pending_request is None)
            error = editor._conversation[-1]
            assert error['role'] == 'error' and editor.failedPromptInfo(error['id'])['foreground_requested']
            assert editor.retryFailedPrompt(error['id'])
        wait_for(lambda: not editor.busy and settled(editor) and editor._pending_request is None, seconds=30)
        if case in ('accept', 'retry', 'chat'):
            final = deepcopy(editor._layers)
            assert len(final) == 2 and editor._cursor == cursor+1
            assert final[-1]['pixel_patch']['compositing'] == 'replace_rgba'
            assert final[-1]['mask']['base'] == 'full'
            exported = folder/'foreground.png'
            editor.exportImage(str(exported)); wait_for(lambda: exported.exists() and settled(editor))
            assert Image.open(exported).tobytes() == rgba.tobytes()
            project = folder/'generated.iphoto'
            editor.saveProject(str(project)); wait_for(lambda: not editor.savingProject)
            saved = read_project(project)
            assert saved['layers'] == final and saved['conversation'][0]['foreground_requested'] is True
            editor.undo(); wait_for(lambda: settled(editor))
            assert editor._layers == original and editor._cursor == cursor
            editor.redo(); wait_for(lambda: settled(editor))
            assert editor._layers == final
            assert '生成分辨率300×200' in editor._conversation[-1]['text']
        else:
            assert editor._layers == original and editor._cursor == cursor
        expected = (['auto','auto','photo_review'] if case == 'retry' else
                    ['auto','photo_review'] if case in ('accept','reject','chat') else ['auto'])
        assert stages == expected
        assert len(requests) == len(stages) and not warnings


def test_ordinary_chat_does_not_enable_whole_frame_repainting(ui, monkeypatch):
    from iphoto import generated_foreground
    editor, _, _, warnings, _ = ui
    monkeypatch.setattr(generated_foreground, 'available', lambda: True)
    original, cursor = deepcopy(editor._layers), editor._cursor
    def answer(payload):
        context = json.loads(payload['messages'][1]['content'][0]['text'])
        assert context['foreground_edit_available'] is False
        return response(plan())  # Deliberately disobey the advertised capability.
    monkeypatch.setattr(editor._image_edit, 'start', lambda *_args, **_kwargs: pytest.fail('Unrequested repaint'))
    with mock_api(answer) as (api, requests):
        assert editor.ai.save('qwen', api, 'test-vision', 'fixture', False, True)
        assert editor.sendMessage('用通道抠出人物，保留原片细节', 'auto')
        wait_for(lambda: not editor.busy and editor._pending_request is None and settled(editor))
        # The existing protocol repair makes one bounded retry. Neither reply
        # can enable a capability the user did not select.
        assert editor._layers == original and editor._cursor == cursor and len(requests) == 2 and not warnings


@pytest.mark.parametrize('change', ['generation', 'binding', 'canvas'])
def test_foreground_result_is_bound_before_render_or_commit(ui, change):
    from iphoto.controllers.photo_strategy import foreground_ready
    from iphoto.pixel_patch import encode_patch
    editor, _, _, warnings, _ = ui
    generation = editor._generation
    editor._pending_request = {'binding': editor._document_signature(), 'layer_snapshot': deepcopy(editor._layers),
        'photo_strategy': {'token':'test', 'generation':generation, 'foreground':True, 'reference_id':7,
                           'canvas_size':[300,200]}}
    result = {'patch':encode_patch(paired(), [300,200], [0,0,300,200], preserve_alpha=True),
              'reference_id':7, 'quality':{}}
    if change == 'generation': editor._generation += 1
    elif change == 'binding': editor._layers[0]['visible'] = False
    else: result['patch']['canvas_size'] = [300,201]
    original, cursor = deepcopy(editor._layers), editor._cursor
    foreground_ready(editor, result, {'token':'test'}, generation)
    assert editor._pending_request is None and editor._layers == original and editor._cursor == cursor
    assert not warnings


def test_foreground_review_cannot_replace_alpha_check_with_recipe_edits():
    from iphoto.photo_strategy import parse_review
    value = {'status':'revise', 'summary':'不能用曝光解决缺失发丝',
             'edits':[{'layer_id':'test', 'recipe':Recipe(exposure=.3).to_dict()}]}
    with pytest.raises(ValueError, match='透明度'):
        parse_review(response(value), {'foreground':True, 'candidates':[]})


@pytest.mark.parametrize('intent', ['retouch', 'foreground'])
def test_download_keeps_actual_rgba_only_for_foreground(qt_app, intent):
    from PySide6.QtNetwork import QNetworkReply, QNetworkRequest
    rgba = paired()
    data = BytesIO(); rgba.save(data, format='PNG')
    controller = ImageEditController(SimpleNamespace())
    controller.stage, controller.body = 'download', bytearray()
    controller.cancel_reason, controller.intent = '', intent
    controller.output_size, controller.token, controller.generation = rgba.size, 'token', 7
    class Reply:
        def readAll(self):
            content = data.getvalue(); data.truncate(0); data.seek(0)
            return content
        def attribute(self, key):
            assert key == QNetworkRequest.HttpStatusCodeAttribute
            return 200
        def error(self): return QNetworkReply.NoError
        def deleteLater(self): pass
    reply = Reply(); controller.reply = reply
    results, failures = [], []
    controller.completed.connect(lambda pixels, token, generation: results.append(pixels))
    controller.failure.connect(failures.append)
    controller._finish(reply)
    assert not failures and len(results) == 1
    expected = rgba if intent == 'foreground' else rgba.convert('RGB')
    assert results[0].mode == expected.mode and results[0].tobytes() == expected.tobytes()
