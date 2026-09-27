"""The UI saves projects without freezing editing or losing later changes."""

from pathlib import Path
from threading import Event
from time import perf_counter, sleep

from PIL import Image
from PySide6.QtCore import QPointF, QObject, Qt, QTimer
from PySide6.QtTest import QTest

from iphoto.controllers import session
from iphoto.document import read_project
from iphoto.workspace import Editor
from test_ai import wait_for
from test_editor import settled
from test_import_export import ui


def test_real_save_button_keeps_ui_timer_running(ui, monkeypatch):
    editor, window, find, warnings, tmp_path = ui
    project = tmp_path / "edit.iphoto"
    editor.saveProject(str(project))
    editor.setParameter("exposure", .4)
    editor.finishGesture()
    original = session.write_project

    def slow_write(path, payload, overwrite=False):
        if Path(path) == project:
            sleep(.25)
        return original(path, payload, overwrite=overwrite)

    monkeypatch.setattr(session, "write_project", slow_write)
    completed, ticks = [], []
    editor.projectSaveCompleted.connect(lambda path, error: completed.append((path, error)))
    button = window.findChild(QObject, "saveProjectButton")
    started = perf_counter()
    QTimer.singleShot(50, lambda: ticks.append(perf_counter() - started))
    point = button.mapToScene(QPointF(button.width() / 2, button.height() / 2)).toPoint()
    QTest.mouseClick(window, Qt.LeftButton, Qt.NoModifier, point)
    assert editor.savingProject and not button.property("enabled")
    wait_for(lambda: bool(completed))
    assert completed[-1] == (str(project), "")
    assert ticks and ticks[0] < .15
    assert read_project(project)["layers"][0]["recipe"]["exposure"] == .4
    assert not editor.dirty and not warnings


def test_edit_during_save_remains_dirty_until_saved_again(tmp_path, qt_app, ai_store, monkeypatch):
    source = tmp_path / "photo.png"
    Image.new("RGB", (300, 200), "blue").save(source)
    editor = Editor(ai_store=ai_store)
    gate = Event()
    started = Event()
    try:
        editor.openImage(str(source))
        wait_for(lambda: editor.hasImage and settled(editor))
        project = tmp_path / "edit.iphoto"
        editor.saveProject(str(project))
        editor.setParameter("exposure", .2)
        editor.finishGesture()
        original = session.write_project

        def delayed_write(path, payload, overwrite=False):
            if Path(path) == project and not gate.is_set():
                started.set()
                assert gate.wait(3)
            return original(path, payload, overwrite=overwrite)

        monkeypatch.setattr(session, "write_project", delayed_write)
        completed = []
        editor.projectSaveCompleted.connect(lambda path, error: completed.append((path, error)))
        assert editor.saveProjectAsync(str(project))
        wait_for(started.is_set)
        editor.setParameter("exposure", .8)
        editor.finishGesture()
        gate.set()
        wait_for(lambda: len(completed) == 1)
        assert completed[-1] == (str(project), "")
        assert read_project(project)["layers"][0]["recipe"]["exposure"] == .2
        assert editor.dirty
        assert editor.saveProjectAsync(str(project))
        wait_for(lambda: len(completed) == 2)
        assert read_project(project)["layers"][0]["recipe"]["exposure"] == .8
        assert not editor.dirty
    finally:
        gate.set()
        editor.close()


def test_failed_save_as_of_clean_project_stays_clean(tmp_path, qt_app, ai_store, monkeypatch):
    source = tmp_path / "photo.png"
    Image.new("RGB", (300, 200), "blue").save(source)
    editor = Editor(ai_store=ai_store)
    try:
        editor.openImage(str(source))
        wait_for(lambda: editor.hasImage and settled(editor))
        original_project = tmp_path / "original.iphoto"
        editor.saveProject(str(original_project))
        assert not editor.dirty
        original = session.write_project
        target = tmp_path / "blocked.iphoto"

        def fail_target(path, payload, overwrite=False):
            if Path(path) == target:
                raise OSError("disk full")
            return original(path, payload, overwrite=overwrite)

        monkeypatch.setattr(session, "write_project", fail_target)
        completed = []
        editor.projectSaveCompleted.connect(lambda path, error: completed.append(error))
        assert editor.saveProjectAsync(str(target))
        wait_for(lambda: bool(completed))
        assert "disk full" in completed[-1]
        assert editor.projectPath == str(original_project)
        assert not editor.dirty and not target.exists()
    finally:
        editor.close()


def test_async_save_queues_behind_recovery_without_resurrecting_it(tmp_path, qt_app, ai_store, monkeypatch):
    source = tmp_path / "photo.png"
    Image.new("RGB", (300, 200), "blue").save(source)
    editor = Editor(ai_store=ai_store)
    entered, release = Event(), Event()
    try:
        editor.openImage(str(source))
        wait_for(lambda: editor.hasImage and settled(editor))
        editor.setParameter("exposure", .4)
        editor.finishGesture()
        original = session.write_project

        def slow_recovery(path, payload, overwrite=False):
            if Path(path) == editor._recovery_path:
                entered.set()
                assert release.wait(3)
            return original(path, payload, overwrite=overwrite)

        monkeypatch.setattr(session, "write_project", slow_recovery)
        editor._autosave.start(0)
        wait_for(entered.is_set)
        project = tmp_path / "edit.iphoto"
        completed = []
        editor.projectSaveCompleted.connect(lambda path, error: completed.append((path, error)))
        started = perf_counter()
        assert editor.saveProjectAsync(str(project))
        assert perf_counter() - started < .1
        release.set()
        wait_for(lambda: bool(completed))
        assert completed[-1] == (str(project), "")
        assert not editor.dirty and not editor._recovery_path.exists()
        assert read_project(project)["layers"][0]["recipe"]["exposure"] == .4
    finally:
        release.set()
        editor.close()
