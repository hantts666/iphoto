"""Projects keep their edits when an original photo is moved or replaced."""

from pathlib import Path

from PIL import Image
from PySide6.QtCore import QPointF, QObject, Qt
from PySide6.QtTest import QTest

from iphoto.document import read_project, write_project
from iphoto.workspace import Editor, ROOT
from test_ai import wait_for
from test_editor import settled
from test_import_export import ui


def _photo(path, color):
    Image.new("RGB", (300, 200), color).save(path)


def test_moved_source_can_be_verified_and_project_reference_saved(tmp_path, qt_app, ai_store):
    old = tmp_path / "original.png"
    moved = tmp_path / "moved" / "original.png"
    moved.parent.mkdir()
    _photo(old, "blue")
    editor = Editor(ai_store=ai_store)
    try:
        editor.openImage(str(old))
        wait_for(lambda: editor.hasImage and settled(editor))
        editor.setParameter("exposure", .6)
        editor.finishGesture()
        project = tmp_path / "edit.iphoto"
        editor.saveProject(str(project))
        wait_for(lambda: settled(editor))
        old.rename(moved)
        editor.setParameter("exposure", .2)
        editor.finishGesture()
        before = editor._path
        editor.openProject(str(project))
        assert editor._path == before and editor.parameters["exposure"] == .2
        assert editor._relink_pending is not None
        assert editor.relinkProjectSource(str(moved))
        wait_for(lambda: settled(editor) and editor._path == str(moved))
        assert editor.parameters["exposure"] == .6
        assert editor.projectPath == str(project)
        assert editor.dirty
        editor.saveProject(str(project))
        assert read_project(project)["source"] == str(moved)
        assert not editor.dirty
        editor.openProject(str(project))
        wait_for(lambda: settled(editor))
        assert editor.parameters["exposure"] == .6
        assert not editor.dirty
    finally:
        editor.close()


def test_recovery_with_moved_source_offers_relink(tmp_path, qt_app, ai_store):
    source = tmp_path / "original.png"
    moved = tmp_path / "moved.png"
    _photo(source, "blue")
    editor = Editor(ai_store=ai_store)
    try:
        editor.openImage(str(source))
        wait_for(lambda: editor.hasImage and settled(editor))
        editor.setParameter("exposure", .7)
        editor.finishGesture()
        recovery = editor._recovery_dir / "older.iphoto"
        recovery.parent.mkdir(parents=True, exist_ok=True)
        write_project(recovery, editor._payload())
        source.rename(moved)
        editor.recoverLatest()
        assert editor._relink_pending is not None
        assert editor.relinkProjectSource(str(moved))
        wait_for(lambda: settled(editor) and editor._path == str(moved))
        assert editor.parameters["exposure"] == .7
        assert editor.projectPath == ""
        assert editor.dirty
    finally:
        editor.close()


def test_wrong_relink_keeps_current_edit_and_allows_retry(tmp_path, qt_app, ai_store):
    old = tmp_path / "original.png"
    moved = tmp_path / "moved.png"
    wrong = tmp_path / "wrong.png"
    _photo(old, "blue")
    _photo(wrong, "red")
    editor = Editor(ai_store=ai_store)
    try:
        editor.openImage(str(old))
        wait_for(lambda: editor.hasImage and settled(editor))
        editor.setParameter("exposure", .6)
        editor.finishGesture()
        project = tmp_path / "edit.iphoto"
        editor.saveProject(str(project))
        wait_for(lambda: settled(editor))
        old.rename(moved)
        editor.setParameter("exposure", .2)
        editor.finishGesture()
        editor.openProject(str(project))
        failures = []
        editor.sourceRelinkFailed.connect(failures.append)
        assert editor.relinkProjectSource(str(wrong))
        wait_for(lambda: settled(editor) and failures)
        assert "源图片已变化" in failures[-1]
        assert editor._path == str(old)
        assert editor.parameters["exposure"] == .2
        assert editor._relink_pending is not None
        assert editor.relinkProjectSource(str(moved))
        wait_for(lambda: settled(editor) and editor._path == str(moved))
        assert editor.parameters["exposure"] == .6
    finally:
        editor.close()


