"""Measure drawing or switching photos while element previews build on 24MP."""

import argparse
import os
import sys
import tempfile
from pathlib import Path
from time import monotonic

from PIL import Image, ImageDraw
from PySide6.QtGui import QGuiApplication
from PySide6.QtTest import QTest

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "src"), str(ROOT / "tests")]

from iphoto.ai_settings import AISettings, SettingsStore  # noqa: E402
from iphoto.controllers import pixel_selections  # noqa: E402
from iphoto.workspace import Editor  # noqa: E402
from test_v14 import catalog  # noqa: E402


def wait(app, condition, seconds=60):
    end = monotonic() + seconds
    while monotonic() < end:
        app.processEvents()
        if condition():
            return True
        QTest.qWait(20)
    return False


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--open-during-background", action="store_true")
    parser.add_argument("--pixel-during-background", action="store_true")
    parser.add_argument("--export-during-background", action="store_true")
    args = parser.parse_args()
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    app = QGuiApplication([])
    with tempfile.TemporaryDirectory(prefix="iphoto-priority-") as folder:
        previous_cache_root = os.environ.get("LOCALAPPDATA")
        os.environ["LOCALAPPDATA"] = str(Path(folder) / "app-data")
        photo = Path(folder) / "large.jpg"
        image = Image.new("RGB", (6000, 4000), "#536b85")
        draw = ImageDraw.Draw(image)
        draw.rectangle((600, 500, 2400, 3000), fill="#cb784f")
        draw.ellipse((3300, 600, 5400, 3200), fill="#83a55a")
        image.save(photo, quality=88)
        del image

        settings = SettingsStore(Path(folder) / "settings")
        settings.save(AISettings(enabled=False), "")
        editor = Editor(ai_store=settings)
        try:
            opened = monotonic()
            editor.openImage(str(photo))
            assert wait(app, lambda: editor.hasImage and not editor._active
                        and not editor._pending_render and not editor._timer.isActive())
            print(f"open_24mp_s={monotonic() - opened:.2f}", flush=True)

            editor._scene.set(catalog())
            ids = [row["id"] for row in editor.sceneObjects]
            started = monotonic()
            pixel_selections.precache(editor, ids)
            assert editor._pixel_active or editor._pixel_queue, "EfficientSAM model is unavailable"
            if args.open_during_background:
                assert wait(app, lambda: editor._pixel_active is not None)
                QTest.qWait(600)
                other = Path(folder) / "other.jpg"
                Image.new("RGB", (2400, 1600), "#ae6d50").save(other, quality=88)
                switched = monotonic()
                editor.openImage(str(other))
                assert wait(app, lambda: editor.imageName == other.name and not editor._active)
                print(f"switch_during_background_s={monotonic() - switched:.2f}; "
                      f"old_tasks={len(editor._pixel_queue)}; scene_rows={len(editor.sceneObjects)}", flush=True)
                assert not editor._pixel_active and not editor._pixel_queue
                assert not editor.sceneObjects
                return
            if args.pixel_during_background:
                assert wait(app, lambda: editor._pixel_active is not None)
                QTest.qWait(600)
                clicked = monotonic()
                pixel_selections.point(editor, [0.2, 0.4], True)
                assert wait(app, lambda: editor._pixel_active is not None
                            and editor._pixel_active.get("priority") != "low")
                assert wait(app, lambda: editor.hasSelectionDraft), editor.status
                print(f"pixel_preempt_s={monotonic() - clicked:.2f}; "
                      f"remaining_background={len(editor._pixel_queue)}", flush=True)
                assert wait(app, lambda: not editor._pixel_active and not editor._pixel_queue)
                assert len(editor._scene.precise) == 2
                return
            if args.export_during_background:
                target = Path(folder) / "export.jpg"
                exported = monotonic()
                assert editor.exportImage(str(target))
                assert wait(app, lambda: target.exists() and not editor._active), editor.status
                export_status = editor.status
                assert export_status.startswith("已按原图尺寸导出")
                print(f"export_during_background_s={monotonic() - exported:.2f}; "
                      f"status={export_status}; background={bool(editor._pixel_active or editor._pixel_queue)}", flush=True)
                assert wait(app, lambda: not editor._pixel_active and not editor._pixel_queue)
                print(f"status_after_background={editor.status}", flush=True)
                assert editor.status == export_status, "后台轮廓覆盖了导出成功信息"
                return
            editor.beginSelection("empty")
            editor.drawDraft("rect", "replace", [[.1, .1], [.3, .3]], .02)
            assert wait(app, lambda: bool(editor.maskUrl)), editor.status
            print(f"draft_preview_s={monotonic() - started:.2f}; "
                  f"precise_ready_at_preview={len(editor._scene.precise)}/{len(ids)}", flush=True)
            assert wait(app, lambda: not editor._pixel_active and not editor._pixel_queue
                        and not editor._active and not editor._queue
                        and not editor._pending_render), editor.status
            print(f"background_done_s={monotonic() - started:.2f}; "
                  f"precise_ready={len(editor._scene.precise)}/{len(ids)}; "
                  f"row_states={[row['pixelStatus'] for row in editor.sceneObjects]}; "
                  f"status={editor.status}", flush=True)
        finally:
            editor.close()
            if previous_cache_root is None:
                os.environ.pop("LOCALAPPDATA", None)
            else:
                os.environ["LOCALAPPDATA"] = previous_cache_root


if __name__ == "__main__":
    main()
