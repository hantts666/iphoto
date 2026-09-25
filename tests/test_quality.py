"""Regression cases discovered during the autonomous quality iteration."""
from copy import deepcopy
from pathlib import Path
import json

import pytest
from PIL import Image

from iphoto.app import Editor
from iphoto.document import empty_mask, read_project, validate_project
from test_ai import wait_for
from test_editor import settled


@pytest.fixture
def workspace(tmp_path, qt_app, ai_store):
    path = tmp_path / "source.png"
    Image.new("RGB", (300,200), (55,90,130)).save(path)
    editor = Editor(ai_store=ai_store)
    editor.openImage(str(path))
    wait_for(lambda: editor.hasImage and settled(editor))
    try: yield editor
    finally: editor.close()


def test_layer_selection_does_not_destroy_redo(workspace):
    e = workspace
    original = e.activeLayerId
    e.addLayer()
    e.selectionAction("all")
    e.setParameter("exposure",.5); e.finishGesture()
    e.undo()
    assert e.canRedo
    e.selectLayer(original)
    e.finishGesture()  # QML focus/slider release must not create an edit for selection alone.
    assert e.canRedo
    e.redo()
    assert e.parameters["exposure"] == .5


def test_failed_open_preserves_selection_draft(workspace, tmp_path):
    e = workspace
    draft = empty_mask(True)
    draft["label"] = "未确认的天空"
    e._candidate = deepcopy(draft)
    e._mark_dirty()
    e.openImage(str(tmp_path / "missing.png"))
    wait_for(lambda: settled(e))
    assert e.hasSelectionDraft and e._candidate == draft


def test_autosave_failure_blocks_switch(workspace, tmp_path, monkeypatch):
    e = workspace
    e.setParameter("exposure",.6); e.finishGesture()
    before = e._path
    target = tmp_path / "another.png"
    Image.new("RGB",(300,200),"red").save(target)
    def fail(*args, **kwargs): raise OSError("simulated full disk")
    monkeypatch.setattr("iphoto.controllers.session.write_project",fail)
    e.openImage(str(target)); wait_for(lambda: settled(e))
    assert e._path == before and e.parameters["exposure"] == .6
    assert "保存" in e.status


def test_corrupt_latest_recovery_does_not_hide_valid_session(workspace):
    e = workspace
    e.setParameter("exposure",.7); e.finishGesture(); e._save_recovery()
    original = e._recovery_path
    corrupt = e._recovery_dir / "broken.iphoto"
    corrupt.write_text("{broken", encoding="utf-8")
    import os
    os.utime(corrupt,(original.stat().st_mtime+20,)*2)
    e._recovery_path = e._recovery_dir / "next-session.iphoto"
    e._dirty = False
    e._recipe["exposure"] = 0
    e.recoverLatest(); wait_for(lambda: settled(e))
    assert e.parameters["exposure"] == .7


def test_malformed_project_is_reported_without_exception(workspace, tmp_path):
    data = deepcopy(workspace._payload())
    data["layers"][0]["locked"] = [["exposure"]]
    path = tmp_path / "malformed.iphoto"
    path.write_text(json.dumps(data),encoding="utf-8")
    before = deepcopy(workspace._layers)
    workspace.openProject(str(path))
    assert workspace._layers == before
    assert "打开项目失败" in workspace.status


def test_close_protection_and_recovery_retry(workspace, monkeypatch):
    e = workspace
    e.setParameter("exposure", .4); e.finishGesture()
    failures = []
    e.recoverySaveFailed.connect(lambda: failures.append(True))
    import iphoto.controllers.session as module
    original = module.write_project
    def fail(*args, **kwargs): raise OSError("disk full")
    monkeypatch.setattr(module,"write_project",fail)
    assert not e.prepareClose()
    assert failures and e.hasImage and e.dirty
    monkeypatch.setattr(module,"write_project",original)
    assert e.prepareClose()
    assert read_project(e._recovery_path)["layers"][0]["recipe"]["exposure"] == .4


def test_export_is_not_published_until_encoding_finishes(tmp_path, monkeypatch):
    from iphoto.engine import load_source, export_image, Recipe
    path = tmp_path / "original.png"
    Image.new("RGB",(30,20),"red").save(path)
    source = load_source(path)
    target = tmp_path / "final.png"
    original = Image.Image.save
    def fail_save(image, output, **kwargs):
        output.write(b"incomplete image")
        assert not target.exists(), "A partial export must not appear under its final filename"
        raise OSError("encoder failure")
    monkeypatch.setattr(Image.Image,"save",fail_save)
    with pytest.raises(OSError): export_image(source,Recipe(),target)
    assert not target.exists()
    assert not list(tmp_path.glob("*.tmp"))
    monkeypatch.setattr(Image.Image,"save",original)
    export_image(source,Recipe(),target)
    with Image.open(target) as exported: assert exported.size == (30,20)


