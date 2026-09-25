import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QT_QUICK_BACKEND", "software")

import time
from pathlib import Path
import numpy as np
from PIL import Image
import pytest
from PySide6.QtCore import QUrl
from PySide6.QtGui import QGuiApplication
from PySide6.QtTest import QTest

from iphoto.app import Editor
from iphoto.engine import Recipe, render


def wait_for(test, timeout=15000):
    end = time.monotonic() + timeout / 1000
    while time.monotonic() < end:
        QGuiApplication.processEvents()
        if test(): return
        QTest.qWait(20)
    raise AssertionError("Qt/worker operation timed out")


def settled(editor):
    return editor._active is None and not editor._pending_render and not editor._queue and not editor._timer.isActive()


def test_real_editor_history_latest_render_project_and_export(tmp_path, qt_app, ai_store):
    app = QGuiApplication.instance() or QGuiApplication([])
    path = tmp_path / "image.png"
    Image.new("RGB", (320, 200), (75, 110, 120)).save(path)
    editor = Editor(ai_store=ai_store)
    editor.ai.save("qwen", "", "", "", False, False)
    try:
        editor.openImage(str(path))
        wait_for(lambda: editor.hasImage and settled(editor))
        assert editor.parameters == Recipe().to_dict()
        for exposure in [.1, .3, -.2, .55]:
            editor.setParameter("exposure", exposure)
        editor.finishGesture()
        wait_for(lambda: settled(editor))
        shown = Image.open(QUrl(editor.previewUrl).toLocalFile())
        expected = render(Image.open(path), Recipe(exposure=.55))
        assert np.array_equal(np.asarray(shown), np.asarray(expected))
        editor.undo()
        wait_for(lambda: settled(editor))
        assert editor.parameters["exposure"] == 0
        editor.redo()
        wait_for(lambda: settled(editor))
        assert editor.parameters["exposure"] == .55
        editor.applyDescription("提亮暗部，色调暖一点")
        wait_for(lambda: settled(editor))
        assert editor.parameters["exposure"] == .55  # manual field remains locked
        assert editor.parameters["shadows"] > 0
        saved = dict(editor.parameters)
        project = tmp_path / "session.json"
        editor.saveProject(str(project))
        editor.reset()
        wait_for(lambda: settled(editor))
        editor.openProject(str(project))
        wait_for(lambda: settled(editor))
        assert editor.parameters == saved
        exported = tmp_path / "edited.png"
        editor.exportImage(str(exported))
        wait_for(lambda: settled(editor))
        assert exported.exists()
        with Image.open(exported) as output:
            assert output.size == (320, 200)
    finally:
        editor.close()
