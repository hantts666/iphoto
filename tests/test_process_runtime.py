"""Worker startup policy and real image output across process boundaries."""

import json
import os
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace

import numpy as np
from PIL import Image
import pytest

from iphoto.document import new_layer, render_detail_tile, render_layers
from iphoto.engine import SRGB_PROFILE, histogram, load_source, preview, stats
from iphoto.process_runtime import BLAS_THREAD_SETTINGS, prepare_worker
from iphoto import process_runtime

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize("flag", ["--worker", "--detail-worker", "--export-worker"])
def test_photo_workers_get_default_without_changing_other_settings(flag):
    environment = {"PATH": "unchanged", "IPHOTO_TEST": "kept"}
    assert prepare_worker(["run.py", flag], environment) in {
        "iphoto.worker", "iphoto.detail_worker", "iphoto.export_worker",
    }
    assert environment == {"PATH": "unchanged", "IPHOTO_TEST": "kept", "OPENBLAS_NUM_THREADS": "1"}


@pytest.mark.parametrize("setting", BLAS_THREAD_SETTINGS)
@pytest.mark.parametrize("value", ["", "2"])
@pytest.mark.parametrize("flag", ["--worker", "--detail-worker", "--export-worker"])
def test_explicit_thread_settings_are_preserved(flag, setting, value):
    environment = {setting: value}
    prepare_worker([flag], environment)
    assert environment == {setting: value}


@pytest.mark.parametrize("arguments,module", [
    ([], None),
    (["--warm"], "iphoto.segmentation.warm_worker"),
    (["--pixel-worker"], "iphoto.segmentation.pixel_worker"),
    (["--matte-worker"], "iphoto.matting.worker"),
    (["--warm", "--detail-worker"], "iphoto.segmentation.warm_worker"),
    (["--pixel-worker", "--export-worker"], "iphoto.segmentation.pixel_worker"),
    (["--matte-worker", "--export-worker"], "iphoto.matting.worker"),
    (["--export-worker", "--worker"], "iphoto.worker"),
])
def test_policy_follows_actual_dispatch_priority(arguments, module):
    environment = {}
    assert prepare_worker(arguments, environment) == module
    assert environment == ({"OPENBLAS_NUM_THREADS": "1"} if module == "iphoto.worker" else {})


def test_bootstrap_does_not_import_image_or_model_libraries():
    environment = {**os.environ, "PYTHONPATH": str(ROOT / "src")}
    result = subprocess.run(
        [sys.executable, "-c", "import sys; from iphoto.process_runtime import prepare_worker; "
         "prepare_worker(['--worker'], {}); "
         "assert not {'numpy', 'PIL', 'torch', 'PySide6'} & set(sys.modules)"],
        env=environment, capture_output=True, text=True, timeout=15,
    )
    assert result.returncode == 0, result.stderr


def test_gui_import_default_is_restored_before_main_and_children(monkeypatch):
    environment = {"PATH": "kept"}
    observed = []
    def load(module):
        observed.append((module, dict(environment)))
        return SimpleNamespace(main=lambda: dict(environment))
    monkeypatch.setattr(process_runtime, "import_module", load)
    main = process_runtime.load_main([], environment)
    assert observed == [("iphoto.app", {"PATH": "kept", "OPENBLAS_NUM_THREADS": "1"})]
    assert main() == {"PATH": "kept"}
    assert prepare_worker(["--warm"], environment) == "iphoto.segmentation.warm_worker"
    assert environment == {"PATH": "kept"}


def test_failed_gui_import_restores_environment(monkeypatch):
    environment = {"PATH": "kept"}
    def fail(_):
        assert environment["OPENBLAS_NUM_THREADS"] == "1"
        raise RuntimeError("import failed")
    monkeypatch.setattr(process_runtime, "import_module", fail)
    with pytest.raises(RuntimeError, match="import failed"):
        process_runtime.load_main([], environment)
    assert environment == {"PATH": "kept"}


@pytest.mark.parametrize("setting", BLAS_THREAD_SETTINGS)
@pytest.mark.parametrize("value", ["", "2"])
def test_gui_entry_point_preserves_explicit_settings(monkeypatch, setting, value):
    environment = {setting: value}
    observed = []
    def load(module):
        observed.append((module, dict(environment)))
        return SimpleNamespace(main=lambda: dict(environment))
    monkeypatch.setattr(process_runtime, "import_module", load)
    assert process_runtime.load_main([], environment)() == {setting: value}
    assert observed == [("iphoto.app", {setting: value})]


