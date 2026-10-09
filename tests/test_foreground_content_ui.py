"""Real worker, full QML and portable paired-content transactions."""
from copy import deepcopy
import hashlib
from pathlib import Path

import pytest
from PIL import Image
from PySide6.QtCore import QPointF, Qt, QUrl
from PySide6.QtTest import QTest

from iphoto.controllers import foreground_content, worker_bridge
from iphoto.document import read_project
from iphoto.foreground_content import read_foreground
from test_ai import wait_for
from test_editor import settled
from test_import_export import ui as shared_ui
from test_rgba_content import content

ui = shared_ui


def click(window, item):
    QTest.mouseClick(window, Qt.LeftButton, Qt.NoModifier,
                     item.mapToScene(QPointF(item.width()/2, item.height()/2)).toPoint())


def state(editor):
    return deepcopy((editor._layers, editor._cursor, editor._generation, editor._candidate))


@pytest.mark.parametrize('size', [(1440, 930), (1080, 700)])
def test_file_dialog_route_roundtrip_export_and_one_undo(ui, size):
    editor, window, find, warnings, folder = ui
    window.resize(*size)
    source = folder/'source.png'
    digest = hashlib.sha256(source.read_bytes()).hexdigest()
    pixels = content()
    path = folder/'透明发丝.png'
    pixels.save(path)
    before = state(editor)
    dialog = find('foregroundImportDialog')
    assert find('foregroundImportMenuAction').property('enabled')
    assert Path(dialog.property('currentFolder').toLocalFile()) == folder
    # Exercise the actual QML accepted route. Native OS picker interaction is
    # not exercised by the offscreen platform.
    dialog.setProperty('selectedFile', QUrl.fromLocalFile(str(path)))
    dialog.accepted.emit()
    wait_for(lambda: len(editor._layers) == len(before[0])+1 and settled(editor))
    assert editor._cursor == before[1]+1 and not editor.hasSelectionDraft
    assert editor._layer()['pixel_patch']['compositing'] == 'replace_rgba'
    assert editor._layer()['mask']['base'] == 'full'
    imported = state(editor)
    png, jpeg, project = folder/'paired.png', folder/'paired.jpg', folder/'paired.iphoto'
    editor.exportImage(str(png))
    wait_for(lambda: png.exists() and settled(editor))
    assert Image.open(png).tobytes() == pixels.tobytes()
    editor.exportImage(str(jpeg))
    wait_for(lambda: jpeg.exists() and settled(editor))
    expected = Image.alpha_composite(Image.new('RGBA', pixels.size, 'white'), pixels).convert('RGB')
    actual = Image.open(jpeg)
    for point in [(5, 5), (150, 80), (25, 100)]:
        assert max(abs(a-b) for a,b in zip(actual.getpixel(point), expected.getpixel(point))) < 12
    editor.saveProject(str(project))
    saved = read_project(project)
    assert saved['schema_version'] == '1.13' and saved['layers'] == imported[0]
    path.unlink()  # The project owns the content, not a path to a temporary PNG.
    editor.undo()
    wait_for(lambda: settled(editor))
    assert editor._layers == before[0] and editor._cursor == before[1]
    editor.redo()
    wait_for(lambda: settled(editor))
    assert editor._layers == imported[0]
    editor.openProject(str(project))
    wait_for(lambda: settled(editor) and editor._layers == imported[0])
    reopened = folder/'reopened.png'
    editor.exportImage(str(reopened))
    wait_for(lambda: reopened.exists() and settled(editor))
    assert Image.open(reopened).tobytes() == pixels.tobytes()
    assert hashlib.sha256(source.read_bytes()).hexdigest() == digest
    assert not warnings


