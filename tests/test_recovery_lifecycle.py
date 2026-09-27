"""A recovery copy represents unsaved work, not an already saved project."""

from pathlib import Path

from PIL import Image
from PySide6.QtCore import QPointF, QObject, Qt, QUrl
from PySide6.QtGui import QFontDatabase
from PySide6.QtQml import QQmlApplicationEngine
from PySide6.QtTest import QTest

from iphoto.controllers import session
from iphoto.workspace import Editor, ROOT
from test_ai import wait_for
from test_editor import settled


def _open_edited(editor, source):
    editor.openImage(str(source))
    wait_for(lambda: editor.hasImage and settled(editor))
    editor.setParameter("exposure", .5)
    editor.finishGesture()
    wait_for(lambda: settled(editor))
    assert editor.dirty


def test_successful_save_leaves_no_recovery_prompt_on_next_launch(tmp_path, qt_app, ai_store):
    source = tmp_path / "photo.png"
    Image.new("RGB", (300, 200), "blue").save(source)
    project = tmp_path / "edit.iphoto"
    editor = Editor(ai_store=ai_store)
    try:
        _open_edited(editor, source)
        editor.saveProject(str(project))
        assert project.exists() and not editor.dirty
    finally:
        editor.close()
    reopened = Editor(ai_store=ai_store)
    try:
        assert not reopened.canRecover
    finally:
        reopened.close()


def test_recovered_session_is_unsaved_until_project_saved(tmp_path, qt_app, ai_store):
    source = tmp_path / "photo.png"
    Image.new("RGB", (300, 200), "blue").save(source)
    editor = Editor(ai_store=ai_store)
    try:
        _open_edited(editor, source)
    finally:
        editor.close()
    reopened = Editor(ai_store=ai_store)
    try:
        assert reopened.canRecover
        reopened.recoverLatest()
        wait_for(lambda: reopened.hasImage and settled(reopened))
        assert reopened.parameters["exposure"] == .5
        assert reopened.projectPath == ""
        assert reopened.dirty
        wait_for(lambda: reopened._recovery_path.exists() and not reopened.canRecover)
        reopened.saveProject(str(tmp_path / "restored.iphoto"))
        assert not reopened.dirty
    finally:
        reopened.close()
    third = Editor(ai_store=ai_store)
    try:
        assert not third.canRecover
    finally:
        third.close()


def test_failed_project_save_retains_existing_recovery(tmp_path, qt_app, ai_store, monkeypatch):
    source = tmp_path / "photo.png"
    Image.new("RGB", (300, 200), "blue").save(source)
    editor = Editor(ai_store=ai_store)
    try:
        _open_edited(editor, source)
        assert editor._save_recovery()
        recovery = editor._recovery_path
        original = session.write_project

        def fail_project(path, payload, overwrite=False):
            if path == tmp_path / "edit.iphoto":
                raise OSError("disk full")
            return original(path, payload, overwrite=overwrite)

        monkeypatch.setattr(session, "write_project", fail_project)
        editor.saveProject(str(tmp_path / "edit.iphoto"))
        assert editor.dirty
        assert recovery.exists()
        assert not (tmp_path / "edit.iphoto").exists()
    finally:
        editor.close()


def test_failed_recovery_replacement_keeps_older_copy(tmp_path, qt_app, ai_store, monkeypatch):
    source = tmp_path / "photo.png"
    Image.new("RGB", (300, 200), "blue").save(source)
    previous = Editor(ai_store=ai_store)
    try:
        _open_edited(previous, source)
        old_copy = previous._recovery_path
    finally:
        previous.close()
    editor = Editor(ai_store=ai_store)
    try:
        editor.recoverLatest()
        wait_for(lambda: editor.hasImage and settled(editor))
        original = session.write_project

        def fail_new(path, payload, overwrite=False):
            if path == editor._recovery_path:
                raise OSError("disk full")
            return original(path, payload, overwrite=overwrite)

        monkeypatch.setattr(session, "write_project", fail_new)
        assert not editor._save_recovery()
        assert old_copy.exists()
        monkeypatch.setattr(session, "write_project", original)
        assert editor._save_recovery()
        assert editor._recovery_path.exists() and not old_copy.exists()
    finally:
        editor.close()


def test_recovery_is_visible_and_clickable_from_start_screen(tmp_path, qt_app, ai_store):
    for name in ("msyh.ttc", "msyhbd.ttc", "segoeui.ttf"):
        font = Path("C:/Windows/Fonts") / name
        if font.exists():
            QFontDatabase.addApplicationFont(str(font))
    source = tmp_path / "photo.png"
    Image.new("RGB", (300, 200), "blue").save(source)
    previous = Editor(ai_store=ai_store)
    try:
        _open_edited(previous, source)
    finally:
        previous.close()
    editor = Editor(ai_store=ai_store)
    engine = QQmlApplicationEngine()
    warnings = []
    engine.warnings.connect(lambda items: warnings.extend(i.toString() for i in items))
    engine.rootContext().setContextProperty("editor", editor)
    engine.load(QUrl.fromLocalFile(str(ROOT / "src/iphoto/ui/Main.qml")))
    assert engine.rootObjects(), warnings
    window = engine.rootObjects()[0]
    window.resize(1080, 700)
    try:
        button = window.findChild(QObject, "recoverSessionButton")
        assert button and button.property("visible") and button.property("enabled")
        QTest.qWait(100)
        window.grabWindow().save(str(ROOT / "artifacts/ux-recovery-entry-1080.png"))
        point = button.mapToScene(QPointF(button.width() / 2, button.height() / 2)).toPoint()
        QTest.mouseClick(window, Qt.LeftButton, Qt.NoModifier, point)
        wait_for(lambda: editor.hasImage and settled(editor))
        assert editor.parameters["exposure"] == .5 and editor.dirty
        wait_for(lambda: editor._recovery_path.exists() and not editor.canRecover)
        assert not button.property("visible")
        assert not warnings
    finally:
        window.close()
        editor.close()
