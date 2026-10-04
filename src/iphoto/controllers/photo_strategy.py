"""Stage, render, inspect and atomically publish a photographic development."""

from copy import deepcopy
from uuid import uuid4

from ..document import new_layer, MAX_LAYERS
from ..engine import Recipe
from ..ai_protocol import image_data_url
from . import layers, pixel_selections


def _current(self, token=None):
    pending = self._pending_request or {}
    state = pending.get('photo_strategy')
    if not state or token is not None and state['token'] != token:
        return None
    if (pending['binding'] != self._document_signature()
            or state['generation'] != self._generation
            or pending['layer_snapshot'] != self._layers
            or pending.get('selection_snapshot') and (pending['selection_snapshot']['mask'] != self._candidate
                or pending['selection_snapshot']['target_id'] != self._selection_target_id)):
        raise ValueError('照片或图层已变化，成片未应用')
    return pending, state


def begin(self, result):
    pending = self._pending_request
    if pending.get('selection_snapshot') or len(self._layers) + 1 + len(result['regions']) > MAX_LAYERS:
        return self._notify('当前范围或图层位置不支持整体成片，照片未改变', True)
    if pending['binding'] != self._document_signature():
        return self._notify('照片或图层已变化，成片未应用', True)
    base = new_layer('整体光色', True)
    base['recipe'] = result['recipe']
    pending['photo_strategy'] = {'token': uuid4().hex, 'generation': self._generation,
                                 'base': base, 'summary': result['summary'],
                                 'direction': result['strategy'], 'revision': 0}
    self._status = '1/4 成片方向：' + result['strategy']
    self.changed.emit()
    if not result['regions']:
        prepare(self, [], pending)
        return None
    return {**result, 'action': 'layers'}


def prepare(self, proposed, origin):
    try:
        current = _current(self, origin['photo_strategy']['token'])
        if current is None:
            return
        pending, state = current
        state['proposed'] = ([state['base']] if 'base' in state else []) + deepcopy(proposed)
        if state.get('generate'):
            state['preparing'] = True
            self._status = '正在准备选区与周围光色，随后生成局部精修…可取消'
            self._request('generative_crop', before=pending['layer_snapshot'], proposed=state['proposed'],
                          soften=not bool(pending.get('selection_snapshot')),
                          expected_sha256=self._sha, context={'token': state['token']})
            self.changed.emit()
            return
        _render(self, pending, state, soften=True)
    except (ValueError, KeyError, OSError) as exc:
        self._notify(str(exc), True)


def _render(self, pending, state, soften=False):
    state['preparing'] = True
    self._status = ('2/4 正在合成选区精修，核对边界与原图细节…可取消' if state.get('generate')
                    else '2/4 正在合成整体光色和局部精修，检查原图细节…可取消')
    self._request('photo_candidate', before=pending['layer_snapshot'], proposed=state['proposed'],
                  faces=deepcopy(self._face_hints[:3]), soften=soften,
                  expected_sha256=self._sha, context={'token': state['token']})
    self.changed.emit()


def ready(self, result, context, generation):
    try:
        current = _current(self, context['token'])
        if current is None or generation != self._generation:
            return
        pending, state = current
        state['proposed'] = result['proposed']
        state['preparing'] = False
        state['reviewing'] = True
        images = [{**item, 'url': image_data_url(item['path'])} for item in result['images']]
        self._status = '3/4 正在对比成片与放大人脸，检查面具边界、色差和皮肤质感…可取消'
        self.changed.emit()
        self.ai.plan(pending['text'], Recipe().to_dict(), [], images[0]['path'], generation, 'photo_review',
                     {'direction': state['direction'], 'revision': state['revision'],
                      'candidates': [{'layer_id': layer['id'], 'name': layer['name'], 'recipe': layer['recipe'],
                                      'whole_image': 'base' in state and index == 0} for index, layer in enumerate(state['proposed'])],
                      'review_images': [{'label': item['label'], 'url': item['url']} for item in images]})
    except (ValueError, KeyError, OSError) as exc:
        self._notify(str(exc), True)