@pytest.mark.parametrize('source_alpha', [255, 128])
def test_native_detail_does_not_composite_translucent_content_over_its_preview(ui, source_alpha):
    editor, window, find, warnings, folder = ui
    source = folder/'large.png'
    Image.new('RGBA', (2400, 1600), (230, 190, 150, source_alpha)).save(source)
    editor.openImage(str(source))
    wait_for(lambda: settled(editor) and window.property('previewReady'))
    rgba = Image.new('RGBA', (1200, 800), (180, 30, 20, 128))
    rgba.putpixel((0, 0), (0, 0, 0, 0))
    path = folder/'half-alpha.png'
    rgba.save(path)
    assert editor.importForeground(str(path))
    wait_for(lambda: settled(editor) and len(editor._layers) == 2)
    click(window, find('actualSizeButton'))
    wait_for(lambda: window.property('detailReady'), seconds=25)
    surface = find('canvasSurface')
    point = surface.mapToScene(QPointF(surface.width()/2, surface.height()/2)).toPoint()
    origin = surface.mapToScene(QPointF(0, 0))
    x, y = point.x()-origin.x(), point.y()-origin.y()
    checker = '#4a4d52' if (int(x)//12+int(y)//12)%2 else '#383c41'
    expected = Image.alpha_composite(Image.new('RGBA', (1, 1), checker),
                                    Image.new('RGBA', (1, 1), (180, 30, 20, 128))).getpixel((0, 0))[:3]
    actual = window.grabWindow().pixelColor(point).getRgb()[:3]
    assert max(abs(a-b) for a,b in zip(actual, expected)) <= 2, (actual, expected)
    hold = find('holdOriginalButton')
    hold_point = hold.mapToScene(QPointF(hold.width()/2, hold.height()/2)).toPoint()
    QTest.mousePress(window, Qt.LeftButton, Qt.NoModifier, hold_point)
    QTest.qWait(50)
    original = Image.alpha_composite(Image.new('RGBA', (1, 1), checker),
                                    Image.new('RGBA', (1, 1), (230, 190, 150, source_alpha))).getpixel((0, 0))[:3]
    actual = window.grabWindow().pixelColor(point).getRgb()[:3]
    assert max(abs(a-b) for a,b in zip(actual, original)) <= 2, (actual, original)
    QTest.mouseRelease(window, Qt.LeftButton, Qt.NoModifier, hold_point)
    for mode in ('white', 'black'):
        editor.selection.setMaskView(mode)
        wait_for(lambda: settled(editor) and window.property('maskDetailReady'), seconds=20)
        expected = Image.alpha_composite(Image.new('RGBA', (1, 1), mode),
                                        Image.new('RGBA', (1, 1), (180, 30, 20, 128))).getpixel((0, 0))[:3]
        actual = window.grabWindow().pixelColor(point).getRgb()[:3]
        assert max(abs(a-b) for a,b in zip(actual, expected)) <= 2, (actual, expected)
    assert not warnings


def test_queued_import_has_visible_cancellable_progress_and_retry(ui, monkeypatch):
    editor, window, find, warnings, folder = ui
    path = folder/'content.png'
    content().save(path)
    before = state(editor)
    original = editor._pump
    monkeypatch.setattr(editor, '_pump', lambda: None)
    assert editor.importForeground(str(path))
    editor.changed.emit()
    QTest.qWait(50)
    assert editor.busy and editor.selection.taskKind == 'content'
    assert find('aiRequestProgress').isVisible()
    assert find('cancelAiRequest').property('text') == '取消导入'
    click(window, find('cancelAiRequest'))
    assert not editor.busy and state(editor) == before
    monkeypatch.setattr(editor, '_pump', original)
    assert editor.importForeground(str(path))
    wait_for(lambda: settled(editor) and len(editor._layers) == 2)
    assert editor._cursor == before[1]+1 and not warnings


def test_active_import_cancel_ignores_real_worker_reply(ui, monkeypatch):
    editor, window, find, warnings, folder = ui
    path = folder/'content.png'
    content().save(path)
    before = state(editor)
    original = worker_bridge._read
    # Hold delivery, not computation: the actual worker still reads the PNG.
    monkeypatch.setattr(worker_bridge, '_read', lambda _: None)
    assert editor.importForeground(str(path))
    assert editor._active['op'] == 'foreground_import'
    QTest.qWait(80)
    click(window, find('cancelAiRequest'))
    assert editor._active['cancelled'] and not editor.busy
    monkeypatch.setattr(worker_bridge, '_read', original)
    editor._read()
    wait_for(lambda: settled(editor))
    assert state(editor) == before and '取消导入' in editor.status and not warnings


@pytest.mark.parametrize('change', ['generation', 'signature', 'source'])
def test_late_result_cannot_apply_to_changed_document(ui, change):
    editor, window, find, warnings, folder = ui
    path = folder/'content.png'
    content().save(path)
    context = {'binding':editor._document_signature(), 'source_sha256':editor._sha,
               'canvas_size':[300, 200]}
    generation = editor._generation
    if change == 'generation': editor._generation += 1
    elif change == 'signature': editor._layers[0]['visible'] = False
    else: context['source_sha256'] = '0'*64
    before = state(editor)
    foreground_content.ready(editor, read_foreground(path, (300, 200)), context, generation)
    assert state(editor) == before and '过期' in editor.status
    assert not warnings


def test_invalid_file_and_live_selection_leave_document_unchanged(ui):
    editor, window, find, warnings, folder = ui
    path = folder/'opaque.png'
    Image.new('RGBA', (300, 200), (20, 30, 40, 255)).save(path)
    before = state(editor)
    assert editor.importForeground(str(path))
    wait_for(lambda: settled(editor))
    assert state(editor) == before and '不透明' in editor.status
    editor.drawDraft('rect', 'replace', [[.1, .1], [.8, .8]], .02)
    wait_for(lambda: settled(editor) and editor.hasSelectionDraft)
    before = state(editor)
    assert not editor.importForeground(str(path))
    assert not find('foregroundImportMenuAction').property('enabled')
    assert state(editor) == before and not warnings
