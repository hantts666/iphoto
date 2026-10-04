"""Full QML gestures, real native AI worker and bound range transactions."""

from copy import deepcopy
import hashlib

import numpy as np
from PySide6.QtCore import QPointF, Qt
from PySide6.QtTest import QTest

from iphoto.document import raster_mask, read_project
from iphoto.matting import models
from iphoto.matting.local import stroke_mask
from test_ai import wait_for
from test_canvas_ui import canvas as shared_canvas
from test_editor import settled
from test_matting import soft_scene
from test_scene_object_actions_ui import reveal

canvas = shared_canvas


def prepare(ui,tmp_path):
    assert models.available(), "Configured native AI is required for this gate"
    image,_,mask=soft_scene(960,640)
    source=tmp_path/"soft-edge.png";image.save(source)
    ui.e.openImage(str(source));wait_for(lambda:settled(ui.e) and ui.w.property("previewReady"))
    ui.e._layer()["mask"]=deepcopy(mask);ui.e._load_layer();ui.e._commit()
    ui.e.selection.reviewMask();wait_for(lambda:settled(ui.e) and ui.w.property("selectionPreviewReady"))
    return source


def paint(ui):
    item=ui.find("photoCanvas")
    a,b=(item.mapToScene(QPointF(item.width()*x,item.height()*.49)).toPoint() for x in (.3,.4))
    ui.drag(a,b)


def test_single_repair_group_and_advanced_options_use_actual_clicks(canvas,tmp_path):
    ui=canvas;prepare(ui,tmp_path)
    assert ui.find("transparentDraftMaskButton").isVisible()
    assert not ui.find("refineMethodMenuButton").isVisible() and not ui.find("matteRadiusBox").isVisible()
    reveal(ui,"refineAdvancedButton");ui.click("refineAdvancedButton")
    assert ui.find("refineMethodMenuButton").isVisible() and ui.find("matteRadiusBox").isVisible()
    reveal(ui,"refineAdvancedButton");ui.click("refineAdvancedButton")
    assert not ui.find("refineMethodMenuButton").isVisible()
    ui.click("transparentDraftMaskButton")
    assert ui.e.selection.tool=="transparency" and ui.find("brushDiameterInput").isVisible()
    for name in ("selectionMode_replace","selectionMode_add","selectionMode_subtract"):
        assert not ui.find(name).isVisible()
    ui.find("photoCanvas").forceActiveFocus();ui.e.selection.setBrushDiameter(30)
    ui.key(Qt.Key_BracketRight);assert ui.e.selection.brushDiameter==33
    ui.key(Qt.Key_BracketLeft)
    QTest.mouseMove(ui.w,ui.point("photoCanvas"));QTest.qWait(50)
    footprint=ui.find("brushFootprint")
    assert footprint.isVisible() and footprint.width()>0


def test_real_local_stroke_preserves_outside_binding_and_saves_one_undo(canvas,tmp_path,monkeypatch):
    ui=canvas;source=prepare(ui,tmp_path)
    sha=hashlib.sha256(source.read_bytes()).hexdigest()
    ui.e.setMaskView("grayscale");wait_for(lambda:settled(ui.e))
    ui.click("transparentDraftMaskButton");ui.e.selection.setBrushDiameter(54)
    before=deepcopy((ui.e._candidate,ui.e._layers,ui.e._cursor,ui.e._selection_target_id))
    jobs=[];original=ui.e._request
    def request(op,**data):
        if op=="matte":jobs.append(deepcopy(data))
        return original(op,**data)
    monkeypatch.setattr(ui.e,"_request",request)
    paint(ui)
    wait_for(lambda:not ui.e.matteBusy and len(jobs)==1 and ui.e._candidate!=before[0] and settled(ui.e),seconds=40)
    assert jobs[0]["method"]=="neural" and jobs[0]["mask"]==before[0]
    scope=np.asarray(stroke_mask(jobs[0]["stroke"],(960,640)))>0
    old=np.asarray(raster_mask(before[0],(960,640)));new=np.asarray(raster_mask(ui.e._candidate,(960,640)))
    assert np.array_equal(old[~scope],new[~scope]) and np.any(old!=new)
    assert (ui.e._layers,ui.e._cursor,ui.e._selection_target_id)==before[1:]
    assert ui.e.selection.editingLayerMask and "局部 AI" in ui.e.selectionQuality
    changed=deepcopy(ui.e._candidate)
    ui.e.undo();wait_for(lambda:settled(ui.e));assert ui.e._candidate==before[0]
    ui.e.redo();wait_for(lambda:settled(ui.e));assert ui.e._candidate==changed
    wait_for(lambda:ui.w.property("previewReady") and ui.w.property("selectionPreviewReady")
             and ui.find("selectionToLayerButton").property("enabled"))
    ui.click("selectionToLayerButton");wait_for(lambda:not ui.e.hasSelectionDraft and settled(ui.e))
    assert len(ui.e._layers)==1 and ui.e._layer()["mask"]==changed and ui.e._cursor==before[2]+1
    project=tmp_path/"local.iphoto";ui.e.saveProject(str(project));wait_for(lambda:not ui.e.savingProject)
    assert read_project(project)["layers"]==ui.e._layers
    ui.e.undo();wait_for(lambda:settled(ui.e));assert ui.e._layers==before[1]
    assert hashlib.sha256(source.read_bytes()).hexdigest()==sha


def test_cancel_actual_painted_task_keeps_range_then_can_retry(canvas,tmp_path):
    ui=canvas;prepare(ui,tmp_path)
    ui.click("transparentDraftMaskButton");ui.e.selection.setBrushDiameter(54)
    before=deepcopy((ui.e._candidate,ui.e._layers,ui.e._cursor,ui.e._draft_history,ui.e._generation))
    paint(ui);wait_for(lambda:ui.e.matteBusy and ui.e._matte_active is not None)
    ui.click("cancelAiRequest");wait_for(lambda:not ui.e.matteBusy and not ui.e._matte_aborting)
    assert before==(ui.e._candidate,ui.e._layers,ui.e._cursor,ui.e._draft_history,ui.e._generation)
    paint(ui);wait_for(lambda:not ui.e.matteBusy and ui.e._candidate!=before[0] and settled(ui.e),seconds=40)
    assert ui.e._layers==before[1] and ui.e._selection_target_id==ui.e.activeLayerId
