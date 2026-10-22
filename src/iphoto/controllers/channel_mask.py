"""Editable channel preview; only applying it writes a selection history step."""

from copy import deepcopy
from uuid import uuid4

from PySide6.QtCore import QObject, Property, Signal, Slot, QTimer
from ..matting.channels import CHANNEL_NAMES

LEVEL_CONTROLS = ('channel','black','white','gamma','invert')


class ChannelMaskController(QObject):
    changed = Signal()

    def __init__(self, editor):
        super().__init__(editor)
        self.editor = editor
        self.state = None
        self._preview = ''
        self._options = {}
        self._views = {}
        self._choices = []
        self._levels, self._suggestions = {}, {}
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
        self.sessionTimer = QTimer(self)
        self.sessionTimer.setSingleShot(True)
        self.sessionTimer.setInterval(30000)
        self.sessionTimer.timeout.connect(self._release_session)
        editor.changed.connect(self._editor_changed)

    def _editor_changed(self):
        if self.state and not self._valid():
            self.close()
        else:
            self.changed.emit()

    def _release_session(self):
        from .matte_process import release_session
        token = (self.state or {}).get('token')
        if token:
            release_session(self.editor,token)

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

    @Property('QVariantList', notify=changed)
    def channelChoices(self):
        return self._choices

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

    @Property(bool, notify=changed)
    def savesCurrentLayer(self):
        return bool(self.state and (self.state['target_id']
                    or self.state['candidate'] is None and not self._options.get('whole')))

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
                      'content_prepared':False,'content_reference':None,
                      'target_id':e._selection_target_id, 'revision':0}
        self._preview, self._note = '', '正在准备图像内容与原像素通道（调色前）…'
        self._views, self._view = {}, 'alpha'
        self._choices = []
        self._levels, self._suggestions = {}, {}
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
        self.editor._request('channel_preview', mask=self.state['mask'], options=self._options, initial=initial, display_preview=True, native_views=True, channel_choices=True,
                              **({'analysis_layers':self.state['layers']} if not self.state['content_prepared'] else
                                 {'content_reference':self.state['content_reference']} if self.state['content_reference'] else {}),
                              expected_sha256=self.state['sha'], context={'token':self.state['token'],
                              'revision':self.state['revision']})
        self.changed.emit()

    def ready(self, result, context, generation):
        if not self._valid() or context['token'] != self.state['token'] or context['revision'] != self.state['revision'] or generation != self.editor._generation:
            return
        from PySide6.QtCore import QUrl
        reference=result.get('content_reference')
        if self.state['content_prepared'] and reference!=self.state['content_reference']:
            self.failed('图像内容参照已变化，请重新打开通道工具',context)
            return
        self.state['content_reference']=deepcopy(reference)
        self.state['content_prepared']=True
        self._options = result['options']
        if 'level_suggestions' in result:
            self._suggestions = deepcopy(result['level_suggestions'])
        self._views = {name:QUrl.fromLocalFile(path).toString() for name,path in result.get('views',{'alpha':result['path']}).items()}
        self._choices = [{**item, 'url': QUrl.fromLocalFile(item['path']).toString()}
                         for item in result.get('channel_choices', [])]
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
                      if self._options.get('whole') else f"当前 {CHANNEL_NAMES[result['channel']]}通道；白色保留，黑色移除，灰色保留透明度。" + ('通道差异偏弱，需检查边缘。' if result['score'] < 2 else ''))
        self._note += ' 可切换100%拖动检查。' if self._native else ' 大范围预览已缩小显示。'
        if self._show_result:
            prefix = ('已沿用相同的 AI 透明边缘；' if self._result['quality'].get('reused_alpha')
                      else '实际结果已就绪；')
            self._note = prefix + '黑白底与透明 PNG 的前景颜色一致。' + '；'.join(self._result['quality'].get('warnings', []))
        if result.get('focused'):self._note='当前范围局部 · '+self._note
        if reference:self._note='依据已应用的图像内容（调色前）。'+self._note
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
            changes = {name:value}
            if name=='channel' and isinstance(value,str) and value in (*CHANNEL_NAMES,'auto'):
                # One plane's byte endpoints do not describe another plane.
                # Restore manual tuning before using fresh native references.
                changes = self._levels.get(value) or self._suggestions.get(value) or {
                    'channel':value,'black':0,'white':255,'gamma':1.,'invert':False}
            options = validate_options({**self._options, **changes})
        except ValueError as exc:
            # Reject before invalidating an existing preview or cached result.
            # QML controls constrain paired levels; this also guards callers
            # using stale bounds or non-UI input.
            self._note = '参数未更新：' + str(exc)
            self.changed.emit()
            return
        if options == self._options:
            return
        self._levels[self._options['channel']] = {key:self._options[key] for key in LEVEL_CONTROLS}
        self._levels[options['channel']] = {key:options[key] for key in LEVEL_CONTROLS}
        if name=='channel' or self._view == 'channel' and name in ('black', 'white', 'gamma', 'invert'):
            self._view = 'alpha'
        self._options = options
        self._result, self._draft_views, self._result_views = None, {}, {}
        self._show_result = False
        self._preview, self._views = '', {}
        self._choices = []
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
        self.sessionTimer.stop()
        e._status = '正在结合通道灰度与AI细化原图透明边缘…可取消' if options['ai'] else '正在按原图提取通道透明度…可取消'
        if preview_only:
            self._loading = True
            self._note = '正在计算实际透明边缘，原范围保留…可取消'
        if e._request('matte', method='channel', mask=deepcopy(self.state['mask']), channel_options=options,
                    **({'content_reference':self.state['content_reference']} if self.state['content_reference'] else {}),
                    channel_token=self.state['token'], channel_session=self.state['token'],
                    channel_revision=self.state['revision'], preview_only=preview_only) is False:
            self.cancelled()
        self.changed.emit()

    def result_ready(self, result, active):
        if not self.matches_request(active):
            return
        if not self._matches_content(result):
            self.failed('图像内容已变化，实际效果未应用',{'token':self.state['token'],'revision':self.state['revision']})
            return
        from ..document import validate_mask
        self._result = {'mask':validate_mask(result['mask']), 'quality':deepcopy(result['quality'])}
        self.sessionTimer.start()
        self._note = '正在准备实际黑白底效果与前景颜色…'
        layers = deepcopy(self.state['layers'])
        target = self.state['target_id'] or (self.state['layer_id'] if self.state['candidate'] is None else '')
        if target:
            next(layer for layer in layers if layer['id'] == target)['mask'] = deepcopy(self._result['mask'])
        self.editor._request('channel_preview', mask=self.state['mask'], result_mask=self._result['mask'],
            **({'content_reference':self.state['content_reference']} if self.state['content_reference'] else {}),
            options=self._options, layers=layers, expected_sha256=self.state['sha'], native_views=True, channel_choices=True,
            resolved_channel=self._result['quality']['channel'],
            context={'token':self.state['token'], 'revision':self.state['revision'], 'result_preview':True})
        self.changed.emit()

    def cancelled(self):
        self.sessionTimer.stop()
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
            if self._matches_content(result):self._publish(result)
            else:self.failed('图像内容已变化，结果未应用',{'token':self.state['token'],'revision':self.state['revision']})

    def _matches_content(self,result):
        expected=(self.state.get('content_reference') or {}).get('sha256')
        return result.get('quality',{}).get('content_sha256')==expected

    def _publish(self, result):
        from .layers import applyRangeMask
        from .matting import channel_feedback
        from ..matting.channels import CHANNEL_NAMES
        if not self._valid():
            return
        target = self.state['target_id'] or (self.state['layer_id'] if self.savesCurrentLayer else '')
        try:
            applyRangeMask(self.editor,result['mask'],self.state,target_id=target,
                           name='通道 · '+CHANNEL_NAMES[result['quality']['channel']])
        except ValueError as exc:
            self._loading=False
            self._note=str(exc)
            self.sessionTimer.start()
            self.changed.emit()
            return
        self.close()
        channel_feedback(self.editor,result['quality'],applied=True)

    @Slot()
    def close(self):
        self.timer.stop()
        self.sessionTimer.stop()
        self._release_session()
        token = (self.state or {}).get('token')
        jobs = (self.editor._matte_active,self.editor._matte_pending)
        owned = bool(token and any(job and job.get('channel_token')==token for job in jobs))
        self.state = None
        self._preview = ''
        self._views = {}
        self._choices = []
        self._loading = False
        self._levels, self._suggestions = {}, {}
        self._result, self._draft_views, self._result_views = None, {}, {}
        if owned:
            self.editor.cancelMatte()
        self.changed.emit()