def test_source_hash_failure_keeps_current_worker_photo(workspace, tmp_path):
    e = workspace
    current = e._path
    other = tmp_path / "other.png"
    Image.new("RGB",(70,40),"red").save(other)
    payload = deepcopy(e._payload())
    payload["source"] = str(other)  # deliberately retains a mismatching hash
    project = tmp_path / "changed.iphoto"
    project.write_text(json.dumps(payload),encoding="utf-8")
    e.setParameter("exposure",.3); e.finishGesture()
    e.openProject(str(project)); wait_for(lambda: settled(e))
    assert e._path == current and e.parameters["exposure"] == .3
    assert "源图片已变化" in e.status
    exported = tmp_path / "after-failure.png"
    e.exportImage(str(exported)); wait_for(lambda: settled(e))
    with Image.open(exported) as image: assert image.size == (300,200)


def test_old_advice_does_not_overwrite_changed_selection(workspace):
    from test_ai import configure, mock_api
    e = workspace
    with mock_api() as (url,_):
        configure(e.ai,url)
        e.sendMessage("先给我建议", "advice")
        wait_for(lambda: not e.busy)
    suggestion = e.conversation[-1]["id"]
    e.selectionAction("invert")
    e.drawSelection("rect","add",[[.1,.1],[.5,.5]],.02)
    before = deepcopy(e._layers)
    e.applyAdvice(suggestion)
    assert e._layers == before
    assert e.conversation[-1]["state"] == "stale"
    assert "重新获取建议" in e.status


def test_selection_navigation_reuses_composite_but_not_mask(workspace):
    e = workspace
    e.setParameter("exposure", .3); e.finishGesture()
    wait_for(lambda: settled(e))
    original_layer = e.activeLayerId
    composite = e.previewUrl
    e.addLayer(); wait_for(lambda: settled(e))
    assert e.previewUrl == composite
    urls = set()
    for i in range(10):
        e.drawSelection("ellipse","add",[[.05*i,.1],[.05*i+.1,.5]],.02)
        wait_for(lambda: settled(e))
        urls.add(e.maskUrl)
        assert e.previewUrl == composite
    assert len(urls) == 10
    from PySide6.QtCore import QUrl
    assert Path(QUrl(composite).toLocalFile()).exists()  # cache cleanup must retain live preview
    e.selectLayer(original_layer); wait_for(lambda: settled(e))
    assert e.previewUrl == composite
    e.setParameter("exposure", .6); e.finishGesture()
    wait_for(lambda: settled(e))
    assert e.previewUrl != composite


@pytest.mark.parametrize("values", [
    {"tint":35,"saturation":35,"vibrance":25,"contrast":15,"whites":10,"blacks":-10},
    {"tint":-60,"saturation":60,"vibrance":60},
    {"tint":60,"exposure":1,"contrast":50,"whites":60,"blacks":-60},
    {"vibrance":60,"saturation":60},
])
def test_combined_color_tools_match_float_reference(values):
    import numpy as np
    from iphoto.engine import Recipe, render, render_reference
    rng = np.random.default_rng(42)
    image = Image.fromarray(rng.integers(0,256,(256,256,3),dtype=np.uint8))
    recipe = Recipe.from_dict(values)
    delta = np.abs(np.asarray(render(image,recipe),dtype=np.int16)-np.asarray(render_reference(image,recipe),dtype=np.int16))
    assert delta.max() <= 3
    assert np.percentile(delta,99.9) <= 1


def test_worker_exit_preserves_save_and_does_not_leave_busy(workspace, tmp_path):
    from PySide6.QtCore import QProcess
    e = workspace
    e.setParameter("exposure",.5); e.finishGesture()
    wait_for(lambda: settled(e))
    e.process.kill()
    wait_for(lambda: e.process.state() == QProcess.NotRunning and not e.busy)
    e.exportImage(str(tmp_path / "unavailable.png"))
    assert not e.busy and not e._queue
    project = tmp_path / "safe.iphoto"
    e.saveProject(str(project))
    assert read_project(project)["layers"][0]["recipe"]["exposure"] == .5


def test_parameter_drags_do_not_rebuild_layer_sidebar(workspace):
    changes = []
    workspace.layersChanged.connect(lambda: changes.append(True))
    for exposure in (.1,.2,.3,.4): workspace.setParameter("exposure", exposure)
    workspace.finishGesture()
    wait_for(lambda: settled(workspace))
    assert not changes
    workspace.renameLayer("重命名")
    assert changes and workspace.layers[0]["name"] == "重命名"
