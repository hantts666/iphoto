"""Import breadth (MPO/RAW), export dialog format+quality, warm & precache."""

import sys
import types
from pathlib import Path

import numpy as np
import pytest
from PIL import Image
from PySide6.QtCore import QPointF, QObject, QProcess, Qt, QUrl
from PySide6.QtGui import QFontDatabase
from PySide6.QtQml import QQmlApplicationEngine, QQmlExpression, qmlContext
from PySide6.QtTest import QTest

from iphoto.controllers import pixel_selections
from iphoto.engine import load_source
from iphoto.workspace import Editor, ROOT
from test_ai import wait_for
from test_editor import settled


def test_mpo_photos_import(tmp_path):
    path = tmp_path / "wechat.mpo"
    Image.new("RGB", (120, 80), (200, 120, 60)).save(path, format="MPO")
    source = load_source(path)
    assert source.image.size == (120, 80)


def test_raw_import_uses_rawpy(tmp_path, monkeypatch):
    array = np.zeros((60, 80, 3), dtype=np.uint8)
    array[:] = (30, 90, 150)

    class FakeRaw:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def postprocess(self, **kwargs):
            return array

    fake = types.SimpleNamespace(
        open=lambda _path: FakeRaw(),
        ColorSpace=types.SimpleNamespace(sRGB=1),
    )
    monkeypatch.setitem(sys.modules, "rawpy", fake)
    (tmp_path / "shot.dng").write_bytes(b"fake raw container")
    source = load_source(tmp_path / "shot.dng")
    assert source.image.size == (80, 60)
    assert source.image.getpixel((4, 4)) == (30, 90, 150)


def test_raw_without_rawpy_explains_install(tmp_path, monkeypatch):
    monkeypatch.setitem(sys.modules, "rawpy", None)
    (tmp_path / "shot.nef").write_bytes(b"fake raw container")
    with pytest.raises(ValueError, match="rawpy"):
        load_source(tmp_path / "shot.nef")


@pytest.fixture
def ui(qt_app, ai_store, tmp_path):
    for name in ("msyh.ttc", "msyhbd.ttc", "segoeui.ttf"):
        font = Path("C:/Windows/Fonts") / name
        if font.exists():
            QFontDatabase.addApplicationFont(str(font))
    path = tmp_path / "source.png"
    Image.new("RGB", (300, 200), (55, 90, 130)).save(path)
    editor = Editor(ai_store=ai_store)
    engine = QQmlApplicationEngine()
    warnings = []
    engine.warnings.connect(lambda items: warnings.extend(i.toString() for i in items))
    engine.rootContext().setContextProperty("editor", editor)
    engine.load(QUrl.fromLocalFile(str(ROOT / "src/iphoto/ui/Main.qml")))
    assert engine.rootObjects(), warnings
    window = engine.rootObjects()[0]
    window.resize(1440, 930)
    editor.openImage(str(path))
    wait_for(lambda: editor.hasImage and settled(editor))

    def find(name):
        stack = [window.contentItem()]
        while stack:
            item = stack.pop()
            if item.objectName() == name:
                return item
            # Pointer handlers belong to their delegate but are not visual
            # childItems. Inspect the current visual delegate's direct children.
            for child in item.children():
                if child.objectName() == name:
                    return child
            stack.extend(item.childItems())
        found = window.findChild(QObject, name)
        if found is not None:
            return found
        raise AssertionError(name)

    yield editor, window, find, warnings, tmp_path
    window.close()
    editor.close()
    qt_app.processEvents()