def reviewed(self, result):
    try:
        current = _current(self)
        if current is None:
            return
        pending, state = current
        if result['status'] == 'revise' and state['revision'] == 0:
            by_id = {layer['id']: layer for layer in state['proposed']}
            for edit in result['edits']:
                by_id[edit['layer_id']]['recipe'] = edit['recipe']
            state['revision'] = 1
            state['reviewing'] = False
            state['review_note'] = result['summary']
            return _render(self, pending, state)
        self._pending_request = None
        if result['status'] != 'accept':
            self._message('assistant', '这次候选效果未通过成片检查，照片未改变。\n' + result['summary'],
                          state='unsupported', origin=pending)
            return self._notify('候选效果未通过检查，原照片保留')
        # Validation happened while the full transaction was still busy. Release
        # it only here so the existing layer gate can commit all layers once.
        applied = layers.addLocalLayers(self, state['proposed'],
                                       consume_selection=bool(pending.get('selection_snapshot')),
                                       selection_state='confirmed', allow_pixel_patch=state.get('generate', False))
        origin = {**pending, 'layer_id': self._selected, 'layer_name': '成片精修',
                  'adjustment_layer_ids': [layer['id'] for layer in applied]}
        saved = ('\n已保存选区精修，范围外保持；可一步撤销、调强度或查看原图对比。' if state.get('generate')
                 else '\n已保存整体光色与局部精修，可一步撤销、调强度或查看原图对比。')
        self._message('assistant', state['direction'] + '\n成片检查：' + result['summary'] + saved,
                      state='applied', origin=origin)
        self._notify('4/4 成片已应用；可对比原图或一步撤销')
    except (ValueError, KeyError, OSError) as exc:
        self._notify(str(exc), True)


def cancel(self):
    pending = self._pending_request or {}
    state = pending.get('photo_strategy')
    if not state:
        return
    if self._image_edit.busy:
        return self._image_edit.cancel()
    if self.ai.busy:
        state['cancel_requested'] = True
        self.ai.cancel()
        return
    pixel_selections.cancel(self)
    self._queue = type(self._queue)(request for request in self._queue
                                    if request.get('context', {}).get('token') != state['token'])
    if self._active and self._active.get('context', {}).get('token') == state['token']:
        self._active['cancelled'] = True
    cancelled(self)


def cancelled(self):
    pending = self._pending_request
    self._pending_request = None
    self._message('assistant', '已取消本次成片，照片未改变。', state='answered', origin=pending)
    self._notify('已取消本次成片，照片未改变')


def begin_generate(self, result):
    pending = self._pending_request
    try:
        if len(self._layers) >= MAX_LAYERS or pending['binding'] != self._document_signature():
            raise ValueError('照片、图层或位置已变化，选区图像编辑未应用')
        pending['photo_strategy'] = {'token': uuid4().hex, 'generation': self._generation,
                                     'summary': result['summary'], 'direction': result['edit_prompt'],
                                     'revision': 0, 'generate': True}
        if result['scope'] != 'regions':
            layer = new_layer('AI 选区精修')
            layer['mask'] = deepcopy(pending.get('selection_snapshot', {}).get('mask') or self._layer()['mask'])
            prepare(self, [layer], pending)
            return None
        return {**result, 'action': 'layers'}
    except (ValueError, KeyError) as exc:
        self._notify(str(exc), True)


def generative_ready(self, result, context, generation):
    try:
        current = _current(self, context['token'])
        if current is None or generation != self._generation:
            return
        pending, state = current
        state.update(proposed=result['proposed'], box=result['box'], preparing=False,
                     canvas_size=result['canvas_size'])
        self._image_edit.start(image_data_url(result['path']), state['direction'], result['output_size'],
                               state['token'], generation)
    except (ValueError, KeyError, OSError) as exc:
        self._notify(str(exc), True)


def generated(self, image, token, generation):
    from ..pixel_patch import encode_patch
    try:
        current = _current(self, token)
        if current is None or generation != self._generation:
            return
        pending, state = current
        state['proposed'][0]['pixel_patch'] = encode_patch(image, state['canvas_size'], state['box'])
        _render(self, pending, state)
    except (ValueError, KeyError, OSError) as exc:
        self._notify(str(exc), True)
