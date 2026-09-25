"""Load a consistent, app-local MSVC runtime before ONNX on Windows.

Python and Qt ship newer vcruntime than some supported Windows installations.
Loading the system's older msvcp alongside it can crash ONNX during import.
Use Qt's signed companion DLLs without changing PATH or system installations.
Keep handles alive for the lifetime of the inference worker.
"""

import sys
from importlib.util import find_spec
from pathlib import Path

_handles = []


def prepare_runtime():
    if sys.platform != "win32" or _handles:
        return
    import ctypes

    spec = find_spec("PySide6")
    if spec is None or not spec.submodule_search_locations:
        return
    directory = Path(next(iter(spec.submodule_search_locations)))
    for name in (
        "vcruntime140.dll",
        "vcruntime140_1.dll",
        "msvcp140.dll",
        "msvcp140_1.dll",
        "msvcp140_2.dll",
        "msvcp140_codecvt_ids.dll",
        "concrt140.dll",
    ):
        path = directory / name
        if path.is_file():
            _handles.append(ctypes.WinDLL(str(path)))