def test_export_dialog_writes_chosen_format_and_quality(ui):
    editor, window, find, warnings, tmp_path = ui
    from PySide6.QtCore import QObject as _QObject
    dialog = window.findChild(_QObject, "exportDialog")
    assert dialog is not None
    dialog.open()
    QTest.qWait(200)
    dialog.setProperty("filePath", str(tmp_path / "out"))
    dialog.setProperty("formatIndex", 0)
    dialog.setProperty("quality", 85)
    confirm = find("exportConfirmButton")
    point = confirm.mapToScene(QPointF(confirm.width() / 2, confirm.height() / 2)).toPoint()
    QTest.mouseClick(window, Qt.LeftButton, Qt.NoModifier, point)
    wait_for(lambda: settled(editor) and (tmp_path / "out.jpg").exists())
    assert (tmp_path / "out.jpg").stat().st_size > 0
    dialog.open()
    QTest.qWait(200)
    dialog.setProperty("filePath", str(tmp_path / "out"))
    dialog.setProperty("formatIndex", 1)
    point = confirm.mapToScene(QPointF(confirm.width() / 2, confirm.height() / 2)).toPoint()
    QTest.mouseClick(window, Qt.LeftButton, Qt.NoModifier, point)
    wait_for(lambda: settled(editor) and (tmp_path / "out.png").exists())
    assert (tmp_path / "out.png").stat().st_size > 0
    editor.setParameter("exposure", 1.5)
    editor.finishGesture()
    wait_for(lambda: settled(editor))
    editor.exportWithQuality(QUrl.fromLocalFile(str(tmp_path / "q100.jpg")).toString(), 100)
    wait_for(lambda: settled(editor))
    editor.exportWithQuality(QUrl.fromLocalFile(str(tmp_path / "q80.jpg")).toString(), 80)
    wait_for(lambda: settled(editor))
    assert (tmp_path / "q80.jpg").stat().st_size < (tmp_path / "q100.jpg").stat().st_size
    assert not warnings, warnings


def test_export_dialog_path_accepts_enter(ui):
    editor, window, find, warnings, tmp_path = ui
    dialog = window.findChild(QObject, "exportDialog")
    dialog.open()
    QTest.qWait(80)
    target = tmp_path / "keyboard.jpg"
    path_field = find("exportPathField")
    assert path_field.property("activeFocus")
    path_field.setProperty("text", str(target))
    QTest.keyClick(window, Qt.Key_Return)
    wait_for(lambda: target.exists() and not editor.busy, seconds=3)
    assert not dialog.property("opened")
    assert not warnings


def test_export_format_switch_updates_visible_destination(ui):
    editor, window, find, warnings, tmp_path = ui
    dialog = window.findChild(QObject, "exportDialog")
    dialog.open()
    QTest.qWait(80)
    destination = tmp_path / "portrait.final.jpeg"
    dialog.setProperty("filePath", str(destination))
    formats = find("exportFormatBox")
    formats.forceActiveFocus()
    QTest.keyClick(window, Qt.Key_Down)
    assert dialog.property("formatIndex") == 1
    assert dialog.property("filePath") == str(tmp_path / "portrait.final.png")
    QTest.keyClick(window, Qt.Key_Up)
    assert dialog.property("formatIndex") == 0
    assert dialog.property("filePath") == str(tmp_path / "portrait.final.jpg")
    QTest.keyClick(window, Qt.Key_Down)
    assert dialog.property("formatIndex") == 1
    assert dialog.property("filePath") == str(tmp_path / "portrait.final.png")
    assert find("exportPathField").property("text") == str(tmp_path / "portrait.final.png")
    browse = next(child for child in dialog.findChildren(QObject)
                  if child.property("title") == "导出为新文件")
    assert QQmlExpression(qmlContext(browse), browse, "selectedNameFilter.index").evaluate()[0] == 1
    assert window.grabWindow().save(str(ROOT / "artifacts/ux-export-format-switch.png"))
    confirm = find("exportConfirmButton")
    point = confirm.mapToScene(QPointF(confirm.width() / 2, confirm.height() / 2)).toPoint()
    QTest.mouseClick(window, Qt.LeftButton, Qt.NoModifier, point)
    output = tmp_path / "portrait.final.png"
    wait_for(lambda: output.exists() and not editor.busy)
    assert Image.open(output).format == "PNG"
    assert not destination.exists()
    assert not warnings


def test_export_browse_starts_in_path_directory(ui):
    editor, window, find, warnings, tmp_path = ui
    dialog = window.findChild(QObject, "exportDialog")
    dialog.open()
    browse = next(child for child in dialog.findChildren(QObject)
                  if child.property("title") == "导出为新文件")
    assert Path(browse.property("currentFolder").toLocalFile()) == tmp_path
    other = tmp_path / "another-folder"
    other.mkdir()
    dialog.setProperty("filePath", str(other / "next.jpg"))
    assert Path(browse.property("currentFolder").toLocalFile()) == other
    assert not warnings


