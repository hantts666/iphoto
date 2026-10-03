"""Step guidance ends with its operation; independent notices remain visible."""

from copy import deepcopy

import pytest
from PySide6.QtTest import QTest

from iphoto.document import new_layer
from test_ai import wait_for
from test_canvas_ui import canvas  # noqa: F401
from test_editor import settled
from test_selection_controller import editor  # noqa: F401


def test_real_range_output_replaces_guidance_with_current_operation(canvas):  # noqa: F811
    ui = canvas
    ui.click("tool_ellipse")
    ui.drag(ui.point("photoCanvas", .3, .25), ui.point("photoCanvas", .7, .75))
    wait_for(lambda: settled(ui.e) and ui.find("selectionToLayerButton").property("enabled"))
    toast = ui.find("workspaceToast")
    assert toast.isVisible() and "选好范围" in toast.property("message")
    ui.click("selectionToLayerButton")
    wait_for(lambda: settled(ui.e) and not ui.e.hasSelectionDraft)
    assert len(ui.e._layers) == 2 and ui.e.selection.pickedLayerId == ui.e.activeLayerId
    assert toast.isVisible() and "已开始局部调整" in toast.property("message")
    assert "选好范围" not in ui.find("workspaceStatusText").property("text")
    assert ui.e._cursor == 1
    ui.e.undo()
    wait_for(lambda: settled(ui.e))
    assert len(ui.e._layers) == 1


@pytest.mark.parametrize("save", [False, True])
def test_real_mask_review_end_keeps_its_completion_notice(canvas, save):  # noqa: F811
    ui = canvas
    original = deepcopy(ui.e._layer())
    ui.click("layerMaskThumb_" + ui.e.activeLayerId)
    wait_for(lambda: settled(ui.e) and ui.e.hasSelectionDraft)
    toast = ui.find("workspaceToast")
    assert toast.isVisible() and "正在修正" in toast.property("message")
    if save:
        ui.e.drawDraft("rect", "subtract", [[.05, .05], [.2, .2]], 0)
        wait_for(lambda: settled(ui.e) and ui.find("selectionToLayerButton").property("enabled"))
        ui.click("selectionToLayerButton")
    else:
        ui.click("discardSelectionButton")
    wait_for(lambda: settled(ui.e) and not ui.e.hasSelectionDraft)
    assert toast.isVisible()
    assert ("已保存此层" if save else "已放弃草稿") in toast.property("message")
    assert "正在修正" not in ui.find("workspaceStatusText").property("text")
    assert len(ui.e._layers) == 1
    if save:
        ui.e.undo()
        wait_for(lambda: settled(ui.e))
    assert ui.e._layer() == original


@pytest.mark.parametrize("error", [False, True])
def test_draft_events_do_not_dismiss_unrelated_success_or_error(canvas, error):  # noqa: F811
    ui = canvas
    ui.e.beginSelection("empty")
    wait_for(lambda: settled(ui.e))
    # Controlled delivery isolates view lifetime from the provider or disk.
    message = "项目保存失败：目标不可写" if error else "项目已保存"
    ui.e._notify(message, error, scope="draft" if error else "")
    toast = ui.find("workspaceToast")
    assert toast.isVisible() and toast.property("message") == message
    before = deepcopy((ui.e._layers, ui.e._candidate, ui.e._generation, ui.e._cursor, ui.e._serial))
    # A completion can arrive after a newer independent notification.
    ui.e.selection.draftEnded.emit()
    ui.e.selection.resultApplied.emit()
    QTest.qWait(40)
    assert toast.isVisible() and toast.property("message") == message
    assert bool(toast.property("isError")) is error
    assert before == (ui.e._layers, ui.e._candidate, ui.e._generation, ui.e._cursor, ui.e._serial)


def test_synchronous_auto_output_cannot_publish_its_late_initial_guidance(editor):  # noqa: F811
    notices = []
    editor.notification.connect(lambda message, error: notices.append((message, error)))
    editor.selection._pending_cutout = True
    editor.beginSelection("current")
    wait_for(lambda: settled(editor))
    assert not editor.hasSelectionDraft and len(editor._layers) == 2
    assert "已开始局部调整" in editor.status
    assert not any("正在修正" in message for message, _ in notices)
    before = (editor.status, editor._status_epoch, editor._generation, editor._serial, editor._cursor, len(notices))
    editor._notify("晚到的选区指导", scope="draft")
    assert before == (editor.status, editor._status_epoch, editor._generation, editor._serial, editor._cursor, len(notices))


@pytest.mark.parametrize("apply", [False, True])
def test_region_end_updates_status_without_replaying_draft_guidance(editor, apply):  # noqa: F811
    region = new_layer("脸部")
    region["mask"]["ops"] = [{"kind": "rect", "mode": "add", "points": [[.2, .2], [.8, .8]]}]
    original = deepcopy(editor._layers)
    editor._region_candidate = {"summary": "分区预览", "layers": [region]}
    editor.changed.emit()
    editor._notify("检查分区范围后开始调整", scope="draft")
    if apply:
        editor.selection.applyRegions()
    else:
        editor.selection.discard()
    wait_for(lambda: settled(editor))
    assert not editor.hasRegionDraft
    assert ("已开始 1 个分区调整" if apply else "已取消分区方案") in editor.status
    assert editor.notificationScope == ""
    if apply:
        editor.undo()
        wait_for(lambda: settled(editor))
    assert editor._layers == original


def test_inpaint_output_reports_completion_instead_of_range_instructions(editor):  # noqa: F811
    original = deepcopy(editor._layers)
    editor.drawDraft("rect", "replace", [[.2, .2], [.8, .8]], 0)
    wait_for(lambda: settled(editor))
    editor.selection.apply("inpaint")
    wait_for(lambda: settled(editor))
    assert not editor.hasSelectionDraft and len(editor._layers) == 2
    assert "已创建内容感知填充层" in editor.status and editor.notificationScope == ""
    editor.undo()
    wait_for(lambda: settled(editor))
    assert editor._layers == original
