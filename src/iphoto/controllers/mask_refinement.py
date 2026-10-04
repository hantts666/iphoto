"""Close-up AI exclusions followed by one atomic mask/recipe transaction."""
from copy import deepcopy
from uuid import uuid4

from ..ai_mask_refinement import eligible, mask_data_url, map_points, validate_result
from ..ai_protocol import image_data_url
from ..document import new_layer, raster_mask_cached, validate_layers
from ..engine import Recipe
from . import layers, pixel_selections


def _origin(pending):
    return {k: deepcopy(pending[k]) for k in ('mode', 'text', 'model', 'layer_id', 'layer_name',
            'request_user_id', 'request_binding') if k in pending}


def _current(self, pending):
    state = pending['mask_refinement']
    content = lambda records: [{k: v for k, v in layer.items() if k != 'collapsed'} for layer in records]
    if (state['source_sha'] != self._sha or state['generation'] != self._generation
            or pending['binding'] != self._document_signature()
            or content(pending['layer_snapshot']) != content(self._layers)
            or state['candidate'] != self._candidate or state['draft_target'] != self._selection_target_id):
        raise ValueError('照片、图层或范围已变化，过期精细修正未应用')
    return state


def begin(self, result):
    pending = self._pending_request
    try:
        if pending['binding'] != self._document_signature():
            raise ValueError('照片中的图层已变化，过期范围修正未应用')
        plan = result['mask_refinement']
        draft = result['scope'] == 'current_selection'
        if draft:
            mask, lid = self._candidate, self._selection_target_id
            snapshot = pending.get('selection_snapshot') or {}
            if snapshot.get('mask') != mask or snapshot.get('target_id') != lid:
                raise ValueError('当前范围已变化，过期范围修正未应用')
        else:
            lid = plan['layer_id']
            mask = next(layer for layer in self._layers if layer['id'] == lid)['mask']
        if lid:
            target = next(layer for layer in self._layers if layer['id'] == lid)
            if target['kind'] != 'adjustment' or target.get('heal') or target.get('inpaint'):
                raise ValueError('此图层不能进行五官范围修正')
        if not eligible(mask):
            raise ValueError('当前目标不是明确的鼻部或嘴唇范围，照片未改变')
        state = {'token': uuid4().hex, 'mask': deepcopy(mask), 'source_sha': self._sha, 'generation': self._generation,
                 'candidate': deepcopy(self._candidate), 'draft_target': self._selection_target_id,
                 'draft': draft, 'target_id': lid, 'recipe': deepcopy(plan['recipe']), 'preparing': True}
        pending['mask_refinement'] = state
        _current(self, pending)
        self._status = '正在从原图准备五官与蒙版对照…可随时取消'
        self.changed.emit()
        if self._request('mask_refinement_crop', mask=state['mask'], source_sha=self._sha,
                         context={'mask_token': state['token']}) is False:
            raise ValueError('原图对照未能准备，已有范围和参数保留')
    except (ValueError, KeyError, StopIteration) as exc:
        failed(self, str(exc))


def crop_ready(self, result, context, generation):
    pending = self._pending_request or {}
    state = pending.get('mask_refinement')
    if not state or context.get('mask_token') != state['token']:
        return
    try:
        state = _current(self, pending)
        if generation != self._generation or result['source_size'] != [self._width, self._height]:
            raise ValueError('原图尺寸或范围已变化，过期修正未应用')
        left, top, right, bottom = result['crop_box']
        if (any(type(value) is not int for value in result['crop_box'])
                or not 0 <= left < right <= self._width or not 0 <= top < bottom <= self._height
                or result['crop_size'] != [right-left, bottom-top]):
            raise ValueError('五官对照尺寸无效，已有范围保留')
        state['crop'] = deepcopy(result)
        state['preparing'] = False
        self._status = 'AI 正在放大对照五官与蒙版，定位误选部分…'
        workspace = {'face_part': state['mask']['face_part'], 'crop_size': result['crop_size'],
                     '_mask_path': result['mask_path'], 'selection_image': mask_data_url(result['mask_path']),
                     'selection_overlay': image_data_url(result['overlay_path'])}
        if self.ai.plan(pending['text'], Recipe().to_dict(), [], result['path'], self._generation,
                        'mask_points', workspace) is False:
            raise ValueError('AI对照未能启动，已有范围和参数保留')
        self.changed.emit()
    except (ValueError, KeyError, OSError) as exc:
        failed(self, str(exc))