def test_open_dialogs_follow_current_photo_and_project(ui):
    editor, window, find, warnings, tmp_path = ui
    dialogs = window.findChildren(QObject)
    photo_dialog = next(item for item in dialogs if item.property("title") == "选择照片")
    project_dialog = next(item for item in dialogs if item.property("title") == "打开项目")

    def folder(dialog):
        return Path(dialog.property("currentFolder").toLocalFile())

    assert folder(photo_dialog) == tmp_path
    assert folder(project_dialog) == tmp_path
    projects = tmp_path / "projects"
    projects.mkdir()
    editor.saveProject(str(projects / "edit.iphoto"))
    assert folder(photo_dialog) == tmp_path
    assert folder(project_dialog) == projects
    other = tmp_path / "other"
    other.mkdir()
    photo = other / "next.png"
    Image.new("RGB", (300, 200), (25, 50, 75)).save(photo)
    editor.openImage(str(photo))
    wait_for(lambda: editor.hasImage and settled(editor) and editor._path == str(photo))
    assert folder(photo_dialog) == other
    assert folder(project_dialog) == other
    assert not warnings


def test_choose_smart_warms_embedding(ui, monkeypatch):
    editor, window, find, warnings, tmp_path = ui
    requests = []
    monkeypatch.setattr(pixel_selections, "available", lambda: True)
    monkeypatch.setattr(Editor, "_start_warm", lambda self: requests.append(self._sha))
    editor.selection.chooseTool("smart")
    assert requests == [editor._sha]


def test_point_status_mentions_prompt_count(ui, monkeypatch):
    editor, window, find, warnings, tmp_path = ui
    monkeypatch.setattr(pixel_selections, "available", lambda: True)
    monkeypatch.setattr(pixel_selections, "warm", lambda _: None)
    monkeypatch.setattr(Editor, "_request", lambda self, op, **data: None)
    editor.selection.chooseTool("smart")
    editor.pixelPoint([0.4, 0.5], True)
    assert "1 个提示点" in editor.status
    editor._pixel_points = [[0.4, 0.5, 1]]  # complete() would normally record these
    editor.pixelPoint([0.6, 0.5], False)
    assert "2 个提示点" in editor.status


def test_precache_fills_precise_and_hover_previews(ui, monkeypatch):
    from iphoto.segmentation.classical import bitmap_mask
    from test_v14 import catalog

    editor, window, find, warnings, tmp_path = ui
    monkeypatch.setattr(pixel_selections, "available", lambda: True)
    captured = []
    monkeypatch.setattr(Editor, "_request", lambda self, op, **data: captured.append(data))
    editor._scene.set(catalog())
    editor.changed.emit()
    ids = [o["id"] for o in editor.sceneObjects]
    pixel_selections.precache(editor, ids)
    assert len(captured) == len(ids)
    assert all(request["context"]["purpose"] == "precache" for request in captured)
    assert all(len(request["jobs"]) == 1 for request in captured)
    assert {request["jobs"][0]["id"] for request in captured} == set(ids)
    alpha = __import__("test_pixel_selection", fromlist=["ring"]).ring()[1]
    mask = bitmap_mask(alpha, "precache fixture")
    for request in captured:
        pixel_selections.complete(
            editor,
            {"items": [{"id": request["jobs"][0]["id"], "mask": mask, "quality": {}}]},
            request["context"],
        )
    assert set(editor._scene.precise) == set(ids)
    previews = [row["maskPreview"] for row in editor._scene.rows()]
    assert all(p.startswith("data:image/png") for p in previews)
    assert editor.hasSelectionDraft is False  # precache must not create a draft
    assert all(row["pixelStatus"] == "ready" for row in editor.sceneObjects)


def test_failed_precache_object_is_no_longer_shown_as_pending(ui):
    from test_v14 import catalog

    editor, window, find, warnings, tmp_path = ui
    editor._scene.set(catalog())
    lid = editor.sceneObjects[0]["id"]
    editor._scene.mark_pixel_status([lid], "pending")
    pixel_selections.complete(
        editor,
        {"items": []},
        {"purpose": "precache", "object_id": lid, "status_epoch": editor._status_epoch},
    )
    row = next(row for row in editor.sceneObjects if row["id"] == lid)
    assert row["pixelStatus"] == "unavailable" and not row["pixelReady"]
    assert "0/3 已就绪" in editor.status and "1 个未可靠贴边" in editor.status
    QTest.qWait(80)
    stack = [find("sceneRow_" + lid)]
    hints = []
    while stack:
        item = stack.pop()
        if item.property("hint") is not None:
            hints.append(item.property("hint"))
        stack.extend(item.childItems())
    assert any("点击可重试" in hint for hint in hints)


