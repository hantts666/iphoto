"""Progressive source-resolution viewport tiles, separate from the edit worker."""

import json
from math import ceil, floor
from pathlib import Path
import sys

from PySide6.QtCore import QProcess, QUrl

from ..paths import ROOT


def _visible_box(self, visible_rect=None):
    x, y, width, height = visible_rect if visible_rect is not None else self._viewport.visibleRect
    if width <= 0 or height <= 0:
        return None
    source_width, source_height = self._width, self._height
    visible = (
        max(0, floor(x * source_width)), max(0, floor(y * source_height)),
        min(source_width, ceil((x + width) * source_width)),
        min(source_height, ceil((y + height) * source_height)),
    )
    if visible[0] >= visible[2] or visible[1] >= visible[3]:
        return None
    return visible


def _tile_box(self, visible):
    width = visible[2] - visible[0]
    height = visible[3] - visible[1]
    pad_x, pad_y = round(width * .2), round(height * .2)

    def snapped(px, py):
        return (
            max(0, floor((visible[0] - px) / 128) * 128),
            max(0, floor((visible[1] - py) / 128) * 128),
            min(self._width, ceil((visible[2] + px) / 128) * 128),
            min(self._height, ceil((visible[3] + py) / 128) * 128),
        )

    box = snapped(pad_x, pad_y)
    if (box[2] - box[0]) * (box[3] - box[1]) > 8_000_000:
        box = snapped(0, 0)
    if (box[2] - box[0]) * (box[3] - box[1]) > 8_000_000:
        return None
    return box


def wanted(self, zoom, visible_rect):
    if not self.hasImage or max(self._width, self._height) <= 1600:
        return False
    threshold = max(.45, 1600 / max(self._width, self._height) * 1.15)
    if zoom < threshold:
        return False
    visible = _visible_box(self, visible_rect)
    return visible is not None and _tile_box(self, visible) is not None


def request(self):
    if self._closing:
        return
    if not wanted(self, self._viewport.zoom, self._viewport.visibleRect):
        stop(self)
        return
    # Cloud waiting prevents document edits but owns no local image worker.
    # Browsing may render the current snapshot once photo preparation ends.
    if self.imageWorkBusy or self._warm_process.state() != QProcess.NotRunning:
        return
    if self._detail_failed_generation == (self._generation, self._detail_version):
        return
    visible = _visible_box(self)
    if visible is None:
        return
    mask = (
        self._region_candidate["layers"][self._region_index]["mask"]
        if self._region_candidate else self._candidate or self._layer()["mask"]
    )
    needs_mask = (
        self.selection.showMask and self._mask_view != "adjustment"
        and (self._mask_view in ('white', 'black') or "bitmap" in mask or bool(mask["ops"]) or mask["feather"] > 0
             or mask.get("edge_shift", 0) != 0)
    )
    current = self._detail_box
    if (self._detail_url and self._detail_generation == self._generation
            and self._detail_frame_version == self._detail_version
            and (not needs_mask or self._detail_mask_url)
            and current is not None and all((current[0] <= visible[0],
                                              current[1] <= visible[1],
                                              current[2] >= visible[2],
                                              current[3] >= visible[3]))):
        return
    box = _tile_box(self, visible)
    if box is None:
        return
    if any(item and item["box"] == box and item["version"] == self._detail_version
           and bool(item.get("mask")) == needs_mask
           for item in (self._detail_active, self._detail_pending)):
        return
    from .worker_bridge import preview_payload

    payload = preview_payload(self)
    self._detail_serial += 1
    self._detail_pending = {
        "id": self._detail_serial, "op": "detail", "generation": self._generation,
        "version": self._detail_version, "source_path": self._path,
        "source_sha": self._sha, "box": box, "layers": payload["layers"],
        **({"mask": mask, "mask_view": payload["mask_view"]} if needs_mask else {}),
    }
    self._detail_idle_timer.stop()
    pump(self)
    self.changed.emit()


def invalidate(self):
    self._detail_idle_timer.stop()
    self._detail_version += 1
    # Keep the last color tile, with its delivered generation and rectangle.
    # The view can retain a complete frozen frame while the next tile renders.
    self._detail_mask_url = ""
    self._detail_pending = None
    self.changed.emit()