def test_relink_dialog_retries_from_real_qml_button(ui):
    editor, window, find, warnings, tmp_path = ui
    source = Path(editor._path)
    editor.setParameter("exposure", .5)
    editor.finishGesture()
    project = tmp_path / "edit.iphoto"
    editor.saveProject(str(project))
    wait_for(lambda: settled(editor))
    moved = tmp_path / "moved.png"
    source.rename(moved)
    wrong = tmp_path / "wrong.png"
    _photo(wrong, "red")
    editor.openProject(str(project))
    dialog = window.findChild(QObject, "sourceRelinkDialog")
    assert dialog and dialog.property("opened")
    assert window.property("modalActive")
    QTest.qWait(100)
    window.grabWindow().save(str(ROOT / "artifacts/ux-project-relink.png"))
    dialog.setProperty("filePath", str(wrong))
    button = find("relinkConfirmButton")

    def click_confirm():
        point = button.mapToScene(QPointF(button.width() / 2, button.height() / 2)).toPoint()
        QTest.mouseClick(window, Qt.LeftButton, Qt.NoModifier, point)

    click_confirm()
    wait_for(lambda: settled(editor) and bool(dialog.property("errorText")))
    assert dialog.property("opened")
    assert editor._path == str(source)
    dialog.setProperty("filePath", str(moved))
    path_field = find("relinkPathField")
    assert path_field.property("activeFocus")
    QTest.keyClick(window, Qt.Key_Return)
    wait_for(lambda: settled(editor) and editor._path == str(moved))
    assert not dialog.property("opened")
    assert editor.dirty
    assert "已找回原照片" in editor.status
    assert not warnings


def test_relink_browse_starts_beside_project(ui):
    editor, window, find, warnings, tmp_path = ui
    source = Path(editor._path)
    project = tmp_path / "edit.iphoto"
    editor.saveProject(str(project))
    moved = tmp_path / "moved.png"
    source.rename(moved)
    editor.openProject(str(project))
    dialog = window.findChild(QObject, "sourceRelinkDialog")
    assert dialog and dialog.property("opened")
    browse = next(child for child in dialog.findChildren(QObject)
                  if child.property("title") == "选择项目原照片")
    assert Path(browse.property("currentFolder").toLocalFile()) == tmp_path
    other = tmp_path / "another-folder"
    other.mkdir()
    dialog.setProperty("filePath", str(other / "moved.png"))
    assert Path(browse.property("currentFolder").toLocalFile()) == other
    assert not warnings


def test_replaced_source_prompts_and_cancel_keeps_current_document(ui):
    editor, window, find, warnings, tmp_path = ui
    source = Path(editor._path)
    moved = tmp_path / "original-copy.png"
    moved.write_bytes(source.read_bytes())
    editor.setParameter("exposure", .5)
    editor.finishGesture()
    project = tmp_path / "edit.iphoto"
    editor.saveProject(str(project))
    wait_for(lambda: settled(editor))
    _photo(source, "red")
    editor.openProject(str(project))
    dialog = window.findChild(QObject, "sourceRelinkDialog")
    wait_for(lambda: settled(editor) and dialog.property("opened"))
    assert "源图片已变化" in dialog.property("errorText")
    assert editor._path == str(source)
    assert editor.parameters["exposure"] == .5
    dialog.close()
    wait_for(lambda: not dialog.property("opened"))
    assert editor._relink_pending is None
    assert editor._path == str(source)
    assert "已取消找回原照片" in editor.status
    assert window.grabWindow().save(str(ROOT / "artifacts/ux-relink-cancel-status.png"))
    assert not warnings