def test_background_contours_do_not_replace_newer_export_status(ui):
    from test_v14 import catalog

    editor, _, _, _, _ = ui
    editor._scene.set(catalog())
    lid = editor.sceneObjects[0]["id"]
    context = {"purpose": "precache", "object_id": lid,
               "status_epoch": editor._status_epoch}
    editor._scene.mark_pixel_status([lid], "pending")
    editor._notify("已按原图尺寸导出：export.jpg")
    pixel_selections.complete(editor, {"items": []}, context)
    assert editor.status == "已按原图尺寸导出：export.jpg"
    assert next(row for row in editor.sceneObjects if row["id"] == lid)["pixelStatus"] == "unavailable"


def test_large_image_preview_proxy_and_full_resolution_export(ui, tmp_path):
    editor, window, find, warnings, tmp = ui
    big = tmp / "big.png"
    Image.new("RGB", (3200, 2400), (70, 80, 90)).save(big)
    editor.openImage(str(big))
    wait_for(lambda: editor.hasImage and settled(editor))
    from PySide6.QtCore import QUrl

    with Image.open(QUrl(editor.previewUrl).toLocalFile()) as preview:
        assert max(preview.size) <= 1600
    out = tmp / "full.png"
    editor.exportImage(str(out))
    wait_for(lambda: settled(editor) and out.exists())
    with Image.open(out) as exported:
        assert exported.size == (3200, 2400)
    assert not warnings, warnings


def test_default_export_path_is_safe_and_second_export_gets_new_name(ui):
    from PySide6.QtCore import QObject as _QObject

    editor, window, find, warnings, tmp_path = ui
    window.resize(1080, 700)
    QTest.qWait(80)
    assert not window.property("chatOpen")
    dialog = window.findChild(_QObject, "exportDialog")
    assert dialog is not None
    dialog.open()
    QTest.qWait(120)
    assert window.grabWindow().save(str(ROOT / "artifacts/ux-min-export-dialog.png"))
    dialog.close()

    def export_default():
        dialog.open()
        QTest.qWait(120)
        path = Path(dialog.property("filePath"))
        assert path.is_absolute() and path.parent == tmp_path
        confirm = find("exportConfirmButton")
        point = confirm.mapToScene(
            QPointF(confirm.width() / 2, confirm.height() / 2)
        ).toPoint()
        QTest.mouseClick(window, Qt.LeftButton, Qt.NoModifier, point)
        wait_for(lambda: settled(editor) and path.exists())
        return path

    first = export_default()
    second = export_default()
    assert first != second
    assert first.stat().st_size > 0 and second.stat().st_size > 0
    assert not warnings, warnings


def test_invalid_export_path_keeps_dialog_open_for_correction(ui):
    from PySide6.QtCore import QObject as _QObject

    editor, window, find, warnings, tmp_path = ui
    dialog = window.findChild(_QObject, "exportDialog")
    dialog.open()
    QTest.qWait(120)
    confirm = find("exportConfirmButton")
    point = confirm.mapToScene(
        QPointF(confirm.width() / 2, confirm.height() / 2)
    ).toPoint()
    for path, format_index, expected in [
        ("relative.jpg", 0, "完整路径"),
        (str(tmp_path / "source.png"), 1, "不能覆盖原图"),
    ]:
        dialog.setProperty("formatIndex", format_index)
        dialog.setProperty("filePath", path)
        QTest.mouseClick(window, Qt.LeftButton, Qt.NoModifier, point)
        QTest.qWait(80)
        assert dialog.property("opened"), path
        assert expected in editor.status
        assert expected in find("exportErrorText").property("text")
        if path == "relative.jpg":
            assert window.grabWindow().save(str(ROOT / "artifacts/ux-min-export-error.png"))
    dialog.close()


