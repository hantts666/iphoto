"""Original comparison must retain the real source texture and frame geometry."""
from copy import deepcopy
import io
import json
import subprocess
import sys

import numpy as np
from PIL import Image
import pytest
from PySide6.QtCore import QProcess, Qt, QUrl
from PySide6.QtGui import QImage
from PySide6.QtTest import QTest

from iphoto.document import new_layer, render_layers
from iphoto.engine import Recipe, Source, load_source
from iphoto.paths import ROOT
from test_ai import wait_for
from test_canvas_ui import canvas  # noqa: F401
from test_editor import settled
from test_detail_continuity_ui import detail_ui  # noqa: F401
from test_preview_continuity_ui import frames  # noqa: F401


@pytest.mark.parametrize('mode', ['RGB', 'RGBA'])
@pytest.mark.parametrize('with_mask', [False, True])
def test_real_worker_reuses_original_crop_and_keeps_current_pair_in_bounded_assets(tmp_path, mode, with_mask):
    source_path = tmp_path/'source.png'
    pixels = np.random.default_rng(83).integers(0, 256, (161, 247, 4 if mode == 'RGBA' else 3), dtype='uint8')
    Image.fromarray(pixels).save(source_path)
    source = load_source(source_path)
    box = (27, 19, 218, 147)
    requests = []
    for serial in range(1, 16):
        layer = new_layer('adjustment', True)
        layer['recipe'] = Recipe(exposure=serial/20).to_dict()
        requests.append({'id': serial, 'op': 'detail', 'generation': serial, 'box': box,
                         'source_path': str(source.path), 'source_sha': source.digest, 'layers': [layer],
                         **({'mask': layer['mask'], 'mask_view': 'overlay'} if with_mask else {})})
    directory = tmp_path/'tiles'
    process = subprocess.run([sys.executable, str(ROOT/'run.py'), '--detail-worker', str(directory)],
                             input=''.join(json.dumps(r)+'\n' for r in requests), text=True,
                             capture_output=True, encoding='utf8', timeout=30)
    assert process.returncode == 0, process.stderr
    replies = [json.loads(line) for line in process.stdout.splitlines()]
    assert len(replies) == 15 and all(r['ok'] for r in replies), process.stdout
    assert len({r['result']['original'] for r in replies}) == 1
    last = replies[-1]['result']
    with Image.open(last['original']) as original:
        assert original.mode == mode and original.size == (191, 128)
        assert original.tobytes() == source.image.crop(box).tobytes()
        assert original.info.get('icc_profile')
    with Image.open(last['path']) as edited:
        assert edited.tobytes() == render_layers(source.image, requests[-1]['layers']).crop(box).tobytes()
    if with_mask:
        assert Image.open(last['mask']).size == (191, 128)
    assert len(list(directory.glob('*.png'))) <= 8


def test_worker_source_identity_includes_digest_and_never_reuses_wrong_original(tmp_path, monkeypatch):
    from iphoto import detail_worker

    class Stream(io.StringIO):
        def reconfigure(self, **kwargs):
            pass

    path = tmp_path/'same-name.png'
    calls = []
    sources = [Source(path, Image.new('RGB', (16, 12), (30, 50, 70)), 'old', b''),
               Source(path, Image.new('RGB', (16, 12), (150, 170, 190)), 'new', b''),
               Source(path, Image.new('RGB', (16, 12), (150, 170, 190)), 'new', b'')]

    def load(_path):
        calls.append(_path)
        return sources[len(calls)-1]

    requests = [{'id': i+1, 'op': 'detail', 'generation': i, 'box': [2, 1, 14, 11],
                 'source_path': str(path), 'source_sha': digest, 'layers': [new_layer('whole', True)]}
                for i, digest in enumerate(['old', 'new', 'wrong'])]
    output = Stream()
    monkeypatch.setattr(detail_worker, 'load_source', load)
    monkeypatch.setattr(sys, 'argv', ['run.py', '--detail-worker', str(tmp_path/'tiles')])
    monkeypatch.setattr(sys, 'stdin', Stream(''.join(json.dumps(r)+'\n' for r in requests)))
    monkeypatch.setattr(sys, 'stdout', output)
    detail_worker.main()
    replies = [json.loads(line) for line in output.getvalue().splitlines()]
    assert len(calls) == 3 and [r['ok'] for r in replies] == [True, True, False]
    assert '源照片已变化' in replies[-1]['error']
    assert replies[0]['result']['original'] != replies[1]['result']['original']
    for reply, expected in zip(replies[:2], sources[:2]):
        with Image.open(reply['result']['original']) as original:
            assert original.tobytes() == expected.image.crop((2, 1, 14, 11)).tobytes()


def open_texture(ui, tmp_path):
    y, x = np.mgrid[:1600, :2400]
    texture = np.where((x+y) % 2, 255, 0).astype('uint8')
    path = tmp_path/'fine-texture.png'
    Image.fromarray(texture).convert('RGB').save(path)
    ui.e.openImage(str(path))
    wait_for(lambda: ui.e.imageName == path.name and settled(ui.e) and ui.w.property('previewReady'))
    ui.e.setParameter('exposure', -.8);ui.e.finishGesture()
    wait_for(lambda: settled(ui.e))
    ui.click('actualSizeButton')
    wait_for(lambda: ui.w.property('detailReady') and ui.find('canvasViewport').property('originalDetailReady'))
    return load_source(path)


