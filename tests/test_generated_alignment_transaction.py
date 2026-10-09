"""Full QML + actual alignment worker; cloud/image generation are controlled."""
import base64
from copy import deepcopy
from io import BytesIO
import json
from pathlib import Path

import numpy as np
from PIL import Image
import pytest
from PySide6.QtCore import QPointF, Qt, QTimer
from PySide6.QtGui import QGuiApplication
from PySide6.QtTest import QTest

from iphoto.controllers import photo_strategy
from iphoto.document import raster_mask, render_layers, read_project
from iphoto.engine import Recipe
from iphoto.generation_scope import validate_scope_reference
from test_ai import configure, mock_api, wait_for
from test_editor import settled
from test_generated_alignment import reference, displaced
from test_import_export import ui as shared_ui

ui = shared_ui


def click(window, item):
    assert item.isVisible() and item.property('enabled')
    point = item.mapToScene(QPointF(item.width()/2, item.height()/2)).toPoint()
    QTest.mouseClick(window, Qt.LeftButton, Qt.NoModifier, point)


@pytest.mark.parametrize('case,size', [('accept',(1440,930)), ('accept',(1080,700)),
    ('reject',(1440,930)), ('cancel',(1080,700)), ('bad_reference',(1440,930)),
    ('bad_hash',(1440,930)), ('stale',(1440,930)), ('unrelated_pixels',(1440,930)),
    ('no_scope',(1080,700)), ('empty_scope',(1080,700)),
    ('wrong_size_scope',(1440,930)), ('scope_file_missing',(1440,930))])
