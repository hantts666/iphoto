"""Real staged matting, native inspection and cutout export share the same pixels."""
from copy import deepcopy
import hashlib
from pathlib import Path

from PIL import Image
import pytest
from PySide6.QtCore import QUrl

from iphoto.document import raster_mask
from iphoto.matting.channel_view import native_previews
from iphoto.workspace import Editor
from test_ai import wait_for
from test_canvas_ui import canvas  # noqa: F401
from test_channel_matting import scene
from test_editor import settled


def prepared(editor, tmp_path):
    image, _, mask, options = scene()
    path = tmp_path/'staged-cloth.png'; image.save(path)
    editor.openImage(str(path)); wait_for(lambda: editor.hasImage and settled(editor))
    editor._layer()['mask'] = deepcopy(mask)
    editor._layer()['recipe']['exposure'] = .35
    editor._load_layer(); editor._commit()
    editor.beginSelection('current'); wait_for(lambda: settled(editor))
    editor.channelMask.open(); wait_for(lambda: not editor.channelMask.loading and settled(editor))
    for key, value in options.items():
        editor.channelMask.setOption(key, value)
    wait_for(lambda: not editor.channelMask.loading and settled(editor))
    return image, path


def unchanged(editor):
    return deepcopy((editor._candidate, editor._layers, editor._draft_history, editor._cursor, editor._generation))


def test_real_ai_preview_native_views_export_and_cached_apply_are_atomic(canvas, tmp_path, monkeypatch):  # noqa: F811
    ui = canvas; e = ui.e; image, path = prepared(e, tmp_path); channel = e.channelMask
    before = unchanged(e); digest = hashlib.sha256(path.read_bytes()).hexdigest()
    requests = []; request = e._request
    def counted(op, **fields):
        requests.append(op)
        return request(op, **fields)
    monkeypatch.setattr(e, '_request', counted)
    wait_for(lambda: ui.find('channelResultPreviewButton').property('enabled'))
    ui.click('channelResultPreviewButton')
    wait_for(lambda: channel.hasResult and not channel.loading and settled(e), seconds=45)
    wait_for(lambda: ui.find('channelMaskDialog').property('previewReady'))
    assert channel.showingResult and channel.nativeView and unchanged(e) == before
    assert channel._result['quality']['tiles'] > 0 and channel._result['mask']['color_recovery']
    result = deepcopy(channel._result); result_views = deepcopy(channel._result_views)
    paths = {name: Path(QUrl(url).toLocalFile()) for name, url in result_views.items()}
    assert all(p.is_file() for p in paths.values())
    ui.click('channelNativeZoomButton')
    wait_for(lambda: ui.find('channelMaskDialog').property('nativeZoom'))
    native_box = (31,6,209,154)
    assert ui.find('channelAlphaPreview').width() == native_box[2]-native_box[0]
    assert ui.find('channelAlphaPreview').height() == native_box[3]-native_box[1]
    serial = e._serial
    ui.click('channelCompareButton'); wait_for(lambda: not channel.showingResult)
    assert '草图' in ui.find('channelPreviewStage').property('text')
    assert all(Path(QUrl(url).toLocalFile()).is_file() for url in channel._draft_views.values())
    ui.click('channelCompareButton'); wait_for(lambda: channel.showingResult)
    assert e._serial == serial and unchanged(e) == before
    ui.click('channelApplyButton')
    wait_for(lambda: not channel.opened and settled(e))
    assert requests.count('matte') == 1
    assert e._candidate == result['mask'] and e._layers == before[1]
    final = deepcopy(e._candidate); e.undo(); assert e._candidate == before[0]
    e.redo(); assert e._candidate == final
    wait_for(lambda: settled(e))
    target = tmp_path/'actual-cutout.png'
    assert e.exportRange(str(target), 'cutout')
    wait_for(lambda: e._export_request is None and target.is_file(), seconds=40)
    with Image.open(target) as output:
        output.load()
        assert output.getchannel('A').tobytes() == raster_mask(final, image.size).tobytes()
        for name in ('white', 'black'):
            expected = Image.alpha_composite(Image.new('RGBA', output.size, name), output).convert('RGB')
            with Image.open(paths[name]) as actual:
                assert actual.size == (178,148) and actual.tobytes() == expected.crop(native_box).tobytes()
    assert hashlib.sha256(path.read_bytes()).hexdigest() == digest