def screen_texture(ui):
    center = ui.point('canvasSurface')
    patch = ui.w.grabWindow().copy(center.x()-40, center.y()-40, 80, 80).convertToFormat(QImage.Format_Grayscale8)
    return np.frombuffer(patch.bits(), dtype='uint8').reshape(patch.height(), patch.bytesPerLine())[:, :patch.width()].std()


def test_hold_and_split_compare_show_real_texture_without_requests_or_edits(canvas, tmp_path):  # noqa: F811
    ui = canvas
    source = open_texture(ui, tmp_path)
    original = deepcopy((ui.e._layers, ui.e._history, ui.e._cursor, ui.e._generation))
    serial = ui.e._detail_serial
    native = ui.find('originalDetailImage')
    with Image.open(QUrl(ui.e.detailOriginalUrl).toLocalFile()) as tile:
        assert tile.tobytes() == source.image.crop(ui.e._detail_box).tobytes()
    point = ui.point('holdOriginalButton')
    QTest.mousePress(ui.w, Qt.LeftButton, Qt.NoModifier, point);QTest.qWait(40)
    assert native.isVisible() and screen_texture(ui) > 100
    assert '原图细节已显示' in ui.find('detailStatusCaption').property('text')
    QTest.mouseRelease(ui.w, Qt.LeftButton, Qt.NoModifier, point);QTest.qWait(40)
    assert not native.isVisible() and ui.w.property('detailReady')
    ui.click('compareButton')
    assert native.isVisible() and ui.find('canvasViewport').property('originalDetailReady')
    assert ui.e._detail_serial == serial
    assert (ui.e._layers, ui.e._history, ui.e._cursor, ui.e._generation) == original


def test_original_survives_recipe_change_pan_and_worker_park(canvas, tmp_path):  # noqa: F811
    ui = canvas
    source = open_texture(ui, tmp_path)
    first = ui.e.detailOriginalUrl
    ui.e.setParameter('exposure', -.2);ui.e.finishGesture()
    assert ui.find('canvasViewport').property('originalDetailReady')
    wait_for(lambda: ui.w.property('detailReady') and settled(ui.e))
    assert ui.e.detailOriginalUrl == first
    ui.n.pan(-650, 0)
    wait_for(lambda: ui.w.property('detailReady') and ui.find('canvasViewport').property('originalDetailReady')
             and ui.e.detailOriginalUrl != first)
    with Image.open(QUrl(ui.e.detailOriginalUrl).toLocalFile()) as tile:
        assert tile.tobytes() == source.image.crop(ui.e._detail_box).tobytes()
    serial = ui.e._detail_serial
    ui.e._detail_idle_timer.setInterval(40);ui.e._detail_idle_timer.start()
    wait_for(lambda: ui.e._detail_process.state() == QProcess.NotRunning)
    point = ui.point('holdOriginalButton')
    QTest.mousePress(ui.w, Qt.LeftButton, Qt.NoModifier, point);QTest.qWait(40)
    assert ui.find('originalDetailImage').isVisible() and screen_texture(ui) > 100
    assert ui.e._detail_serial == serial
    QTest.mouseRelease(ui.w, Qt.LeftButton, Qt.NoModifier, point)
    ui.click('fitCanvasButton')
    wait_for(lambda: ui.e.detailOriginalUrl == '')
    assert not ui.find('canvasViewport').property('originalDetailReady')


def test_switch_photo_never_displays_previous_native_original(canvas, tmp_path):  # noqa: F811
    ui = canvas
    open_texture(ui, tmp_path)
    first = ui.e.detailOriginalUrl
    ui.click('compareButton')
    path = tmp_path/'other-photo.png'
    Image.new('RGB', (2400, 1600), (170, 60, 90)).save(path)
    ui.e.openImage(str(path))
    wait_for(lambda: ui.e.imageName == path.name and settled(ui.e) and ui.w.property('previewReady'))
    assert ui.e.detailOriginalUrl == '' and not ui.find('originalDetailImage').isVisible()
    ui.click('actualSizeButton')
    wait_for(lambda: ui.find('canvasViewport').property('originalDetailReady'))
    assert ui.e.detailOriginalUrl != first
    with Image.open(QUrl(ui.e.detailOriginalUrl).toLocalFile()) as tile:
        assert tile.getpixel((10, 10)) == (170, 60, 90)


def test_delayed_original_keeps_its_displayed_rectangle_and_reports_proxy_fallback(detail_ui, frames):  # noqa: F811
    view = detail_ui
    native = view.image('originalDetailImage')
    def shown_rect():
        value = native.property('displayedRect')
        return value.toVariant() if hasattr(value, 'toVariant') else list(value)
    wait_for(lambda: native.property('hasFrame'))
    old_rect = shown_rect()
    pending = frames((74, 123, 142))
    box = (256, 128, 2176, 1536)
    view.e._detail_box = box
    view.e._detail_original_url = pending['url']
    view.e.changed.emit()
    wait_for(lambda: pending['requested'].is_set())
    assert shown_rect() == old_rect
    point = view.point('holdOriginalButton')
    QTest.mousePress(view.w, Qt.LeftButton, Qt.NoModifier, point);QTest.qWait(30)
    assert native.isVisible() and '原图细节已显示' in view.caption()
    pending['release'].set()
    expected = [256/2400, 128/1600, 1920/2400, 1408/1600]
    wait_for(lambda: shown_rect() == expected)
    assert native.property('frameCurrent')
    view.e._detail_original_url = ''
    view.e.changed.emit();QTest.qWait(30)
    assert not native.isVisible() and '原图预览' in view.caption()
    assert '正在载入原像素对比细节' in view.caption()
    QTest.mouseRelease(view.w, Qt.LeftButton, Qt.NoModifier, point)