def test_geometry_is_a_cancellable_bound_stage_before_review_and_commit(ui, monkeypatch, case, size):
    editor, window, find, warnings, tmp_path = ui
    window.resize(*size)
    window.requestActivate()
    QTest.qWait(100)
    source = reference()
    path = tmp_path/'texture.png'
    source.save(path)
    editor.openImage(str(path))
    wait_for(lambda: editor.hasImage and settled(editor))
    editor.drawDraft('ellipse', 'replace', [[.30,.28],[.68,.75]], .025)
    wait_for(lambda: settled(editor))
    original, candidate, cursor = deepcopy(editor._layers), deepcopy(editor._candidate), editor._cursor
    stages, alignments = [], []

    def answer(payload):
        context = json.loads(payload['messages'][1]['content'][0]['text'])
        assert editor._layers == original and editor._cursor == cursor
        if context['mode'] == 'auto':
            plan = {'action':'generate','scope':'current_selection','summary':'在范围内精修',
                'edit_prompt':'局部精修，保持其他内容位置','strategy':None,'recipe':Recipe().to_dict(),
                'regions':[],'layer_edits':[],'repairs':[],'group':None,'mask_refinement':None}
        else:
            assert context['mode'] == 'photo_review' and alignments
            plan = {'status':'reject' if case=='reject' else 'accept','summary':'检查实际合成像素','edits':[]}
        return {'choices':[{'finish_reason':'stop','message':{'content':json.dumps(plan)}}]}

    original_plan = editor.ai.plan
    def plan(*args):
        values = list(args)
        if values[5] == 'auto':
            values[6] = {**values[6],'image_edit_available':True}
        return original_plan(*values)
    monkeypatch.setattr(editor.ai,'plan',plan)

    def generate(url, prompt, size, token, generation, *, scope_url):
        validate_scope_reference(url, scope_url)
        assert url.startswith('data:image/png;base64,')
        with Image.open(BytesIO(base64.b64decode(url.split(',',1)[1]))) as image:
            image = image.convert('RGB')
        if case == 'unrelated_pixels':
            pixels = np.random.default_rng(99).integers(0,256,(image.height,image.width,3),dtype=np.uint8)
        else:
            pixels = np.array(image)
            y,x = np.ogrid[:image.height,:image.width]
            center = (x-image.width*.5)**2+(y-image.height*.5)**2 < 30**2
            pixels[center,0] = np.minimum(pixels[center,0].astype(int)+55,255)
        output = displaced(Image.fromarray(pixels)).resize(tuple(size),Image.Resampling.LANCZOS)
        QTimer.singleShot(0,lambda:editor._image_edit.completed.emit(output,token,generation))
        return True
    monkeypatch.setattr(editor._image_edit,'start',generate)
    original_ready = photo_strategy.generative_ready
    def ready(owner, result, context, generation):
        if case == 'no_scope':
            result = {key:value for key,value in result.items() if key != 'scope_path'}
        elif case == 'scope_file_missing':
            Path(result['scope_path']).unlink()
        elif case in ('empty_scope','wrong_size_scope'):
            with Image.open(result['scope_path']) as scope:
                size = scope.size
            if case == 'wrong_size_scope':
                size = (size[0]+1,size[1])
            Image.new('L', size, 0 if case=='empty_scope' else 255).save(result['scope_path'])
        return original_ready(owner,result,context,generation)
    monkeypatch.setattr(photo_strategy,'generative_ready',ready)
    original_aligned = photo_strategy.aligned
    def aligned(owner,result,context,generation):
        alignments.append(result['alignment'])
        return original_aligned(owner,result,context,generation)
    monkeypatch.setattr(photo_strategy,'aligned',aligned)
    original_request = editor._request
    def request(op,**kwargs):
        if op == 'generative_align':
            stages.append(op)
            if case == 'bad_reference': kwargs['reference_id'] += 1
            if case == 'bad_hash': kwargs['expected_sha256'] = '0'*64
            if case == 'cancel':
                def cancel():
                    assert find('aiRequestProgress').isVisible()
                    assert find('cancelAiRequest').isVisible()
                    click(window,find('cancelAiRequest'))
                QTimer.singleShot(0,cancel)
            if case == 'stale':
                QTimer.singleShot(0,lambda:setattr(editor,'_generation',editor._generation+1))
        return original_request(op,**kwargs)
    monkeypatch.setattr(editor,'_request',request)
    with mock_api(answer) as (url, requests):
        configure(editor.ai,url)
        QTest.qWait(100)
        if not find('descriptionInput').isVisible():
            click(window,find('chatToggleButton'))
            QTest.qWait(50)
        click(window,find('descriptionInput'))
        QGuiApplication.clipboard().setText('直接生成精修当前范围')
        QTest.keyClick(window,Qt.Key_V,Qt.ControlModifier)
        click(window,find('applyDescriptionButton'))
        wait_for(lambda:not editor.busy and settled(editor) and editor._pending_request is None,seconds=30)
        assert stages == ([] if case in ('no_scope','empty_scope','wrong_size_scope','scope_file_missing') else ['generative_align'])
        if case == 'accept':
            assert alignments[-1]['status'] == 'aligned'
            assert len(editor._layers)==len(original)+1 and editor._cursor==cursor+1
            assert editor._layers[-1]['mask']==candidate
            result = render_layers(source,editor._layers)
            alpha = np.asarray(raster_mask(candidate,source.size))
            assert np.array_equal(np.asarray(result)[alpha==0],np.asarray(source)[alpha==0])
            assert np.any(np.asarray(result)[alpha>0]!=np.asarray(source)[alpha>0])
            final = deepcopy(editor._layers)
            project = tmp_path/'aligned.iphoto'
            editor.saveProject(str(project));wait_for(lambda:not editor.savingProject)
            assert read_project(project)['layers']==final
            output = tmp_path/'aligned.png'
            editor.exportImage(str(output));wait_for(lambda:editor._export_request is None)
            with Image.open(output) as exported:
                assert exported.convert('RGB').tobytes()==result.tobytes()
            editor.undo();wait_for(lambda:settled(editor));assert editor._layers==original
            editor.redo();wait_for(lambda:settled(editor));assert editor._layers==final
        else:
            assert editor._layers==original and editor._cursor==cursor and editor._candidate==candidate
        assert len(requests)==(2 if case in ('accept','reject') else 1)
    assert not warnings