@pytest.mark.parametrize('ending', ['cancel', 'close', 'crash', 'missing-source'])
def test_preview_failure_or_cancel_never_publishes_and_releases_loading(qt_app, ai_store, tmp_path, ending):
    e = Editor(ai_store=ai_store)
    try:
        _, path = prepared(e, tmp_path); channel = e.channelMask; before = unchanged(e)
        if ending == 'missing-source': path.unlink()
        channel.previewResult(); wait_for(lambda: e.matteBusy)
        if ending == 'close': channel.close()
        elif ending == 'cancel': e.cancelMatte()
        elif ending == 'crash':
            wait_for(lambda: e._matte_active is not None)
            e._matte_process.kill()
        wait_for(lambda: not e.matteBusy and not channel.loading, seconds=30)
        assert unchanged(e) == before and not channel.hasResult
        assert channel.opened == (ending != 'close')
    finally: e.close()


def test_parameter_change_discards_cached_result_and_ignores_old_reply(qt_app, ai_store, tmp_path):
    e = Editor(ai_store=ai_store)
    try:
        prepared(e, tmp_path); channel = e.channelMask; before = unchanged(e)
        channel.previewResult(); wait_for(lambda: channel.hasResult and settled(e), seconds=45)
        result = deepcopy(channel._result)
        active = {'channel_token':channel.state['token'], 'channel_revision':channel.state['revision'],
                  'channel_options':deepcopy(channel.options)}
        channel.setOption('gamma', 1.4)
        assert not channel.hasResult and not channel.previewUrl
        channel.result_ready(result, active)
        assert channel._result is None and unchanged(e) == before
        wait_for(lambda: not channel.loading and settled(e))
        assert not channel.hasResult and channel.options['gamma'] == 1.4
    finally: e.close()


def test_invalid_or_unchanged_option_preserves_real_cached_result_without_recalculating(qt_app, ai_store, tmp_path, monkeypatch):
    e = Editor(ai_store=ai_store)
    try:
        prepared(e, tmp_path); channel = e.channelMask; before = unchanged(e)
        channel.previewResult(); wait_for(lambda: channel.hasResult and not channel.loading and settled(e), seconds=45)
        original = deepcopy((channel.options, channel.state, channel._result, channel._views, channel._result_views))
        preview = channel.previewUrl
        requests = []; request = e._request
        def counted(op, **fields):
            requests.append(op)
            return request(op, **fields)
        monkeypatch.setattr(e, '_request', counted)
        for name, value in [('black', channel.options['white']), ('white', channel.options['black']),
                            ('gamma', float('nan')), ('gamma', True), ('gamma', channel.options['gamma'])]:
            channel.setOption(name, value)
            assert channel.hasResult and not channel.loading and channel.previewUrl == preview
            assert (channel.options, channel.state, channel._result, channel._views, channel._result_views) == original
            assert not channel.timer.isActive() and unchanged(e) == before
        channel.apply(); wait_for(lambda: not channel.opened and settled(e))
        assert 'matte' not in requests and e._candidate == original[2]['mask']
        assert e._layers == before[1]
    finally: e.close()


def test_large_views_do_not_claim_native_resolution(monkeypatch):
    image, _, mask, _ = scene(); alpha = raster_mask(mask, image.size)
    monkeypatch.setattr('iphoto.matting.channel_view.MAX_NATIVE_VIEW', 1)
    views, info = native_previews(image.resize((2400,1600)), alpha.resize((2400,1600)), mask, 8, whole=True)
    assert not info['native']
    assert all(picture.size == (1600,1067) for _, picture in views)


@pytest.mark.parametrize('alpha', [Image.new('RGB', (240,160)), Image.new('L', (120,160))])
def test_native_preview_rejects_mismatched_alpha(alpha):
    with pytest.raises(ValueError, match='尺寸不一致'):
        native_previews(scene()[0], alpha, scene()[2], 8)
