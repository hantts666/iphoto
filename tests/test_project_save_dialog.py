"""Project save failures leave the attempted path ready for a real UI retry."""

from pathlib import Path

from PySide6.QtCore import QPointF, QObject, Qt
from PySide6.QtTest import QTest

from iphoto.controllers import session
from iphoto.document import read_project
from iphoto.workspace import ROOT
from test_ai import wait_for
from test_import_export import ui


def _click(window, item):
    point = item.mapToScene(QPointF(item.width() / 2, item.height() / 2)).toPoint()
    QTest.mouseClick(window, Qt.LeftButton, Qt.NoModifier, point)


def test_new_project_save_failure_reopens_path_for_retry(ui, monkeypatch):
    editor, window, find, warnings, tmp_path = ui
    editor.setParameter("exposure", .4)
    editor.finishGesture()
    _click(window, find("saveProjectButton"))
    dialog = window.findChild(QObject, "projectSaveDialog")
    assert dialog and dialog.property("opened")
    suggested = Path(dialog.property("filePath"))
    assert suggested.is_absolute() and suggested.parent == tmp_path
    assert suggested.suffix == ".iphoto"
    failed = tmp_path / "failed.iphoto"
    target = tmp_path / "retry.iphoto"
    original = session.write_project

    def fail_first(path, payload, overwrite=False):
        if Path(path) == failed:
            raise OSError("disk full")
        return original(path, payload, overwrite=overwrite)

    monkeypatch.setattr(session, "write_project", fail_first)
    dialog.setProperty("filePath", str(failed))
    _click(window, find("projectSaveConfirmButton"))
    wait_for(lambda: dialog.property("opened") and "disk full" in dialog.property("errorText"))
    assert window.grabWindow().save(str(ROOT / "artifacts/ux-project-save-retry.png"))
    assert dialog.property("filePath") == str(failed)
    assert editor.dirty and not failed.exists()
    dialog.setProperty("filePath", str(target))
    _click(window, find("projectSaveConfirmButton"))
    wait_for(lambda: target.exists() and not editor.savingProject)
    assert not dialog.property("opened")
    assert editor.projectPath == str(target) and not editor.dirty
    assert read_project(target)["layers"][0]["recipe"]["exposure"] == .4
    assert not warnings


def test_suggested_project_path_avoids_existing_file(ui):
    editor, window, find, warnings, tmp_path = ui
    first = Path(editor.suggestProjectPath())
    assert first.parent == tmp_path and first.suffix == ".iphoto"
    first.write_text("reserved", encoding="utf-8")
    second = Path(editor.suggestProjectPath())
    assert second != first and not second.exists()
    project = tmp_path / "saved.iphoto"
    editor.saveProject(str(project))
    copy_path = Path(editor.suggestProjectPath())
    assert copy_path.parent == tmp_path and "副本" in copy_path.stem
    assert not warnings


def test_project_path_enter_saves_from_keyboard(ui):
    editor, window, find, warnings, tmp_path = ui
    _click(window, find("saveProjectButton"))
    dialog = window.findChild(QObject, "projectSaveDialog")
    assert dialog and dialog.property("opened")
    target = tmp_path / "keyboard.iphoto"
    path_field = find("projectSavePathField")
    assert path_field.property("activeFocus")
    path_field.setProperty("text", str(target))
    QTest.keyClick(window, Qt.Key_Return)
    wait_for(lambda: target.exists() and not editor.savingProject)
    assert editor.projectPath == str(target)
    assert not dialog.property("opened")
    assert not warnings


def test_project_browse_starts_in_path_directory(ui):
    editor, window, find, warnings, tmp_path = ui
    _click(window, find("saveProjectButton"))
    dialog = window.findChild(QObject, "projectSaveDialog")
    browse = next(child for child in dialog.findChildren(QObject)
                  if child.property("title") == "选择项目保存位置")
    assert Path(browse.property("currentFolder").toLocalFile()) == tmp_path
    other = tmp_path / "another-folder"
    other.mkdir()
    dialog.setProperty("filePath", str(other / "another.iphoto"))
    assert Path(browse.property("currentFolder").toLocalFile()) == other
    assert not warnings


def test_existing_project_save_failure_offers_same_retry_dialog(ui, monkeypatch):
    editor, window, find, warnings, tmp_path = ui
    project = tmp_path / "original.iphoto"
    editor.saveProject(str(project))
    editor.setParameter("exposure", .6)
    editor.finishGesture()
    original = session.write_project

    def fail_original(path, payload, overwrite=False):
        if Path(path) == project:
            raise OSError("disk full")
        return original(path, payload, overwrite=overwrite)

    monkeypatch.setattr(session, "write_project", fail_original)
    _click(window, find("saveProjectButton"))
    dialog = window.findChild(QObject, "projectSaveDialog")
    wait_for(lambda: dialog and dialog.property("opened") and "disk full" in dialog.property("errorText"))
    assert dialog.property("filePath") == str(project)
    assert editor.dirty
    assert read_project(project)["layers"][0]["recipe"]["exposure"] == 0
    alternate = tmp_path / "alternate.iphoto"
    dialog.setProperty("filePath", str(alternate))
    _click(window, find("projectSaveConfirmButton"))
    wait_for(lambda: alternate.exists() and not editor.savingProject)
    assert editor.projectPath == str(alternate) and not editor.dirty
    assert not warnings