def pump(self):
    if self._closing or self._detail_aborting or self._detail_active or not self._detail_pending:
        return
    if self._detail_process.state() == QProcess.NotRunning:
        interpreter = Path(sys.executable)
        if interpreter.name.lower() == "pythonw.exe":
            interpreter = interpreter.with_name("python.exe")
        self._detail_process.start(
            str(interpreter), [str(ROOT / "run.py"), "--detail-worker", self._cache.name]
        )
        return
    if self._detail_process.state() != QProcess.Running:
        return
    self._detail_active, self._detail_pending = self._detail_pending, None
    self._detail_process.write(
        (json.dumps(self._detail_active, ensure_ascii=False) + "\n").encode("utf-8")
    )


def read(self):
    self._detail_buffer += bytes(self._detail_process.readAllStandardOutput())
    while b"\n" in self._detail_buffer:
        line, self._detail_buffer = self._detail_buffer.split(b"\n", 1)
        if not line.lstrip().startswith(b"{"):
            continue
        try:
            response = json.loads(line)
            active = self._detail_active
            if not active or response.get("id") != active["id"]:
                continue
            self._detail_active = None
            current = (
                active["generation"] == self._generation
                and active["version"] == self._detail_version
                and active["source_sha"] == self._sha
            )
            from .preview_updates import accepts

            intermediate = (
                active["source_sha"] == self._sha
                and active["generation"] < self._generation
                and accepts(self, active["generation"])
                and active["version"] <= self._detail_version
                and (active["generation"], active["version"])
                >= (self._detail_generation, self._detail_frame_version)
            )
            if (current or intermediate) and response.get("ok"):
                box = response["result"]["box"]
                self._detail_url = QUrl.fromLocalFile(response["result"]["path"]).toString()
                original_path = response["result"].get("original")
                self._detail_original_url = QUrl.fromLocalFile(original_path).toString() if original_path else ""
                mask_path = response["result"].get("mask")
                self._detail_mask_url = QUrl.fromLocalFile(mask_path).toString() if current and mask_path else ""
                self._detail_box = tuple(box)
                self._detail_generation = active["generation"]
                self._detail_frame_version = active["version"]
            elif current:
                print("[detail worker] " + response.get("error", "unknown error"), file=sys.stderr)
                self._detail_failed_generation = (self._generation, self._detail_version)
            if self._detail_pending is None:
                self._detail_idle_timer.start()
            self.changed.emit()
            pump(self)
        except Exception as exc:
            self._detail_active = None
            self._detail_failed_generation = (self._generation, self._detail_version)
            print("[detail worker] invalid response: " + str(exc), file=sys.stderr)
            if self._detail_pending is None:
                self._detail_idle_timer.start()
            self.changed.emit()


def stderr(self):
    data = bytes(self._detail_process.readAllStandardError()).decode("utf-8", "replace")
    if data:
        print("[detail worker] " + data[-2000:], file=sys.stderr)


def release(self):
    """Stop source-image work while preserving the delivered frame metadata."""
    self._detail_idle_timer.stop()
    self._detail_pending = self._detail_active = None
    self._detail_buffer = b""
    if self._detail_process.state() != QProcess.NotRunning:
        self._detail_aborting = True
        self._detail_process.kill()
    self.changed.emit()


def stop(self):
    self._detail_idle_timer.stop()
    if (self._detail_process.state() == QProcess.NotRunning and not self._detail_url
            and not self._detail_pending and not self._detail_active):
        return
    self._detail_url = ""
    self._detail_original_url = ""
    self._detail_mask_url = ""
    self._detail_box = None
    self._detail_version += 1
    self._detail_failed_generation = -1
    release(self)


def park(self):
    """Release the source image process while keeping its displayed tile."""
    if (self._detail_process.state() == QProcess.Running
            and self._detail_active is None and self._detail_pending is None):
        release(self)


def finished(self, *_):
    aborted = self._detail_aborting
    self._detail_aborting = False
    had_active = self._detail_active is not None
    self._detail_active = None
    self._detail_buffer = b""
    if had_active and not aborted:
        self._detail_failed_generation = (self._generation, self._detail_version)
    self.changed.emit()
    pump(self)


def error(self, _):
    if self._detail_process.state() == QProcess.NotRunning and not self._detail_aborting:
        self._detail_active = self._detail_pending = None
        self._detail_failed_generation = (self._generation, self._detail_version)
        self.changed.emit()
