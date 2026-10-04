"""Real row input and visible contour quality in the complete QML window.

Synthetic masks test routing/quality ownership, not model accuracy. Large
photo and real local model evidence lives in artifacts/ux-object-status-69.
"""

from copy import deepcopy

from PIL import Image, ImageChops, ImageDraw
import pytest
from PySide6.QtCore import QPointF, Qt
from PySide6.QtTest import QSignalSpy, QTest

from iphoto.controllers import pixel_selections
from iphoto.document import raster_mask
from iphoto.segmentation.classical import bitmap_mask
from test_ai import wait_for
from test_canvas_ui import canvas  # noqa: F401
from test_editor import settled
from test_v14 import catalog, workspace_v14  # noqa: F401


WARNING = "模型对边界信心较低，请补充提示点"


def install(ui, warnings=False):
    ui.e._scene.set(catalog())
    for obj in ui.e._scene.catalog["objects"]:
        mask = bitmap_mask(raster_mask(obj["mask"], (300, 200)), obj["name"])
        ui.e._scene.set_precise(obj["id"], mask, {
            "elapsed_ms": 9876, "warnings": [WARNING] if warnings else [],
        })
    ui.e.changed.emit()
    QTest.qWait(80)


def reveal(ui, name):
    item, scroll = ui.find(name), ui.find("propertyScroll")
    for _ in range(40):
        top = item.mapToScene(QPointF(0, 0)).y()
        begin = scroll.mapToScene(QPointF(0, 0)).y()
        if begin + 4 <= top and top + item.height() <= begin + scroll.height() - 4:
            flick = scroll.property("contentItem")
            wait_for(lambda: not flick.property("moving"))
            QTest.qWait(40)
            top = item.mapToScene(QPointF(0, 0)).y()
            if not (begin + 4 <= top and top + item.height() <= begin + scroll.height() - 4):
                continue
            return
        delta = -120 if top >= begin + scroll.height() - item.height() else 120
        QTest.wheelEvent(ui.w, ui.point("propertyScroll"), QPointF(0, delta).toPoint())
        QTest.qWait(35)
    raise AssertionError(name)


def test_left_click_with_hover_enabled_keeps_cached_warning_and_can_correct(canvas, monkeypatch):  # noqa: F811
    ui = canvas
    install(ui, warnings=True)
    before = deepcopy((ui.e._layers, ui.e._history, ui.e._cursor))
    assert ui.w.property("objectPreviewEnabled")
    assert ui.find("sceneStatus_object-1").property("text") == "需检查"
    # Click the visible name, with all production input handlers enabled.
    ui.click("sceneSelect_object-1")
    wait_for(lambda: settled(ui.e) and ui.e.hasSelectionDraft)
    assert WARNING in ui.e.selectionQuality and WARNING in ui.e.status
    assert "0.0s" in ui.e.selectionQuality and "9.9s" not in ui.e.selectionQuality
    assert WARNING in ui.e.conversation[-1]["text"]
    assert deepcopy((ui.e._layers, ui.e._history, ui.e._cursor)) == before
    mask = deepcopy(ui.e._candidate)
    monkeypatch.setattr(pixel_selections, "available", lambda: True)
    monkeypatch.setattr(pixel_selections, "warm", lambda _: None)
    reveal(ui, "refineAdvancedButton")
    ui.click("refineAdvancedButton")
    reveal(ui, "correctPixelPointsButton")
    clicked = QSignalSpy(ui.find("correctPixelPointsButton").clicked)
    ui.click("correctPixelPointsButton")
    assert clicked.count() == 1, (ui.point("correctPixelPointsButton"), ui.size)
    assert ui.e.selection.tool == "smart" and ui.e._pixel_hint == mask
    assert ui.e._candidate == mask
    assert deepcopy((ui.e._layers, ui.e._history, ui.e._cursor)) == before


def test_checkbox_and_right_menu_have_independent_real_clicks(canvas):  # noqa: F811
    ui = canvas
    install(ui)
    ui.click("sceneCheck_object-1")
    assert ui.e.checkedObjectCount == 1 and not ui.e.hasSelectionDraft
    ui.click("sceneSelect_object-1")
    wait_for(lambda: settled(ui.e) and ui.e.hasSelectionDraft)
    original = deepcopy(ui.e._candidate)
    reveal(ui, "sceneSelect_object-2")
    QTest.mouseClick(ui.w, Qt.RightButton, Qt.NoModifier, ui.point("sceneSelect_object-2"))
    QTest.qWait(100)
    assert ui.find("sceneElementMenu").property("visible")
    assert ui.e._candidate == original
    ui.click("elemMenuReplace")
    wait_for(lambda: settled(ui.e) and ui.e._candidate != original)
    assert ui.e._scene.selected == {"object-2"}
    assert ui.e._candidate["bitmap"] == ui.e._scene.precise["object-2"]["mask"]["bitmap"]
    assert ui.e._cursor == 0 and len(ui.e._layers) == 1


