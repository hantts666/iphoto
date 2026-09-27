"""One-click subject cutout: u2net draft chained straight into a new layer."""

import pytest
from PIL import Image

from iphoto.document import empty_mask
from iphoto.workspace import Editor
from test_ai import wait_for
from test_editor import settled


@pytest.fixture
def workspace(tmp_path, qt_app, ai_store, monkeypatch):
    path = tmp_path / "source.png"
    Image.new("RGB", (300, 200), (55, 90, 130)).save(path)
    editor = Editor(ai_store=ai_store)
    editor.openImage(str(path))
    wait_for(lambda: editor.hasImage and settled(editor))

    original = Editor._request
    subject = empty_mask(True)
    subject["label"] = "主体"

    def request(self, op, **data):
        if op != "selection":
            return original(self, op, **data)
        self._set_candidate(subject)
        self.changed.emit()

    monkeypatch.setattr(Editor, "_request", request)
    try:
        yield editor
    finally:
        editor.close()


def test_cutout_creates_layer_without_leaving_draft(workspace):
    e = workspace
    e.selection.cutoutSubject()
    wait_for(lambda: settled(e))
    assert len(e.layers) == 2
    assert not e.hasSelectionDraft
    assert e._layers[-1]["mask"]["label"] == "主体"
    assert e._layers[-1]["name"].startswith("选区调整")


def test_cutout_refused_with_existing_draft(workspace):
    e = workspace
    e.beginSelection("empty")
    e.selection.cutoutSubject()
    wait_for(lambda: settled(e))
    assert len(e.layers) == 1 and e.hasSelectionDraft
    assert "先输出或取消" in e.status


def test_cutout_refused_without_model(workspace):
    e = workspace
    e._capabilities = [c for c in e._capabilities if c["id"] != "u2net"]
    e.selection.cutoutSubject()
    wait_for(lambda: settled(e))
    assert len(e.layers) == 1
    assert "主体模型未配置" in e.status


def test_pending_cutout_cleared_after_completion(workspace):
    e = workspace
    e.selection.cutoutSubject()
    wait_for(lambda: settled(e))
    assert e.selection._pending_cutout is False
