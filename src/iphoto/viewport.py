"""Canvas geometry and temporary navigation keys, independent of document edits.

Zoom 1 means one source pixel per physical display pixel. QML consumes DIP
geometry; no navigation operation submits a worker render or touches undo history.
"""

from math import isfinite

from PySide6.QtCore import QEvent, QObject, Property, Signal, Slot, Qt


class Viewport(QObject):
    changed = Signal()
    keysChanged = Signal()
    cancelGesture = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._source = (0, 0)
        self._size = (1.0, 1.0)
        self._dpr = 1.0
        self._zoom = 1.0
        self._x = self._y = 0.0
        self._fit = True
        self._space = self._control = self._alt = False
        self._window = None

    @Property(float, notify=changed)
    def zoom(self):
        return self._zoom

    @Property(float, notify=changed)
    def imageWidth(self):
        return self._source[0] * self._zoom / self._dpr

    @Property(float, notify=changed)
    def imageHeight(self):
        return self._source[1] * self._zoom / self._dpr

    @Property(float, notify=changed)
    def imageX(self):
        return self._x

    @Property(float, notify=changed)
    def imageY(self):
        return self._y

    @Property(bool, notify=changed)
    def fitMode(self):
        return self._fit

    @Property(bool, notify=changed)
    def reducedPreview(self):
        return False

    @Property("QVariantList", notify=changed)
    def visibleRect(self):
        if not self.imageWidth or not self.imageHeight:
            return [0, 0, 1, 1]
        left = max(0.0, -self._x / self.imageWidth)
        top = max(0.0, -self._y / self.imageHeight)
        right = min(1.0, (self._size[0] - self._x) / self.imageWidth)
        bottom = min(1.0, (self._size[1] - self._y) / self.imageHeight)
        return [left, top, right - left, bottom - top]

    @Property(bool, notify=keysChanged)
    def spaceHeld(self):
        return self._space

    @Property(bool, notify=keysChanged)
    def temporaryZoom(self):
        return self._space and self._control

    @Property(bool, notify=keysChanged)
    def altHeld(self):
        return self._alt

    def _fit_zoom(self):
        if not all(self._source):
            return 1.0
        return min(1.0, *(v * self._dpr / s for v, s in zip(self._size, self._source)))

    def _constrain(self):
        # Permit moving small images too, but always retain a recoverable edge.
        for axis, extent in (("_x", self.imageWidth), ("_y", self.imageHeight)):
            available = self._size[0 if axis == "_x" else 1]
            edge = min(64.0, extent / 2, available / 2)
            setattr(
                self,
                axis,
                max(edge - extent, min(available - edge, getattr(self, axis))),
            )

    @Slot(int, int)
    def setSource(self, width, height):
        self._source = (max(0, width), max(0, height))
        self.resetKeys()
        self.fit()

    @Slot(float, float, float)
    def resize(self, width, height, dpr):
        if not all(isfinite(v) and v > 0 for v in (width, height, dpr)):
            return
        new_size = (width, height)
        if new_size == self._size and dpr == self._dpr:
            return
        cx = (self._size[0] / 2 - self._x) / max(self.imageWidth, 1e-9)
        cy = (self._size[1] / 2 - self._y) / max(self.imageHeight, 1e-9)
        self._size, self._dpr = new_size, dpr
        if self._fit:
            self.fit()
        else:
            self._x = width / 2 - cx * self.imageWidth
            self._y = height / 2 - cy * self.imageHeight
            self._constrain()
            self.changed.emit()

    @Slot()
    def fit(self):
        self._fit = True
        self._zoom = self._fit_zoom()
        self._x = (self._size[0] - self.imageWidth) / 2
        self._y = (self._size[1] - self.imageHeight) / 2
        self.changed.emit()

    @Slot(float, float, float)
    def zoomAt(self, value, x, y):
        if not all(self._source) or not all(isfinite(v) for v in (value, x, y)):
            return
        value = max(min(0.01, self._fit_zoom()), min(32.0, value))
        ratio = value / self._zoom
        self._x, self._y = x - (x - self._x) * ratio, y - (y - self._y) * ratio
        self._zoom, self._fit = value, False
        self._constrain()
        self.changed.emit()

    @Slot(float)
    def setZoom(self, value):
        self.zoomAt(value, self._size[0] / 2, self._size[1] / 2)

    @Slot(int)
    def step(self, direction):
        # Familiar, predictable stops while wheel/drag zoom remains continuous.
        stops = (
            0.01,
            0.025,
            0.05,
            0.0833,
            0.125,
            0.1667,
            0.25,
            0.3333,
            0.5,
            0.6667,
            1,
            1.5,
            2,
            3,
            4,
            6,
            8,
            12,
            16,
            24,
            32,
        )
        values = stops if direction > 0 else reversed(stops)
        value = next(
            (v for v in values if (v - self._zoom) * direction > 0.0001), self._zoom
        )
        self.setZoom(value)

    @Slot(float, float)
    def pan(self, dx, dy):
        if not all(self._source) or not all(isfinite(v) for v in (dx, dy)):
            return
        if dx == dy == 0:
            return
        self._x += dx
        self._y += dy
        self._fit = False
        self._constrain()
        self.changed.emit()

    @Slot(float, float)
    def centerOn(self, x, y):
        if not all(self._source) or not all(isfinite(v) for v in (x, y)):
            return
        self._x = self._size[0] / 2 - min(1, max(0, x)) * self.imageWidth
        self._y = self._size[1] / 2 - min(1, max(0, y)) * self.imageHeight
        self._fit = False
        self._constrain()
        self.changed.emit()

    @Slot(QObject)
    def attachWindow(self, window):
        if self._window is window:
            return
        if self._window is not None:
            self._window.removeEventFilter(self)
        self._window = window
        window.installEventFilter(self)

    @Slot()
    def resetKeys(self):
        self._space = self._control = self._alt = False
        self.keysChanged.emit()
        self.cancelGesture.emit()

    def eventFilter(self, watched, event):
        kind = event.type()
        if kind in (QEvent.WindowDeactivate, QEvent.Hide):
            self.resetKeys()
        elif kind in (QEvent.KeyPress, QEvent.KeyRelease, QEvent.ShortcutOverride):
            key = event.key()
            allowed = bool(watched.property("navigationShortcutsEnabled"))
            if key == Qt.Key_Space and (allowed or self._space):
                if kind == QEvent.ShortcutOverride:
                    event.accept()
                    return True
                if not event.isAutoRepeat():
                    self._space = kind == QEvent.KeyPress and allowed
                    self._control = bool(event.modifiers() & Qt.ControlModifier)
                    self._alt = bool(event.modifiers() & Qt.AltModifier)
                    self.keysChanged.emit()
                return True
            if key in (Qt.Key_Control, Qt.Key_Alt) and kind != QEvent.ShortcutOverride:
                if key == Qt.Key_Control:
                    self._control = kind == QEvent.KeyPress and allowed
                else:
                    self._alt = kind == QEvent.KeyPress and allowed
                self.keysChanged.emit()
        return super().eventFilter(watched, event)