def test_restored_catalog_progress_updates_then_yields_to_foreground_status(canvas, monkeypatch):  # noqa: F811
    ui = canvas
    ui.e._scene.set(catalog())
    ui.e._scene.remember(ui.e._scene_key())
    ui.e._scene.set(None)
    ui.e.changed.emit()
    captured = []
    real_request = ui.e._request

    def request(op, **data):
        if op == "segment":
            captured.append(deepcopy(data))
            return True
        return real_request(op, **data)

    monkeypatch.setattr(ui.e, "_request", request)
    monkeypatch.setattr(pixel_selections, "available", lambda: True)
    ui.click("analyzeSceneButton")
    assert len(captured) == 3 and not ui.e.busy
    assert ui.find("sceneStatus_object-1").property("text") == "准备中"
    obj = ui.e._scene.catalog["objects"][0]
    mask = bitmap_mask(raster_mask(obj["mask"], (300, 200)), obj["name"])
    pixel_selections.complete(ui.e, {"items": [{"id": "object-1", "mask": mask,
                                                "quality": {"warnings": [WARNING]}}]}, captured[0]["context"])
    assert "1/3 已就绪" in ui.e.status and "2 个预计算中" in ui.e.status
    assert "1 个范围需检查" in ui.e.status
    ui.e._notify("已按原图尺寸导出：portrait.png")
    pixel_selections.complete(ui.e, {"items": []}, captured[1]["context"])
    assert ui.e.status == "已按原图尺寸导出：portrait.png"
    QTest.qWait(40)
    assert ui.find("sceneStatus_object-2").property("text") == "需重选"
    assert not ui.e.hasSelectionDraft and ui.e._cursor == 0


def test_all_contour_states_and_long_name_fit_without_recreating_rows(canvas):  # noqa: F811
    ui = canvas
    data = catalog()
    data["objects"][0]["name"] = "很长的画面元素名称" * 8
    ui.e._scene.set(data)
    ui.e.changed.emit()
    QTest.qWait(60)
    button = ui.find("sceneSelect_object-1")
    status = ui.find("sceneStatus_object-1")
    assert status.property("text") == "待准备"
    ui.e._scene.mark_pixel_status(["object-1"], "pending")
    ui.e.changed.emit()
    assert ui.find("sceneSelect_object-1") is button
    assert status.property("text") == "准备中"
    obj = ui.e._scene.catalog["objects"][0]
    mask = bitmap_mask(raster_mask(obj["mask"], (300, 200)), obj["name"])
    ui.e._scene.set_precise("object-1", mask, {"warnings": []})
    ui.e.changed.emit()
    QTest.qWait(40)
    assert status.property("text") == "可选"
    row = ui.find("sceneRow_object-1")
    left = status.mapToScene(QPointF(0, 0)).x()
    right = row.mapToScene(QPointF(row.width(), 0)).x()
    assert left + status.width() <= right and status.width() > 0
    assert button.width() <= row.width() - 20
    assert ui.find("sceneSelect_object-1") is button


@pytest.mark.parametrize("auto_apply", [False, True])
def test_mixed_cached_and_new_masks_keep_included_and_excluded_warnings(workspace_v14, auto_apply):  # noqa: F811
    editor = workspace_v14
    editor._scene.set(catalog())
    objects = editor._scene.catalog["objects"]
    alpha = [raster_mask(obj["mask"], (200, 140)) for obj in objects[:2]]
    exclusion = Image.new("L", (200, 140), 0)
    ImageDraw.Draw(exclusion).rectangle((25, 20, 30, 30), fill=255)
    included_warning, excluded_warning = "缓存边缘需要补点", "排除对象范围需要检查"
    first = bitmap_mask(alpha[0], objects[0]["name"])
    second = bitmap_mask(alpha[1], objects[1]["name"])
    editor._scene.set_precise("object-1", first, {"warnings": [included_warning], "elapsed_ms": 9876})
    editor._scene.set_precise("object-3", bitmap_mask(exclusion, "排除区域"),
                              {"warnings": [excluded_warning], "elapsed_ms": 9876})
    composed = bitmap_mask(ImageChops.subtract(ImageChops.lighter(*alpha), exclusion), "组合范围")
    before = deepcopy((editor._layers, editor._history, editor._cursor))
    pixel_selections.complete(editor, {"mask": composed, "items": [
        {"id": "object-2", "mask": second, "quality": {"warnings": [WARNING], "elapsed_ms": 1234}},
    ]}, {"purpose": "objects", "ids": ["object-1", "object-2"], "exclude": ["object-3"],
         "mode": "replace", "composed": True, "auto_apply": auto_apply})
    wait_for(lambda: settled(editor))
    for warning in (included_warning, excluded_warning, WARNING):
        assert editor.selectionQuality.count(warning) == 1
        assert warning in editor.conversation[-1]["text"] and warning in editor.status
    assert "1.2s" in editor.selectionQuality and "9.9s" not in editor.selectionQuality
    assert editor.conversation[-1]["state"] == ("applied" if auto_apply else "draft")
    if auto_apply:
        assert len(editor._layers) == len(before[0]) + 1 and editor._cursor == before[2] + 1
        assert not editor.hasSelectionDraft
        editor.undo()
        wait_for(lambda: settled(editor))
        assert editor._layers == before[0]
    else:
        assert editor.hasSelectionDraft and deepcopy((editor._layers, editor._history, editor._cursor)) == before