def planned(self, result):
    pending = self._pending_request
    try:
        state = _current(self, pending)
        if result.get('mode') != 'mask_points' or result['status'] == 'unsupported':
            raise ValueError('AI未可靠定位到误选区域，已有范围和参数保留。\n'+result['summary'])
        state['points'] = map_points(result['points'], state['crop']['crop_box'], (self._width, self._height))
        state['summary'] = result['summary']
        context = {'purpose': 'ai_mask_refinement', 'mask_token': state['token'],
                   'origin': _origin(pending), 'points': deepcopy(state['points'])}
        if pixel_selections.start(self, [{'id': 'target', 'hint': state['mask'], 'points': state['points']}], context) is False:
            raise ValueError('精细神经修正未能启动，已有范围和参数保留')
    except (ValueError, KeyError) as exc:
        failed(self, str(exc))


def complete(self, result, context):
    pending = self._pending_request or {}
    state = pending.get('mask_refinement')
    if not state or context.get('mask_token') != state['token']:
        return
    try:
        state = _current(self, pending)
        if not isinstance(result.get('items'), list) or len(result['items']) != 1 or result['items'][0]['id'] != 'target':
            raise ValueError('精细范围结果不完整，已有范围保留')
        item = result['items'][0]
        mask = validate_result(item['mask'], state['mask'], (self._width, self._height))
        if not raster_mask_cached(mask, (512, 512)).getbbox():
            raise ValueError('精细修正结果为空，已有范围保留')
        if mask == state['mask'] and state['recipe'] is None:
            raise ValueError('本次修正没有改变范围，已有范围保留')
        lid = state['target_id']
        if state['draft'] and not lid:
            if state['recipe'] is None:
                self._set_candidate(mask)
                message_state, output = 'draft', '已修正当前范围，原有图层与颜色参数保留'
            else:
                proposed = new_layer('AI 五官调整')
                proposed['mask'], proposed['recipe'] = mask, state['recipe']
                layers.addLocalLayers(self, [proposed], consume_selection=True, selection_state='confirmed')
                lid = proposed['id']; message_state, output = 'applied', '已修正范围并建立独立调整层'
        else:
            target = next(layer for layer in self._layers if layer['id'] == lid)
            recipe = state['recipe'] or target['recipe']
            proposed = deepcopy(target)
            proposed['mask'], proposed['recipe'] = mask, Recipe.from_dict(recipe).to_dict()
            for key in target['locked']:
                proposed['recipe'][key] = target['recipe'][key]
            validate_layers([proposed if layer['id'] == lid else layer for layer in self._layers])
            previous_recipe = dict(target['recipe'])
            self._commit()
            target['mask'], target['recipe'] = proposed['mask'], proposed['recipe']
            if state['draft']:
                layers._consume_selection(self, 'confirmed')
            self._selected = lid
            self._load_layer(); self._commit(); self._change()
            self.selection.showAppliedResult(lid)
            self.selection.focusChangedParameters(previous_recipe)
            message_state, output = 'applied', '已修正已有图层的范围'+('并调整参数' if state['recipe'] is not None else '，颜色参数保持')
        quality = item.get('quality', {})
        warnings = [warning.replace('请补充提示点', '请放大检查边缘')
                    for warning in quality.get('warnings', [])]
        if lid:
            from .conversation import _layer_result_notes
            warnings += _layer_result_notes(self._layers, [{'layer_id': lid}])
        self._selection_quality = self._quality_text(mask)+' · '+quality.get('model', '本地神经修正')
        origin = {**_origin(pending), **({'layer_id': lid, 'adjustment_layer_ids': [lid]} if lid else {})}
        self._pending_request = None
        self._message('assistant', output+'。\n'+state['summary']+'\n请放大检查边缘；可一步撤销。'
                      + ('\n'+'；'.join(warnings) if warnings else ''), state=message_state, origin=origin)
        self._notify(output+'；请检查边缘，可一步撤销', scope='draft' if message_state == 'draft' else '')
    except (ValueError, KeyError, StopIteration) as exc:
        failed(self, str(exc))


def failed(self, message, context=None):
    pending = self._pending_request or {}
    if context and context.get('mask_token') != pending.get('mask_refinement', {}).get('token'):
        return
    self._pending_request = None
    self._message('error', message, state='failed', origin=_origin(pending))
    self._notify(message, True)


def cancel(self):
    pending = self._pending_request or {}
    state = pending.get('mask_refinement')
    if not state:
        return
    if self.ai.busy:
        self.ai.cancel()
        return
    token = state['token']
    self._queue = type(self._queue)(job for job in self._queue if job.get('context', {}).get('mask_token') != token)
    self._pixel_queue = type(self._pixel_queue)(job for job in self._pixel_queue if job.get('context', {}).get('mask_token') != token)
    if self._active and self._active.get('context', {}).get('mask_token') == token:
        self._active['cancelled'] = True
    if self._pixel_active and self._pixel_active.get('context', {}).get('mask_token') == token:
        self._stop_pixel()
    self._pending_request = None
    message = '已取消精细范围修正，已有范围和参数保留'
    self._message('assistant', message, state='answered', origin=_origin(pending))
    self._notify(message)
