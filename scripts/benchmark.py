"""A reproducible 24MP CPU export benchmark; no network or user photos."""
from pathlib import Path
import sys
import time
import json
import tempfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from PIL import Image
from iphoto.engine import Recipe, PRESETS, load_source, export_image, preview, render

with tempfile.TemporaryDirectory(prefix="iphoto-benchmark-") as directory:
    source_path = Path(directory) / "24mp.jpg"
    with Image.open(ROOT / "assets/lake.jpg") as image:
        image.resize((6000, 4000)).save(source_path, quality=95)
    started = time.perf_counter()
    source = load_source(source_path)
    load_seconds = time.perf_counter() - started
    proxy = preview(source.image)
    recipe = Recipe.from_dict(PRESETS["natural"])
    preview_times = []
    for _ in range(5):
        started = time.perf_counter()
        output = render(proxy, recipe)
        preview_times.append(round((time.perf_counter() - started) * 1000))
    started = time.perf_counter()
    export_image(source, recipe, Path(directory) / "export.jpg")
    report = {"input": "6000x4000 resized sample, JPEG", "load_seconds": round(load_seconds, 3),
              "preview_render_ms": preview_times, "export_seconds": round(time.perf_counter() - started, 3),
              "preview_scope": "1600px; render only, excludes PNG encoding, IPC and display"}
    if sys.platform == "win32":
        import ctypes
        from ctypes import wintypes
        class Memory(ctypes.Structure):
            _fields_ = [("cb", wintypes.DWORD), ("PageFaultCount", wintypes.DWORD)] + [(n, ctypes.c_size_t) for n in
                ("PeakWorkingSetSize", "WorkingSetSize", "QuotaPeakPagedPoolUsage", "QuotaPagedPoolUsage",
                 "QuotaPeakNonPagedPoolUsage", "QuotaNonPagedPoolUsage", "PagefileUsage", "PeakPagefileUsage")]
        memory = Memory(); memory.cb = ctypes.sizeof(memory)
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.GetCurrentProcess.restype = wintypes.HANDLE
        psapi = ctypes.WinDLL("psapi", use_last_error=True)
        psapi.GetProcessMemoryInfo.argtypes = [wintypes.HANDLE, ctypes.POINTER(Memory), wintypes.DWORD]
        if psapi.GetProcessMemoryInfo(kernel.GetCurrentProcess(), ctypes.byref(memory), memory.cb):
            report["benchmark_process_peak_working_set_mib"] = round(memory.PeakWorkingSetSize / 1024 ** 2, 1)
    (ROOT / "artifacts/benchmark.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report, indent=2))
