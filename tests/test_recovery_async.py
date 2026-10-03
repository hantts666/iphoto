"""Large recovery writes keep the editor responsive and preserve the newest edit."""

from pathlib import Path
from threading import Event, Timer
from time import perf_counter, sleep

from PIL import Image
from PySide6.QtCore import QTimer

from iphoto.controllers import session
from iphoto.document import read_project
from iphoto.workspace import Editor
from test_ai import wait_for
from test_editor import settled


def _edited_editor(tmp_path, ai_store):
    source = tmp_path / "photo.png"
    Image.new("RGB", (300, 200), "blue").save(source)
    editor = Editor(ai_store=ai_store)
    editor.openImage(str(source))
    wait_for(lambda: editor.hasImage and settled(editor))
    editor.setParameter("exposure", .2)
    editor.finishGesture()
    editor._autosave.stop()
    return editor


def test_autosave_does_not_block_ui_timer(tmp_path, qt_app, ai_store, monkeypatch):
    editor = _edited_editor(tmp_path, ai_store)
    original = session.write_project

    def slow_write(path, payload, overwrite=False):
        if Path(path) == editor._recovery_path:
            sleep(.25)
        return original(path, payload, overwrite=overwrite)

    monkeypatch.setattr(session, "write_project", slow_write)
    ticks = []
    try:
        started = perf_counter()
        QTimer.singleShot(50, lambda: ticks.append(perf_counter() - started))
        editor._autosave.start(0)
        # A visible destination is not a writer-completion signal. On Windows
        # publication can still hold its rename handle when the path appears.
        wait_for(lambda: editor._recovery_future is None and editor._recovery_path.exists())
        assert ticks and ticks[0] < .15
        assert read_project(editor._recovery_path)["layers"][0]["recipe"]["exposure"] == .2
    finally:
        editor.close()


def test_manual_save_waits_for_running_recovery_and_cleans_it(tmp_path, qt_app, ai_store, monkeypatch):
    editor = _edited_editor(tmp_path, ai_store)
    started = Event()
    release = Event()
    original = session.write_project

    def delayed_write(path, payload, overwrite=False):
        if Path(path) == editor._recovery_path:
            started.set()
            assert release.wait(3)
        return original(path, payload, overwrite=overwrite)

    monkeypatch.setattr(session, "write_project", delayed_write)
    try:
        editor._autosave.start(0)
        wait_for(started.is_set)
        editor.setParameter("exposure", .8)
        editor.finishGesture()
        project = tmp_path / "saved.iphoto"
        Timer(.15, release.set).start()
        editor.saveProject(str(project))
        assert project.exists() and not editor.dirty
        assert read_project(project)["layers"][0]["recipe"]["exposure"] == .8
        assert not editor._recovery_path.exists()
    finally:
        release.set()
        editor.close()