NATIVE_RUNTIME_PROBE = '''
import ctypes
import json
import os
from pathlib import Path
import numpy as np
threads = None
for path in (Path(np.__file__).resolve().parent.parent / "numpy.libs").glob("*openblas*"):
    library = ctypes.CDLL(str(path))
    for symbol in ("scipy_openblas_get_num_threads64_", "openblas_get_num_threads64_", "openblas_get_num_threads"):
        getter = getattr(library, symbol, None)
        if getter is not None:
            getter.restype, getter.argtypes = ctypes.c_int, []
            threads = getter()
            break
observation = {"threads": threads, "settings": {key: os.environ[key] for key in
    ("OPENBLAS_NUM_THREADS", "OPENBLAS_DEFAULT_NUM_THREADS", "GOTO_NUM_THREADS", "OMP_NUM_THREADS") if key in os.environ}}
'''


@pytest.mark.parametrize("explicit_threads", [None, 2])
def test_real_gui_library_import_and_qt_child_inheritance(explicit_threads):
    environment = {key: value for key, value in os.environ.items() if key not in BLAS_THREAD_SETTINGS}
    environment["PYTHONPATH"] = str(ROOT / "src")
    if explicit_threads is not None:
        environment["OPENBLAS_NUM_THREADS"] = str(explicit_threads)
    child_program = NATIVE_RUNTIME_PROBE + "print(json.dumps(observation))\n"
    control = subprocess.run([sys.executable, "-c", child_program], env=environment,
                             capture_output=True, text=True, encoding="utf-8", timeout=20)
    assert control.returncode == 0, control.stderr
    expected_child = json.loads(control.stdout)
    program = (
        "from iphoto.process_runtime import load_main\n"
        "main = load_main([])\n"
        "assert main.__module__ == 'iphoto.app'\n" + NATIVE_RUNTIME_PROBE +
        "gui = observation\n"
        "import sys\n"
        "from PySide6.QtCore import QProcess\n"
        "from PySide6.QtGui import QGuiApplication\n"
        "app = QGuiApplication([])\n"
        "process = QProcess()\n"
        f"process.start(sys.executable, ['-c', {child_program!r}])\n"
        "assert process.waitForFinished(15000)\n"
        "assert process.exitCode() == 0, bytes(process.readAllStandardError())\n"
        "child = json.loads(bytes(process.readAllStandardOutput()).decode('utf-8'))\n"
        "print(json.dumps({'gui': gui, 'child': child}))\n"
    )
    result = subprocess.run([sys.executable, "-c", program], env=environment,
                            capture_output=True, text=True, encoding="utf-8", timeout=30)
    assert result.returncode == 0, result.stderr
    observed = json.loads(result.stdout)
    assert observed["gui"]["settings"] == expected_child["settings"]
    if observed["gui"]["threads"] is not None:
        assert observed["gui"]["threads"] == (explicit_threads or 1)
    assert observed["child"] == expected_child


@pytest.fixture
def picture(tmp_path):
    y, x = np.mgrid[:256, :384]
    pixels = np.stack(((3*x+y) % 256, (x+2*y) % 256, (2*x+3*y) % 256,
                       (x+y) % 256), axis=-1).astype("uint8")
    path = tmp_path / "source.png"
    Image.fromarray(pixels).save(path, icc_profile=SRGB_PROFILE)
    source = load_source(path)
    layer = new_layer("whole", True)
    layer["recipe"].update(exposure=.2, warmth=7, sharpness=15)
    return source, [layer]