def test_export_worker_failure_keeps_dialog_and_allows_retry(ui, monkeypatch):
    from PySide6.QtCore import QObject as _QObject
    from iphoto.controllers import export_process

    editor, window, find, warnings, tmp_path = ui
    dialog = window.findChild(_QObject, "exportDialog")
    assert dialog is not None
    dialog.open()
    QTest.qWait(100)
    first = tmp_path / "taken-after-check.png"
    dialog.setProperty("formatIndex", 1)
    dialog.setProperty("filePath", str(first))
    queue = export_process.queue

    def occupied_after_preflight(owner, target, quality):
        monkeypatch.setattr(export_process, "queue", queue)
        first.write_bytes(b"another program created this file")
        return queue(owner, target, quality)

    monkeypatch.setattr(export_process, "queue", occupied_after_preflight)
    confirm = find("exportConfirmButton")
    point = confirm.mapToScene(
        QPointF(confirm.width() / 2, confirm.height() / 2)
    ).toPoint()
    QTest.mouseClick(window, Qt.LeftButton, Qt.NoModifier, point)
    wait_for(lambda: "目标文件已存在" in editor.status and not editor.busy)
    assert dialog.property("opened")
    assert "目标文件已存在" in find("exportErrorText").property("text")
    assert find("exportPathField").property("activeFocus")
    assert first.read_bytes() == b"another program created this file"
    assert window.grabWindow().save(str(ROOT / "artifacts/ux-export-worker-error.png"))

    second = tmp_path / "retry.png"
    dialog.setProperty("filePath", str(second))
    QTest.mouseClick(window, Qt.LeftButton, Qt.NoModifier, point)
    wait_for(lambda: second.exists() and not editor.busy)
    assert not dialog.property("opened")
    assert not warnings, warnings


def test_large_export_dialog_shows_pending_until_worker_finishes(ui):
    from PySide6.QtCore import QObject as _QObject

    editor, window, find, warnings, tmp_path = ui
    big = tmp_path / "large.png"
    Image.new("RGB", (6000, 4000), (80, 100, 120)).save(big)
    editor.openImage(str(big))
    wait_for(lambda: editor.hasImage and settled(editor) and editor._width == 6000)
    dialog = window.findChild(_QObject, "exportDialog")
    dialog.open()
    QTest.qWait(100)
    output = tmp_path / "large-export.jpg"
    dialog.setProperty("filePath", str(output))
    confirm = find("exportConfirmButton")
    point = confirm.mapToScene(
        QPointF(confirm.width() / 2, confirm.height() / 2)
    ).toPoint()
    QTest.mouseClick(window, Qt.LeftButton, Qt.NoModifier, point)
    assert dialog.property("opened") and dialog.property("pending")
    assert not confirm.property("enabled")
    assert window.grabWindow().save(str(ROOT / "artifacts/ux-export-pending.png"))
    wait_for(lambda: output.exists() and settled(editor), seconds=30)
    assert not dialog.property("opened") and not dialog.property("pending")
    with Image.open(output) as exported:
        assert exported.size == (6000, 4000)
    assert not warnings, warnings


def test_export_dialog_exits_pending_if_worker_stops(ui):
    from PySide6.QtCore import QObject as _QObject

    editor, window, find, warnings, tmp_path = ui
    dialog = window.findChild(_QObject, "exportDialog")
    dialog.open()
    QTest.qWait(100)
    output = tmp_path / "not-exported.jpg"
    dialog.setProperty("filePath", str(output))
    confirm = find("exportConfirmButton")
    point = confirm.mapToScene(
        QPointF(confirm.width() / 2, confirm.height() / 2)
    ).toPoint()
    QTest.mouseClick(window, Qt.LeftButton, Qt.NoModifier, point)
    assert dialog.property("pending")
    editor._export_process.kill()
    wait_for(lambda: not dialog.property("pending"))
    assert dialog.property("opened")
    assert "导出进程异常退出" in find("exportErrorText").property("text")
    assert not output.exists()
    assert not warnings, warnings


