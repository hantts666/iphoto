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
        self._views = {}
        self._view = 'alpha'
        self._note = ''
        self._loading = False
        self._result = None
        self._draft_views = {}
        self._result_views = {}
        self._show_result = False
        self._native = False
        self.timer = QTimer(self)
        self.timer.setSingleShot(True)
        self.timer.setInterval(160)
        self.timer.timeout.connect(self._refresh)
        editor.changed.connect(self.changed.emit)

    def _valid(self):
        state = self.state
        e = self.editor
        return bool(state and not e.hasRegionDraft and state['generation'] == e._generation and state['sha'] == e._sha
                    and state['layer_id'] == e._selected and state['candidate'] == e._candidate
                    and state['layers'] == e._layers and state['target_id'] == e._selection_target_id)

    def matches_request(self, request):
        return bool(self._valid() and request.get('channel_token') == self.state['token']
                    and request.get('channel_revision') == self.state['revision']
                    and request.get('channel_options') == self._options
                    and request.get('mask') == self.state['mask'])

    @Property(bool, notify=changed)
    def opened(self):
        return self._valid()

    @Property(bool, notify=changed)
    def hasResult(self):
        return bool(self._valid() and self._result is not None and self._result_views)

    @Property(bool, notify=changed)
    def showingResult(self):
        return self.hasResult and self._show_result

    @Property(bool, notify=changed)
    def nativeView(self):
        return self._valid() and self._native

    @Slot()
    def compare(self):
        if not self.hasResult or self.editor.busy or self._loading:
            return
        self._show_result = not self._show_result
        self._views = self._result_views if self._show_result else self._draft_views
        self._preview = self._views[self._view]
        self.changed.emit()

    @Property(str, notify=changed)
    def previewUrl(self):
        return self._preview if self._valid() else ''

    @Property('QVariantMap', notify=changed)
    def options(self):
        return self._options

    @Property(str, notify=changed)
    def view(self):
        return self._view

    @Slot(str)
    def setView(self, value):
        if not self._valid() or self.editor.busy or value not in self._views:
            return
        self._view=value
        self._preview=self._views[value]
        self.changed.emit()

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
        # Preview owns its input independently. Opening a dialog must not
        # create a selection, dirty the project or change its undo history.
        candidate = deepcopy(e._candidate)
        self.state = {'token':uuid4().hex, 'generation':e._generation, 'sha':e._sha,
                      'layer_id':e._selected, 'candidate':candidate,
                      'mask':deepcopy(candidate if candidate is not None else e._layer()['mask']),
                      'layers':deepcopy(e._layers),
                      'target_id':e._selection_target_id, 'revision':0}
        self._preview, self._note = '', '正在比较红、绿、蓝、亮度与通道计算…'
        self._views, self._view = {}, 'alpha'
        self._result, self._draft_views, self._result_views = None, {}, {}
        self._show_result, self._native = False, False
        # A second color-line solve can erase learned translucent coverage.
        # Keep it an explicit previewable choice instead of an automatic pass.
        self._options = {'channel':'auto','black':0,'white':255,'gamma':1.,'invert':False,'radius':32,'ai':True,'interior':False,
                         'detail':False,'color':True}
        self._refresh(initial=True)

    def _refresh(self, initial=False):
        if not self._valid() or self.editor.busy:
            return
        self.state['revision'] += 1
        self._loading = True
        self.editor._queue = type(self.editor._queue)(r for r in self.editor._queue if r['op'] != 'channel_preview')
        self.editor._request('channel_preview', mask=self.state['mask'], options=self._options, initial=initial, display_preview=True, native_views=True,
                              expected_sha256=self.state['sha'], context={'token':self.state['token'],
                              'revision':self.state['revision']})
        self.changed.emit()

    def ready(self, result, context, generation):
        if not self._valid() or context['token'] != self.state['token'] or context['revision'] != self.state['revision'] or generation != self.editor._generation:
            return
        from PySide6.QtCore import QUrl
        self._options = result['options']
        self._views = {name:QUrl.fromLocalFile(path).toString() for name,path in result.get('views',{'alpha':result['path']}).items()}
        self._native = result.get('native', False)
        self._show_result = context.get('result_preview', False)
        if self._show_result:
            self._result_views = self._views
        else:
            self._draft_views = self._views
        if self._view not in self._views:self._view='alpha'
        self._preview = self._views[self._view]
        self._loading = False
        self._note = ('从整张照片建立范围：选择通道，调黑白场和灰度；白色保留、黑色移除、灰色半透明。'
                      if self._options.get('whole') else f"推荐 {CHANNEL_NAMES[result['channel']]}通道；白色保留，黑色移除，灰色保留透明度。" + ('通道差异偏弱，需检查边缘。' if result['score'] < 2 else ''))
        self._note += ' 可切换100%拖动检查。' if self._native else ' 大范围预览已缩小显示。'
        if self._show_result:
            self._note = '实际结果已就绪；黑白底使用与透明 PNG 相同的前景颜色恢复。' + '；'.join(self._result['quality'].get('warnings', []))
        if result.get('focused'):self._note='当前范围局部 · '+self._note
        self.changed.emit()

    def failed(self, message, context):
        if (self.state and self._valid() and context.get('token') == self.state['token']
                and context.get('revision') == self.state['revision']):
            self._loading = False
            self._note = message
            self._result = None
            self._result_views = {}
            self.changed.emit()

    @Slot(str, 'QVariant')
    def setOption(self, name, value):
        if not self._valid() or self.editor.busy or name not in self._options:
            return
        from ..matting.channels import validate_options
        try:
            options = validate_options({**self._options, name:value})
        except ValueError as exc:
            # Reject before invalidating an existing preview or cached result.
            # QML controls constrain paired levels; this also guards callers
            # using stale bounds or non-UI input.
            self._note = '参数未更新：' + str(exc)
            self.changed.emit()
            return
        if options == self._options:
            return
        self._options = options
        self._result, self._draft_views, self._result_views = None, {}, {}
        self._show_result = False
        self._preview, self._views = '', {}
        # Invalidate in-flight previews immediately; waiting for the debounce
        # would let an older reply overwrite the user's latest controls.
        self.state['revision'] += 1
        self._loading = True
        self.timer.start()
        self.changed.emit()

    @Slot()
    def previewResult(self):
        if self.hasResult:
            return
        self._calculate(preview_only=True)

    def _calculate(self, *, preview_only=False):
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
        if preview_only:
            self._loading = True
            self._note = '正在计算实际透明边缘，原范围保留…可取消'
        if e._request('matte', method='channel', mask=deepcopy(self.state['mask']), channel_options=options,
                    channel_token=self.state['token'], channel_revision=self.state['revision'], preview_only=preview_only) is False:
            self.cancelled()
        self.changed.emit()

    def result_ready(self, result, active):
        if not self.matches_request(active):
            return
        from ..document import validate_mask
        self._result = {'mask':validate_mask(result['mask']), 'quality':deepcopy(result['quality'])}
        self._note = '正在准备实际黑白底效果与前景颜色…'
        layers = deepcopy(self.state['layers'])
        target = self.state['target_id'] or (self.state['layer_id'] if self.state['candidate'] is None else '')
        if target:
            next(layer for layer in layers if layer['id'] == target)['mask'] = deepcopy(self._result['mask'])
        self.editor._request('channel_preview', mask=self.state['mask'], result_mask=self._result['mask'],
            options=self._options, layers=layers, expected_sha256=self.state['sha'], native_views=True,
            context={'token':self.state['token'], 'revision':self.state['revision'], 'result_preview':True})
        self.changed.emit()

    def cancelled(self):
        if self._valid():
            self._loading = False
            self._note = '已取消计算，原范围保留；可调整参数后再预览。'
            self._result, self._result_views = None, {}
            self.changed.emit()

    @Slot()
    def apply(self):
        if self.hasResult and not self.editor.busy and not self._loading:
            self._publish(self._result)
        else:
            self._calculate()

    def complete_result(self, result, active):
        if self.matches_request(active):
            self._publish(result)

    def _publish(self, result):
        from ..document import validate_mask
        from .matting import complete
        result = {**result, 'mask':validate_mask(result['mask'])}
        create_draft = self.state['candidate'] is None
        self.close()
        if create_draft:
            self.editor.beginSelection('current')
        complete(self.editor, result)

    @Slot()
    def close(self):
        self.timer.stop()
        token = (self.state or {}).get('token')
        jobs = (self.editor._matte_active,self.editor._matte_pending)
        owned = bool(token and any(job and job.get('channel_token')==token for job in jobs))
        self.state = None
        self._preview = ''
        self._views = {}
        self._loading = False
        self._result, self._draft_views, self._result_views = None, {}, {}
        if owned:
            self.editor.cancelMatte()
        self.changed.emit()
