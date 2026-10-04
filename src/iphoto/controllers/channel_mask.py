"""Editable channel preview; only applying it writes a selection history step."""

from copy import deepcopy
from uuid import uuid4

from PySide6.QtCore import QObject, Property, Signal, Slot, QTimer
from ..matting.channels import CHANNEL_NAMES


class ChannelMaskController(QObject):
    changed = Signal()

    def __init__(self, editor):
        super().__init__(editor)
        self.editor = editor
        self.state = None
        self._preview = ''
        self._options = {}
        self._note = ''
        self._loading = False
        self.timer = QTimer(self)
        self.timer.setSingleShot(True)
        self.timer.setInterval(160)
        self.timer.timeout.connect(self._refresh)
        editor.changed.connect(self.changed.emit)

    def _valid(self):
        state = self.state
        e = self.editor
        return bool(state and state['generation'] == e._generation and state['sha'] == e._sha
                    and state['layer_id'] == e._selected and state['mask'] == e._candidate)

    @Property(bool, notify=changed)
    def opened(self):
        return self._valid()

    @Property(str, notify=changed)
    def previewUrl(self):
        return self._preview if self._valid() else ''

    @Property('QVariantMap', notify=changed)
    def options(self):
        return self._options

    @Property(str, notify=changed)
    def note(self):
        return self._note

    @Property(bool, notify=changed)
    def loading(self):
        return self._loading

    @Slot()
    def open(self):
        e = self.editor
        if e.busy or not e.hasImage or e.hasRegionDraft:
            return
        if not e.hasSelectionDraft:
            e.beginSelection('current')
        self.state = {'token':uuid4().hex, 'generation':e._generation, 'sha':e._sha,
                      'layer_id':e._selected, 'mask':deepcopy(e._candidate), 'revision':0}
        self._preview, self._note = '', '正在比较红、绿、蓝、亮度与通道计算…'
        self._options = {'channel':'auto','black':0,'white':255,'gamma':1.,'invert':False,'radius':32,'ai':True,'interior':False,
                         'detail':True,'color':True}
        self._refresh(initial=True)

    def _refresh(self, initial=False):
        if not self._valid() or self.editor.busy:
            return
        self.state['revision'] += 1
        self._loading = True
        self.editor._queue = type(self.editor._queue)(r for r in self.editor._queue if r['op'] != 'channel_preview')
        self.editor._request('channel_preview', mask=self.state['mask'], options=self._options, initial=initial,
                              expected_sha256=self.state['sha'], context={'token':self.state['token'],
                              'revision':self.state['revision']})
        self.changed.emit()

    def ready(self, result, context, generation):
        if not self._valid() or context['token'] != self.state['token'] or context['revision'] != self.state['revision'] or generation != self.editor._generation:
            return
        from PySide6.QtCore import QUrl
        self._options, self._preview = result['options'], QUrl.fromLocalFile(result['path']).toString()
        self._loading = False
        self._note = ('从整张照片建立范围：选择通道，调黑白场和灰度；白色保留、黑色移除、灰色半透明。'
                      if self._options.get('whole') else f"推荐 {CHANNEL_NAMES[result['channel']]}通道；白色保留，黑色移除，灰色保留透明度。" + ('通道差异偏弱，需检查边缘。' if result['score'] < 2 else ''))
        self.changed.emit()

    def failed(self, message, context):
        if (self.state and self._valid() and context.get('token') == self.state['token']
                and context.get('revision') == self.state['revision']):
            self._loading = False
            self._note = message
            self.changed.emit()

    @Slot(str, 'QVariant')
    def setOption(self, name, value):
        if not self._valid() or self.editor.busy or name not in self._options:
            return
        self._options = {**self._options, name:value}
        # Invalidate in-flight previews immediately; waiting for the debounce
        # would let an older reply overwrite the user's latest controls.
        self.state['revision'] += 1
        self._loading = True
        self.timer.start()
        self.changed.emit()

    @Slot()
    def apply(self):
        e = self.editor
        if not self._valid() or e.busy or self._loading or not self._preview:
            return
        from ..matting.channels import validate_options
        try:
            options = validate_options(self._options)
        except ValueError as exc:
            self._note = str(exc)
            self.changed.emit()
            return
        self.timer.stop()
        e._status = '正在结合通道灰度与AI细化原图透明边缘…可取消' if options['ai'] else '正在按原图提取通道透明度…可取消'
        e._request('matte', method='channel', mask=deepcopy(self.state['mask']), channel_options=options,
                    channel_token=self.state['token'])

    @Slot()
    def close(self):
        self.timer.stop()
        token = (self.state or {}).get('token')
        jobs = (self.editor._matte_active,self.editor._matte_pending)
        owned = bool(token and any(job and job.get('channel_token')==token for job in jobs))
        self.state = None
        self._preview = ''
        self._loading = False
        if owned:
            self.editor.cancelMatte()
        self.changed.emit()
