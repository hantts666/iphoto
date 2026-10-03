"""Prepare worker configuration before importing any image libraries."""

import os
from importlib import import_module


WORKER_MODULES = (
    ("--worker", "iphoto.worker"),
    ("--warm", "iphoto.segmentation.warm_worker"),
    ("--pixel-worker", "iphoto.segmentation.pixel_worker"),
    ("--detail-worker", "iphoto.detail_worker"),
    ("--matte-worker", "iphoto.matting.worker"),
    ("--export-worker", "iphoto.export_worker"),
)
IMAGE_WORKERS = frozenset(("iphoto.worker", "iphoto.detail_worker", "iphoto.export_worker"))
BLAS_THREAD_SETTINGS = (
    "OPENBLAS_NUM_THREADS",
    "OPENBLAS_DEFAULT_NUM_THREADS",
    "GOTO_NUM_THREADS",
    "OMP_NUM_THREADS",
)


def prepare_worker(arguments, environment=None):
    """Select the existing dispatch role and configure only photo workers.

    Their image operations do not benefit from OpenBLAS's machine-wide default
    thread pool. Set its default before NumPy loads; preserve any explicit user
    setting, including an empty value. GUI and model processes keep their own
    configuration rather than inheriting a policy set in the GUI process.
    """
    module = next((module for flag, module in WORKER_MODULES if flag in arguments), None)
    environment = os.environ if environment is None else environment
    if module in IMAGE_WORKERS and not any(key in environment for key in BLAS_THREAD_SETTINGS):
        environment["OPENBLAS_NUM_THREADS"] = "1"
    return module


def load_main(arguments, environment=None):
    """Load the entry point, keeping GUI import defaults out of child processes.

    The GUI imports NumPy through workspace before it starts Qt or workers.
    Initialize its image libraries with the photo default, then restore the
    inherited environment before calling main. Worker defaults remain scoped
    to their own process and explicit user settings are always respected.
    """
    environment = os.environ if environment is None else environment
    module = prepare_worker(arguments, environment)
    if module is not None or any(key in environment for key in BLAS_THREAD_SETTINGS):
        return import_module(module or "iphoto.app").main
    environment["OPENBLAS_NUM_THREADS"] = "1"
    try:
        return import_module("iphoto.app").main
    finally:
        environment.pop("OPENBLAS_NUM_THREADS", None)
