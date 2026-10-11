"""Raw channel inspection is independent from alpha and never publishes edits."""
from copy import deepcopy
from pathlib import Path

import numpy as np
from PIL import Image
import pytest
from PySide6.QtCore import QUrl

from iphoto.document import empty_mask
from iphoto.matting.channel_view import channel_choices, native_previews
from iphoto.matting.channels import CHANNELS, channel_plane
from test_ai import wait_for
from test_canvas_ui import canvas  # noqa: F401
from test_channel_matting import scene
from test_editor import settled


@pytest.mark.parametrize('channel', CHANNELS)
def test_raw_native_view_preserves_source_plane_and_is_independent_of_alpha(channel):
    y, x = np.mgrid[:128, :192]
    pixels = np.stack((x, y, (x*37+y*97) % 256), axis=-1).astype('uint8')
    image = Image.fromarray(pixels)
    before = image.tobytes()
    for value in (0, 128, 255):
        alpha = Image.new('L', image.size, value)
        pictures, info = native_previews(image, alpha, empty_mask(True), 32, whole=True, channel=channel)
        views = dict(pictures)
        assert info['native'] and views['channel'].mode == 'L'
        assert views['channel'].tobytes() == channel_plane(image, channel).tobytes()
        assert views['alpha'].tobytes() == alpha.tobytes()
        assert image.tobytes() == before


def test_small_channel_overviews_use_one_frame_and_never_modify_source():
    image, _, _, _ = scene()
    before = image.tobytes()
    box = (30, 20, 190, 140)
    choices = list(channel_choices(image, box))
    assert [item[0] for item in choices] == ['', *CHANNELS]
    assert len({item[2].size for item in choices}) == 1
    assert choices[0][2].tobytes() == image.crop(box).convert('RGB').tobytes()
    for name, _, picture in choices[1:]:
        assert picture.tobytes() == channel_plane(image.crop(box), name).tobytes()
        assert max(picture.size) <= 192
    assert image.tobytes() == before


def test_display_resize_happens_after_native_channel_subtraction(monkeypatch):
    from iphoto.engine import preview
    pixels = np.full((128, 1920, 3), 20, 'uint8')
    pixels[:, ::4, 0] = 250
    pixels[:, ::4, 1] = 0
    image = Image.fromarray(pixels)
    monkeypatch.setattr('iphoto.matting.channel_view.MAX_NATIVE_VIEW', 1)
    views, info = native_previews(image, Image.new('L', image.size, 128), empty_mask(True),
                                  32, whole=True, channel='red_green')
    raw = dict(views)['channel']
    expected = preview(Image.fromarray(channel_plane(image, 'red_green')), 1600)
    assert not info['native'] and raw.tobytes() == expected.tobytes()
    wrong = channel_plane(preview(image, 1600), 'red_green')
    assert np.abs(np.asarray(raw).astype(int)-wrong.astype(int)).max() > 10


def test_actual_auto_result_keeps_raw_channel_and_cached_comparison(qt_app, ai_store, tmp_path):
    from iphoto.workspace import Editor
    editor = Editor(ai_store=ai_store)
    try:
        image, _, mask, _ = scene()
        path = tmp_path / 'auto-channel.png'
        image.save(path)
        editor.openImage(str(path))
        wait_for(lambda: editor.hasImage and settled(editor))
        editor._set_candidate(mask)
        wait_for(lambda: settled(editor))
        channel = editor.channelMask
        channel.open()
        wait_for(lambda: not channel.loading and settled(editor))
        channel.setOption('channel', 'auto')
        wait_for(lambda: not channel.loading and settled(editor))
        channel.setView('channel')
        before = deepcopy((editor._candidate, editor._layers, editor._cursor, editor._generation))
        channel.previewResult()
        wait_for(lambda: channel.hasResult and not channel.loading and settled(editor), seconds=45)
        assert channel.options['channel'] == 'auto'
        assert channel._result['quality']['channel'] in CHANNELS
        raw = Image.open(QUrl(channel.previewUrl).toLocalFile())
        # This independent achromatic scene has the same native byte in R/G/B.
        # The default 32px radius includes the entire 240x160 test photograph.
        assert raw.size == image.size and raw.tobytes() == image.getchannel('R').tobytes()
        assert len(channel.channelChoices) == 8
        serial = editor._serial
        channel.compare()
        assert not channel.showingResult
        channel.compare()
        assert channel.showingResult and editor._serial == serial
        assert (editor._candidate, editor._layers, editor._cursor, editor._generation) == before
    finally:
        editor.close()


def test_clicking_channel_overviews_and_native_inspection_never_publishes(canvas, tmp_path):  # noqa: F811
    ui = canvas
    image, _, mask, _ = scene()
    path = tmp_path / 'channel-choices.png'
    image.save(path)
    ui.e.openImage(str(path))
    wait_for(lambda: ui.e.hasImage and settled(ui.e))
    ui.e._set_candidate(mask)
    wait_for(lambda: settled(ui.e))
    def snapshot():
        return deepcopy((ui.e._candidate, ui.e._layers, ui.e._cursor, ui.e._generation,
                         ui.e._draft_history, ui.e._edit_revision, ui.e.dirty))
    before = snapshot()
    ui.e.channelMask.open()
    wait_for(lambda: not ui.e.channelMask.loading and ui.e.channelMask.previewUrl and settled(ui.e))
    wait_for(lambda: ui.find('channelMaskDialog').property('previewReady'))
    assert len(ui.e.channelMask.channelChoices) == 8
    ui.click('channelChoicesButton')
    wait_for(lambda: ui.find('channelChoicesGrid').isVisible())
    ui.click('channelChoice_red_green')
    wait_for(lambda: not ui.e.channelMask.loading and settled(ui.e)
             and ui.find('channelMaskDialog').property('previewReady'))
    assert ui.e.channelMask.view == 'alpha'
    assert ui.e.channelMask.options['channel'] == 'red_green'
    assert not ui.find('channelChoicesGrid').isVisible()
    assert '通道草图' in ui.find('channelPreviewStage').property('text')
    raw = Path(QUrl(ui.e.channelMask._views['channel']).toLocalFile()).read_bytes()
    ui.e.channelMask.setOption('gamma', 1.7)
    wait_for(lambda: not ui.e.channelMask.loading and settled(ui.e))
    assert ui.e.channelMask.view == 'alpha'
    assert Path(QUrl(ui.e.channelMask._views['channel']).toLocalFile()).read_bytes() == raw
    wait_for(lambda:ui.find('channelMaskDialog').property('previewReady'))
    ui.click('channelNativeZoomButton')
    assert ui.find('channelMaskDialog').property('nativeZoom')
    assert snapshot() == before
    ui.click('channelChoicesButton')
    ui.click('channelChoice_source')
    assert ui.e.channelMask.view == 'source' and snapshot() == before
    ui.click('channelCloseButton')
    wait_for(lambda: not ui.e.channelMask.opened and settled(ui.e))
    assert snapshot() == before