def observe_worker(tmp_path, flag, requests, explicit_threads=None):
    """Observe the loaded BLAS library at exit, without preloading NumPy."""
    hook = tmp_path / "hook"
    hook.mkdir()
    observation = tmp_path / "runtime.json"
    (hook / "sitecustomize.py").write_text('''
import atexit
import json
import os
import sys

@atexit.register
def observe():
    import ctypes
    from pathlib import Path
    numpy = sys.modules.get("numpy")
    threads = None
    if numpy is not None:
        library_dir = Path(numpy.__file__).resolve().parent.parent / "numpy.libs"
        for path in library_dir.glob("*openblas*"):
            library = ctypes.CDLL(str(path))
            for symbol in ("scipy_openblas_get_num_threads64_", "openblas_get_num_threads64_", "openblas_get_num_threads"):
                try:
                    getter = getattr(library, symbol)
                except AttributeError:
                    continue
                getter.restype = ctypes.c_int
                getter.argtypes = []
                threads = getter()
                break
    Path(os.environ["IPHOTO_RUNTIME_OBSERVATION"]).write_text(json.dumps({
        "numpy_loaded": numpy is not None, "threads": threads,
        "env_threads": os.environ.get("OPENBLAS_NUM_THREADS"),
    }), encoding="utf-8")
''', encoding="utf-8")
    environment = {key: value for key, value in os.environ.items() if key not in BLAS_THREAD_SETTINGS}
    environment["PYTHONPATH"] = str(hook)
    environment["IPHOTO_RUNTIME_OBSERVATION"] = str(observation)
    if explicit_threads is not None:
        environment["OPENBLAS_NUM_THREADS"] = str(explicit_threads)
    result = subprocess.run(
        [sys.executable, str(ROOT / "run.py"), flag, str(tmp_path / "cache")],
        input="".join(json.dumps(request) + "\n" for request in requests),
        env=environment, capture_output=True, text=True, encoding="utf-8", timeout=30,
    )
    assert result.returncode == 0, result.stderr
    runtime = json.loads(observation.read_text(encoding="utf-8"))
    assert runtime["numpy_loaded"]
    assert runtime["env_threads"] == str(explicit_threads or 1)
    # The supported Windows distribution ships OpenBLAS with this getter.
    # Other NumPy distributions may use a different BLAS implementation.
    if sys.platform == "win32" or runtime["threads"] is not None:
        assert runtime["threads"] == (explicit_threads or 1)
    responses = [json.loads(line) for line in result.stdout.splitlines()]
    terminal = [response for response in responses
                if not (flag == '--export-worker' and response.get('type') == 'progress')]
    assert terminal and all(response["ok"] for response in terminal), result.stdout + result.stderr
    return responses


def assert_picture(path, expected):
    with Image.open(path) as actual:
        assert actual.mode == expected.mode
        assert actual.size == expected.size
        assert actual.tobytes() == expected.tobytes()
        # LittleCMS creates each process's sRGB profile with its creation time
        # in header bytes 24..35. The color description and tables must match.
        profile = actual.info["icc_profile"]
        assert profile[:24] + profile[36:] == SRGB_PROFILE[:24] + SRGB_PROFILE[36:]


@pytest.mark.parametrize("explicit_threads", [None, 2])
def test_real_preview_worker_runtime_pixels_and_fresh_cache_metrics(tmp_path, picture, explicit_threads):
    source, layers = picture
    responses = observe_worker(tmp_path, "--worker", [
        {"id": 1, "op": "open", "path": str(source.path)},
        {"id": 2, "op": "render", "generation": 7, "layers": layers},
        {"id": 3, "op": "render", "generation": 8, "layers": layers},
        {"id": 4, "op": "shutdown"},
    ], explicit_threads)
    first, cached = responses[1]["result"], responses[2]["result"]
    expected = render_layers(preview(source.image, 1600), layers)
    assert_picture(first["preview"], expected)
    assert first["histogram"] == histogram(expected)
    assert first["stats"] == stats(expected)
    assert not first["cache_hit"] and cached["cache_hit"]
    assert first["preview"] == cached["preview"]
    assert responses[2]["generation"] == 8
    assert first["stage_ms"]["png"] > 0
    assert sum(first["stage_ms"].values()) <= first["elapsed_ms"] + 1
    for stage in ("compose", "png", "histogram", "stats"):
        assert cached["stage_ms"][stage] == 0


def test_real_detail_worker_runtime_and_original_resolution_pixels(tmp_path, picture):
    source, layers = picture
    box = [60, 40, 320, 210]
    responses = observe_worker(tmp_path, "--detail-worker", [{
        "id": 1, "op": "detail", "generation": 2,
        "source_path": str(source.path), "source_sha": source.digest,
        "layers": layers, "box": box,
    }])
    assert responses[0]["generation"] == 2
    assert_picture(responses[0]["result"]["path"], render_detail_tile(source.image, layers, box))


def test_real_export_worker_runtime_full_size_pixels_and_source_preserved(tmp_path, picture):
    source, layers = picture
    original_bytes = source.path.read_bytes()
    target = tmp_path / "result.png"
    responses = observe_worker(tmp_path, "--export-worker", [{
        "id": 1, "op": "export", "source_path": str(source.path),
        "source_sha": source.digest, "stage_path": str(target),
        "layers": layers, "jpeg_quality": 100,
    }])
    assert [(response['type'], response['phase']) for response in responses[:-1]] == [
        ('progress', 1), ('progress', 2), ('progress', 3),
    ]
    assert all(response['id'] == 1 for response in responses)
    assert responses[-1]["result"]["width"] == source.image.width
    assert_picture(target, render_layers(source.image, layers))
    assert source.path.read_bytes() == original_bytes
