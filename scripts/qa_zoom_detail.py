"""Measure actual 100% canvas detail and process memory on a 24MP photo."""

import argparse
import ctypes
import multiprocessing
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from time import monotonic

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QT_QUICK_BACKEND", "software")

import numpy as np
from PIL import Image, ImageStat
from PySide6.QtCore import QObject, QPointF, QProcess, QUrl
from PySide6.QtGui import QFontDatabase, QGuiApplication
from PySide6.QtQml import QQmlApplicationEngine
from PySide6.QtQuickControls2 import QQuickStyle
from PySide6.QtTest import QTest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from iphoto.ai_settings import SettingsStore  # noqa: E402
from iphoto.workspace import Editor  # noqa: E402


class ProcessMemory(ctypes.Structure):
    _fields_ = [
        ("cb", ctypes.c_uint32), ("page_faults", ctypes.c_uint32),
        ("peak_working_set", ctypes.c_size_t), ("working_set", ctypes.c_size_t),
        ("peak_paged_pool", ctypes.c_size_t), ("paged_pool", ctypes.c_size_t),
        ("peak_nonpaged_pool", ctypes.c_size_t), ("nonpaged_pool", ctypes.c_size_t),
        ("pagefile", ctypes.c_size_t), ("peak_pagefile", ctypes.c_size_t),
        ("private", ctypes.c_size_t),
    ]


def memory_mb(pid):
    kernel = ctypes.windll.kernel32
    kernel.OpenProcess.argtypes = [ctypes.c_uint32, ctypes.c_int, ctypes.c_uint32]
    kernel.OpenProcess.restype = ctypes.c_void_p
    kernel.CloseHandle.argtypes = [ctypes.c_void_p]
    psapi = ctypes.windll.psapi
    psapi.GetProcessMemoryInfo.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_uint32]
    handle = kernel.OpenProcess(0x0400 | 0x0010, 0, int(pid))
    if not handle:
        return None
    try:
        info = ProcessMemory()
        info.cb = ctypes.sizeof(info)
        if not psapi.GetProcessMemoryInfo(handle, ctypes.byref(info), info.cb):
            return None
        return round(info.private / 1024**2, 1), round(info.working_set / 1024**2, 1)
    finally:
        kernel.CloseHandle(handle)


def child_memory_mb(pid):
    query = ("Get-CimInstance Win32_Process -Filter 'ParentProcessId = "
             + str(int(pid)) + "' | Select-Object -ExpandProperty ProcessId")
    result = subprocess.run(["powershell", "-NoProfile", "-Command", query],
                            capture_output=True, text=True)
    children = [int(line) for line in result.stdout.splitlines() if line.strip().isdigit()]
    return [(child, memory_mb(child)) for child in children]


def wait(app, condition, seconds=30):
    end = monotonic() + seconds
    while monotonic() < end:
        app.processEvents()
        if condition():
            return
        QTest.qWait(20)
    raise AssertionError("Qt preview did not become ready")


def std_at(image, x, y, radius=80):
    patch = image.crop((x - radius, y - radius, x + radius, y + radius)).convert("L")
    return round(ImageStat.Stat(patch).stddev[0], 2)


