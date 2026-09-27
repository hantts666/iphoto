"""Compare scene-result latency with and without local encode during AI wait."""

import os
import sys
import tempfile
from pathlib import Path
from time import monotonic

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QT_QUICK_BACKEND", "software")

from PIL import Image, ImageDraw
from PySide6.QtCore import QProcess
from PySide6.QtGui import QGuiApplication
from PySide6.QtTest import QTest

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "src"), str(ROOT / "tests")]

from iphoto.ai_settings import SettingsStore  # noqa: E402
from iphoto.controllers import pixel_selections  # noqa: E402
from iphoto.workspace import Editor  # noqa: E402
from test_ai import configure, mock_api  # noqa: E402
from test_v14 import scene_response  # noqa: E402


def wait(app, condition, seconds=60):
    until = monotonic() + seconds
    while monotonic() < until:
        app.processEvents()
        if condition():
            return
        QTest.qWait(20)
    raise AssertionError("UI/worker wait timed out")


def run_case(app, folder, name, *, preheat):
    photo = folder / f"{name}.jpg"
    image = Image.new("RGB", (4000, 3000), "#536b85")
    draw = ImageDraw.Draw(image)
    draw.rectangle((350, 350, 1700, 2450), fill="#c2774f" if preheat else "#c6784f")
    draw.ellipse((2200, 450, 3600, 2400), fill="#83a55a")
    image.save(photo, quality=88)
    del image

    editor = Editor(ai_store=SettingsStore(folder / f"{name}-settings"))
    original_warm = pixel_selections.warm
    try:
        editor.openImage(str(photo))
        wait(
            app,
            lambda: editor.hasImage
            and not editor._active
            and not editor._queue
            and not editor._pending_render
            and not editor._timer.isActive(),
        )
        if not preheat:
            pixel_selections.warm = lambda _: None
        with mock_api(scene_response(), delay=6) as (url, _):
            configure(editor.ai, url)
            started = monotonic()
            editor.analyzeScene(True)
            if preheat:
                wait(
                    app,
                    lambda: editor.ai.busy
                    and editor._warm_process.state() == QProcess.NotRunning
                    and editor._warm_ready_sha == editor._sha,
                )
                print(f"{name}_preheat_done_s={monotonic() - started:.2f}", flush=True)
                assert editor.status.startswith("AI 正在建立画面元素清单"), editor.status
            wait(app, lambda: len(editor.sceneObjects) == 3)
            scene_at = monotonic()
            assert editor.status == "元素清单已建立，正在后台准备轮廓；可先继续操作", editor.status
            wait(app, lambda: not editor._active and not editor._queue
                 and not editor._pixel_active and not editor._pixel_queue
                 and not editor.ai.busy)
            assert editor.status.startswith("元素轮廓 "), editor.status
            disk_loads = sum(
                bool(value["quality"].get("embedding_disk"))
                for value in editor._scene.precise.values()
            )
            if preheat:
                assert disk_loads >= 1
            else:
                assert disk_loads == 0
            print(
                f"{name}_scene_return_s={scene_at - started:.2f} "
                f"post_scene_contours_s={monotonic() - scene_at:.2f} "
                f"ready={len(editor._scene.precise)}/3 disk_loads={disk_loads}",
                flush=True,
            )
    finally:
        pixel_selections.warm = original_warm
        editor.close()


def main():
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    app = QGuiApplication([])
    with tempfile.TemporaryDirectory(prefix="iphoto-scene-preheat-") as temporary:
        folder = Path(temporary)
        previous_cache_root = os.environ.get("LOCALAPPDATA")
        os.environ["LOCALAPPDATA"] = str(folder / "app-data")
        try:
            run_case(app, folder, "baseline", preheat=False)
            run_case(app, folder, "preheat", preheat=True)
        finally:
            if previous_cache_root is None:
                os.environ.pop("LOCALAPPDATA", None)
            else:
                os.environ["LOCALAPPDATA"] = previous_cache_root


if __name__ == "__main__":
    main()