def test_large_export_can_cancel_cleanly_edit_and_retry(ui):
    from PySide6.QtCore import QObject as _QObject

    editor, window, find, warnings, tmp_path = ui
    big = tmp_path / "cancel-source.png"
    Image.new("RGB", (6000, 4000), (70, 90, 110)).save(big)
    editor.openImage(str(big))
    wait_for(lambda: editor.hasImage and settled(editor) and editor._width == 6000)
    dialog = window.findChild(_QObject, "exportDialog")
    dialog.open()
    QTest.qWait(100)
    output = tmp_path / "cancel-output.png"
    dialog.setProperty("formatIndex", 1)
    dialog.setProperty("filePath", str(output))
    confirm = find("exportConfirmButton")
    point = confirm.mapToScene(
        QPointF(confirm.width() / 2, confirm.height() / 2)
    ).toPoint()
    QTest.mouseClick(window, Qt.LeftButton, Qt.NoModifier, point)
    wait_for(lambda: editor._export_request is not None and editor._export_process.state() == QProcess.Running)
    staging = Path(editor._export_request["stage_dir"])
    assert dialog.property("pending") and editor.busy
    cancel = find("cancelExportButton")
    cancel_point = cancel.mapToScene(
        QPointF(cancel.width() / 2, cancel.height() / 2)
    ).toPoint()
    QTest.mouseClick(window, Qt.LeftButton, Qt.NoModifier, cancel_point)
    wait_for(lambda: not editor.busy and not dialog.property("pending"))
    assert dialog.property("opened")
    assert "已取消" in find("exportErrorText").property("text")
    assert not output.exists() and not staging.exists()
    assert not editor._export_cleanup_pending
    editor.setParameter("exposure", 0.5)
    wait_for(lambda: settled(editor))
    assert editor._layer()["recipe"]["exposure"] == 0.5
    QTest.mouseClick(window, Qt.LeftButton, Qt.NoModifier, point)
    wait_for(lambda: output.exists() and settled(editor), seconds=30)
    assert not dialog.property("opened")
    with Image.open(output) as exported:
        assert exported.size == (6000, 4000)
    assert not warnings, warnings


def test_cancel_during_atomic_export_write_removes_temporary_file(ui):
    from PySide6.QtCore import QObject as _QObject

    editor, window, find, warnings, tmp_path = ui
    big = tmp_path / "writing-source.png"
    Image.new("RGB", (10000, 6000), (70, 90, 110)).save(big)
    editor.openImage(str(big))
    wait_for(lambda: editor.hasImage and settled(editor) and editor._width == 10000)
    dialog = window.findChild(_QObject, "exportDialog")
    dialog.open()
    QTest.qWait(100)
    output = tmp_path / "cancel-writing.png"
    dialog.setProperty("formatIndex", 1)
    dialog.setProperty("filePath", str(output))
    confirm = find("exportConfirmButton")
    point = confirm.mapToScene(
        QPointF(confirm.width() / 2, confirm.height() / 2)
    ).toPoint()
    QTest.mouseClick(window, Qt.LeftButton, Qt.NoModifier, point)
    assert editor._export_request is not None
    staging = Path(editor._export_request["stage_dir"])
    wait_for(lambda: any(staging.glob("*.tmp")), seconds=30)
    cancel = find("cancelExportButton")
    cancel_point = cancel.mapToScene(
        QPointF(cancel.width() / 2, cancel.height() / 2)
    ).toPoint()
    QTest.mouseClick(window, Qt.LeftButton, Qt.NoModifier, cancel_point)
    wait_for(lambda: not editor.busy)
    wait_for(lambda: not staging.exists(), seconds=10)
    assert not output.exists() and not staging.exists()
    assert not editor._export_cleanup_pending
    assert dialog.property("opened") and not dialog.property("pending")
    assert not warnings, warnings


def test_export_start_failure_clears_pending_and_staging(ui, monkeypatch):
    from PySide6.QtCore import QObject as _QObject
    from iphoto.controllers import export_process

    editor, window, find, warnings, tmp_path = ui
    dialog = window.findChild(_QObject, "exportDialog")
    dialog.open()
    QTest.qWait(100)
    output = tmp_path / "not-started.jpg"
    dialog.setProperty("filePath", str(output))
    monkeypatch.setattr(export_process.sys, "executable", str(tmp_path / "missing-python.exe"))
    confirm = find("exportConfirmButton")
    point = confirm.mapToScene(
        QPointF(confirm.width() / 2, confirm.height() / 2)
    ).toPoint()
    QTest.mouseClick(window, Qt.LeftButton, Qt.NoModifier, point)
    wait_for(lambda: not dialog.property("pending"))
    assert dialog.property("opened")
    assert "未能启动" in find("exportErrorText").property("text")
    assert not output.exists()
    assert not list(tmp_path.glob(".iphoto-export-*"))
    assert not warnings, warnings


def test_demo_export_defaults_to_user_folder_instead_of_bundled_assets(ui):
    editor, window, find, warnings, tmp_path = ui
    editor.loadDemo()
    wait_for(lambda: editor.hasImage and settled(editor))
    target = Path(editor.suggestExportPath(0))
    assert target.is_absolute() and target.parent.is_dir()
    assert target.parent != ROOT / "assets"
    assert target.name.startswith("山湖之间-编辑")
