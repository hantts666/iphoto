"""Local auto balance: deterministic tone/color targets from worker stats."""

import pytest
from PIL import Image

from iphoto.controllers import auto_adjust
from iphoto.workspace import Editor
from test_ai import wait_for
from test_editor import settled


def test_dark_image_gains_exposure():
    changes = auto_adjust.compute(
        {"means": [0.2, 0.2, 0.2], "low": 0.02, "mid": 0.18, "high": 0.6}, {}
    )
    assert changes["exposure"] > 0.3
    assert changes.get("shadows", 0) >= 24


def test_bright_image_loses_exposure_and_highlights():
    changes = auto_adjust.compute(
        {"means": [0.8, 0.8, 0.8], "low": 0.4, "mid": 0.85, "high": 0.99}, {}
    )
    assert changes["exposure"] < -0.3
    assert changes.get("highlights", 0) <= -24


def test_flat_image_gains_contrast():
    changes = auto_adjust.compute(
        {"means": [0.5, 0.5, 0.5], "low": 0.35, "mid": 0.5, "high": 0.65}, {}
    )
    assert changes["contrast"] > 10


def test_color_casts_are_neutralized():
    blue = auto_adjust.compute(
        {"means": [0.3, 0.3, 0.5], "low": 0.05, "mid": 0.5, "high": 0.95}, {}
    )
    assert blue["warmth"] > 5
    green = auto_adjust.compute(
        {"means": [0.3, 0.6, 0.3], "low": 0.05, "mid": 0.5, "high": 0.95}, {}
    )
    assert green["tint"] > 3


def test_balanced_image_is_untouched():
    assert (
        auto_adjust.compute(
            {"means": [0.5, 0.5, 0.5], "low": 0.04, "mid": 0.52, "high": 0.96}, {}
        )
        == {}
    )


def test_locked_fields_and_ranges_are_respected():
    stats = {"means": [0.2, 0.2, 0.2], "low": 0.02, "mid": 0.18, "high": 0.6}
    assert "exposure" not in auto_adjust.compute(stats, {}, locked=("exposure",))
    changes = auto_adjust.compute(stats, {"exposure": 1.9})
    assert changes["exposure"] <= 2.0


def test_missing_stats_raise():
    with pytest.raises(ValueError):
        auto_adjust.compute(None, {})


@pytest.fixture
def workspace(tmp_path, qt_app, ai_store):
    path = tmp_path / "dark.png"
    Image.new("RGB", (300, 200), (26, 30, 34)).save(path)
    editor = Editor(ai_store=ai_store)
    editor.openImage(str(path))
    wait_for(lambda: editor.hasImage and settled(editor))
    try:
        yield editor
    finally:
        editor.close()


def test_auto_adjust_slot_commits_and_undoes(workspace):
    e = workspace
    wait_for(lambda: e._stats is not None)
    assert e._stats["mid"] < 0.3
    e.autoAdjust()
    wait_for(lambda: settled(e))
    assert e.parameters["exposure"] > 0
    e.undo()
    wait_for(lambda: settled(e))
    assert e.parameters["exposure"] == 0


def test_auto_adjust_on_group_is_refused(workspace):
    e = workspace
    e.groupLayer()
    before = dict(e._recipe)
    e.autoAdjust()
    assert e._recipe == before