def make_photo(photo, width=6000, height=4000):
    row = (np.arange(width, dtype=np.uint8) % 2) * 255
    pixels = np.tile(row, (height, 1))
    pixels[1::2] = 255 - pixels[1::2]
    Image.fromarray(pixels, "L").convert("RGB").save(photo)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--width", type=int, default=6000)
    parser.add_argument("--height", type=int, default=4000)
    args = parser.parse_args()
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    QQuickStyle.setStyle("Basic")
    app = QGuiApplication([])
    for name in ("msyh.ttc", "msyhbd.ttc", "segoeui.ttf"):
        font = Path("C:/Windows/Fonts") / name
        if font.exists():
            QFontDatabase.addApplicationFont(str(font))
    with tempfile.TemporaryDirectory(prefix="iphoto-zoom-detail-") as temporary:
        folder = Path(temporary)
        photo = folder / "fine-detail.png"
        width, height = args.width, args.height
        maker = multiprocessing.Process(target=make_photo, args=(photo, width, height))
        maker.start()
        maker.join()
        assert maker.exitcode == 0 and photo.exists()

        editor = Editor(ai_store=SettingsStore(folder / "settings"))
        engine = QQmlApplicationEngine()
        warnings = []
        engine.warnings.connect(lambda items: warnings.extend(i.toString() for i in items))
        engine.rootContext().setContextProperty("editor", editor)
        engine.load(QUrl.fromLocalFile(str(ROOT / "src/iphoto/ui/Main.qml")))
        assert engine.rootObjects(), warnings
        window = engine.rootObjects()[0]
        window.resize(1440, 930)
        window.setProperty("chatOpen", False)
        try:
            print(f"private_working_mb_before_open_ui={memory_mb(os.getpid())} worker={memory_mb(editor.process.processId())}")
            opened = monotonic()
            editor.openImage(str(photo))
            wait(app, lambda: editor.hasImage and window.property("previewReady")
                 and not editor._active and not editor._pending_render)
            print(f"open_s={monotonic()-opened:.2f}")
            print(f"pids_ui={os.getpid()} worker={editor.process.processId()} worker_state={editor.process.state().name}")
            print(f"private_working_mb_ui={memory_mb(os.getpid())} "
                  f"worker_children={child_memory_mb(editor.process.processId())}")
            zoomed = monotonic()
            editor.viewport.setZoom(1)
            editor.viewport.centerOn(.5, .5)
            try:
                wait(app, lambda: window.property("detailReady") and bool(editor.detailUrl), seconds=20)
            except AssertionError:
                print(f"detail_debug_url={editor.detailUrl} active={editor._detail_active is not None} "
                      f"pending={editor._detail_pending is not None} process={editor._detail_process.state().name} "
                      f"failed_generation={editor._detail_failed_generation} generation={editor._generation} "
                      f"warnings={warnings}", flush=True)
                raise
            print(f"detail_ready_s={monotonic()-zoomed:.2f} detail_rect={editor.detailRect}")
            screenshot = ROOT / "artifacts/ux-zoom-100-detail.png"
            assert window.grabWindow().save(str(screenshot))
            print(f"private_working_mb_zoom100_ui={memory_mb(os.getpid())} "
                  f"detail_children={child_memory_mb(editor._detail_process.processId())}")
            photo_item = window.findChild(QObject, "photoCanvas")
            center = photo_item.mapToScene(QPointF(photo_item.width()/2, photo_item.height()/2)).toPoint()
            with Image.open(photo) as source, Image.open(QUrl(editor.previewUrl).toLocalFile()) as preview, Image.open(screenshot) as displayed:
                print(f"source={source.size} proxy={preview.size} dpr={window.devicePixelRatio()}")
                print(f"center_std_source={std_at(source, width//2, height//2)} "
                      f"proxy={std_at(preview, preview.width//2, preview.height//2)} "
                      f"screen={std_at(displayed, center.x(), center.y())}")
                assert std_at(displayed, center.x(), center.y()) > 100
            first_detail = editor.detailUrl
            editor.viewport.pan(-700, 0)
            wait(app, lambda: window.property("detailReady") and editor.detailUrl != first_detail)
            print(f"pan_detail_rect={editor.detailRect}")
            old_detail, old_preview = editor.detailUrl, editor.previewUrl
            edited_at = monotonic()
            editor.setParameter("exposure", -1)
            editor.finishGesture()
            wait(app, lambda: editor.previewUrl != old_preview and editor.detailUrl != old_detail
                 and window.property("detailReady"))
            edited_screenshot = ROOT / "artifacts/ux-zoom-100-edited.png"
            assert window.grabWindow().save(str(edited_screenshot))
            surface = window.findChild(QObject, "canvasSurface")
            visible_center = surface.mapToScene(QPointF(surface.width()/2, surface.height()/2)).toPoint()
            with Image.open(edited_screenshot) as displayed:
                edited_std = std_at(displayed, visible_center.x(), visible_center.y())
            print(f"edited_center_std={edited_std}")
            assert 40 < edited_std < 115
            print(f"edited_detail_ready_s={monotonic()-edited_at:.2f} "
                  f"detail_children={child_memory_mb(editor._detail_process.processId())}")
            print(f"edited_detail_url_changed={editor.detailUrl != old_detail} "
                  f"preview_url_changed={editor.previewUrl != old_preview}")
            editor.viewport.fit()
            wait(app, lambda: not editor.detailUrl and editor._detail_process.state() == QProcess.NotRunning)
            print("fit_released_detail_process=true")
            assert not warnings, warnings
        finally:
            editor.close()
            window.close()


if __name__ == "__main__":
    main()
