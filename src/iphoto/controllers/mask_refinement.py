"""Close-up AI exclusions followed by one atomic mask/recipe transaction."""
from copy import deepcopy
import json
from uuid import uuid4

from ..ai_mask_refinement import eligible, mask_data_url, map_points, validate_result
from ..ai_mask_review import parse_review, parse_verification
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


def boundary_context(self, mask):
    from .face_inventory import grounding_context
    from ..segmentation import face_precision, precise_sam

    if not eligible(mask) or not face_precision.available() or not precise_sam.available():
        return None
    bounds=raster_mask_cached(mask,(384,384)).getbbox()
    face=grounding_context(self,[(bounds[0]+bounds[2])/768,(bounds[1]+bounds[3])/768]) if bounds else None
    if not face or not face.get('face_features'):
        return None
    return deepcopy({'crop':face['skin_crop'],'features':face['face_features'],'anchor':face['anchor']})


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
                 'draft': draft, 'target_id': lid, 'recipe': deepcopy(plan['recipe']), 'method': plan.get('method','exclude'),
                 'stage': 'points_preparing', 'preparing': True}
        from .face_inventory import grounding_context
        from ..ai_grounding import region_crop

        bounds = raster_mask_cached(mask, (384, 384)).getbbox()
        face = grounding_context(self, [(bounds[0]+bounds[2])/768, (bounds[1]+bounds[3])/768]) if bounds else None
        state['context_crop'] = region_crop(face['mask'], (384, 384)) if face else None
        pending['mask_refinement'] = state
        _current(self, pending)
        if plan.get('method')=='boundary':
            part_context=boundary_context(self,mask)
            if not part_context:
                raise ValueError('缺少可靠的五官边缘修正条件，原范围和参数保留')
            state['summary']='根据面部分区分别保留目标五官，结合原图核对边缘'
            state['stage'],state['preparing']='neural',False
            if pixel_selections.start(self,[{'id':'target','hint':state['mask'],'points':[],
                    'part_boundary':part_context}],{'purpose':'ai_mask_refinement','mask_token':state['token'],
                    'mask_stage':state['stage'],'origin':_origin(pending)}) is False:
                raise ValueError('五官边缘修正未能启动，原范围和参数保留')
            self.changed.emit()
            return
        self._status = '正在从原图准备五官与蒙版对照…可随时取消'
        self.changed.emit()
        if self._request('mask_refinement_crop', mask=state['mask'], source_sha=self._sha,
                         context_crop=state['context_crop'],
                         context={'mask_token': state['token'], 'mask_stage': state['stage']}) is False:
            raise ValueError('原图对照未能准备，已有范围和参数保留')
    except (ValueError, KeyError, StopIteration) as exc:
        failed(self, str(exc))


def crop_ready(self, result, context, generation):
    pending = self._pending_request or {}
    state = pending.get('mask_refinement')
    if (not state or context.get('mask_token') != state['token']
            or context.get('mask_stage') != state['stage']
            or state['stage'] not in ('points_preparing', 'review_preparing', 'verify_preparing')):
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
        review = state['stage'] == 'review_preparing'
        verify = state['stage'] == 'verify_preparing'
        state['stage'] = 'verify' if verify else 'review' if review else 'points'
        self._status = ('AI 正在对比修改前后，核对是否误删真实五官…' if verify else
                       (('AI 正在复查当前范围，检查可明确排除的误选…' if state.get('points_fallback')
                         else 'AI 正在复查修正结果，检查残留误选…') if review
                        else 'AI 正在放大对照五官与蒙版，定位误选部分…'))
        workspace = {'face_part': state['mask']['face_part'], 'crop_size': result['crop_size'],
                     '_mask_path': result['mask_path'], 'selection_image': mask_data_url(result['mask_path']),
                     'selection_overlay': image_data_url(result['overlay_path'])}
        if review:
            workspace['regions'] = result['regions']
        if review or verify:
            if not result.get('reference_path') or not result.get('changes_path'):
                raise ValueError('复查缺少修改前范围与减少覆盖对照，原范围和参数保留')
            workspace['reference_image'] = image_data_url(result['reference_path'])
            workspace['changes_image'] = image_data_url(result['changes_path'])
        if result.get('context_path'):
            workspace['face_context_image'] = image_data_url(result['context_path'])
        text = ('根据原图核对本次减少是否误删原范围里的真实'+state['mask']['face_part']
                if verify else pending['text'])
        if self.ai.plan(text, Recipe().to_dict(), [], result['path'], self._generation,
                        'mask_validate' if verify else 'mask_review' if review else 'mask_points', workspace) is False:
            if self._pending_request is not pending:
                return  # The transport already reported and cleared this request.
            raise ValueError('AI对照未能启动，已有范围和参数保留')
        self.changed.emit()
    except (ValueError, KeyError, OSError) as exc:
        failed(self, str(exc))


def planned(self, result):
    pending = self._pending_request
    try:
        state = _current(self, pending)
        if state['stage'] == 'verify':
            return verified(self, result)
        if state['stage'] == 'review':
            return reviewed(self, result)
        if state['stage'] != 'points':
            return
        if result.get('mode') != 'mask_points':
            raise ValueError('AI未可靠定位到误选区域，已有范围和参数保留。\n'+result['summary'])
        if result['status'] == 'unsupported':
            return review_original(self)
        state['points'] = map_points(result['points'], state['crop']['crop_box'], (self._width, self._height))
        state['summary'] = result['summary']
        state['stage'] = 'neural'
        context = {'purpose': 'ai_mask_refinement', 'mask_token': state['token'],
                   'mask_stage': state['stage'],
                   'origin': _origin(pending), 'points': deepcopy(state['points'])}
        if pixel_selections.start(self, [{'id': 'target', 'hint': state['mask'], 'points': state['points']}], context) is False:
            raise ValueError('精细神经修正未能启动，已有范围和参数保留')
    except (ValueError, KeyError) as exc:
        failed(self, str(exc))


def points_unavailable(self, generation):
    pending = self._pending_request or {}
    state = pending.get('mask_refinement')
    if not state or state['stage'] != 'points' or self._closing:
        return
    if generation != self._generation:
        return failed(self, '照片或范围已变化，过期修正未应用')
    return review_original(self)


def review_original(self):
    """One semantic review of original coverage; never guess rejected points."""
    try:
        state = _current(self, self._pending_request)
        state['points_fallback'] = True
        state['summary'] = '点位未能可靠定位，已改用原图区域复查'
        state['result'] = deepcopy(state['mask'])
        state['quality'] = {'model': '已有五官分区', 'warnings': []}
        state['stage'], state['preparing'] = 'review_preparing', True
        self._status = '点位未能可靠定位，正在准备原图区域复查…可随时取消'
        self.changed.emit()
        if self._request('mask_refinement_crop', mask=state['mask'], source_sha=self._sha, review=True,
                         reference_mask=state['mask'], context_crop=state['context_crop'],
                         context={'mask_token': state['token'], 'mask_stage': state['stage']}) is False:
            raise ValueError('原图区域复查未能准备，已有范围和参数保留')
    except (ValueError, KeyError) as exc:
        failed(self, str(exc))


def complete(self, result, context):
    pending = self._pending_request or {}
    state = pending.get('mask_refinement')
    if (not state or context.get('mask_token') != state['token']
            or context.get('mask_stage') != 'neural' or state['stage'] != 'neural'):
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
            if state.get('method')=='boundary':
                return preserve_original(self,'unchanged')
            raise ValueError('本次修正没有改变范围，已有范围保留')
        state['result'], state['quality'] = mask, deepcopy(item.get('quality', {}))
        state['stage'], state['preparing'] = 'review_preparing', True
        self._status = '正在从原图准备修正结果对照，随后由AI复查残留误选…可随时取消'
        self.changed.emit()
        if self._request('mask_refinement_crop', mask=mask, source_sha=self._sha, review=True,
                         reference_mask=state['mask'], context_crop=state['context_crop'],
                         context={'mask_token': state['token'], 'mask_stage': state['stage']}) is False:
            raise ValueError('修正结果复查未能准备，已有范围和参数保留')
    except (ValueError, KeyError, StopIteration) as exc:
        failed(self, str(exc))


def reviewed(self, result):
    pending = self._pending_request
    try:
        state = _current(self, pending)
        if result.get('mode') != 'mask_review':
            raise ValueError('范围复查返回类型不一致，已有范围与参数保留')
        plan = {key: result[key] for key in ('status', 'summary', 'exclude_regions')}
        plan = parse_review({'choices': [{'finish_reason': 'stop', 'message': {'content': json.dumps(plan)}}]},
                            {'regions': state['crop']['regions']})
        part = '嘴唇' if state['mask']['face_part'] == 'lips' else '鼻部'
        if plan['status'] in ('reject', 'uncertain'):
            if state['result'] == state['mask']:
                return preserve_original(self, plan['status'])
            # The mixed region task can mistake edge loss for removal of the
            # whole subject. Let the sole quality task inspect the unchanged
            # staged candidate before deciding; never publish this decision.
            state['review_summary'] = f'残留未能可靠判明，未作整片清理；{part}边缘与漏选仍需检查'
            return start_verification(self)
        state['review_summary'] = {
            'keep': f'保留现有覆盖；{part}边缘与漏选仍需放大检查',
            'remove': f'已排除AI复查指出的残留范围；{part}边缘与漏选仍需放大检查',
        }[plan['status']]
        if plan['status'] != 'remove':
            return start_verification(self)
        state['stage'], state['preparing'] = 'apply', True
        self._status = '正在清理AI复查发现的误选，其余范围保留…可随时取消'
        self.changed.emit()
        if self._request('mask_refinement_apply', mask=state['result'], source_sha=self._sha,
                         reference_mask=state['mask'],
                         exclude_regions=plan['exclude_regions'], regions=state['crop']['regions'],
                         context={'mask_token': state['token'], 'mask_stage': state['stage']}) is False:
            raise ValueError('复查排除未能完成，已有范围和参数保留')
    except (ValueError, KeyError) as exc:
        failed(self, str(exc))


def applied_regions(self, result, context, generation):
    pending = self._pending_request or {}
    state = pending.get('mask_refinement')
    if (not state or context.get('mask_token') != state['token']
            or context.get('mask_stage') != 'apply' or state['stage'] != 'apply'):
        return
    try:
        state = _current(self, pending)
        if generation != self._generation:
            raise ValueError('范围复查结果已过期，已有范围和参数保留')
        state['result'] = validate_result(result['mask'], state['mask'], (self._width, self._height))
        if not raster_mask_cached(state['result'], (512, 512)).getbbox():
            raise ValueError('范围复查结果为空，已有范围和参数保留')
        state['quality'].setdefault('warnings', []).append('已按AI原图复查判断排除残留区域，其余覆盖保持')
        start_verification(self)
    except (ValueError, KeyError) as exc:
        failed(self, str(exc))


def preserve_original(self, status):
    pending = self._pending_request
    state = _current(self, pending)
    part = '嘴唇' if state['mask']['face_part'] == 'lips' else '鼻部'
    output = ('本次边缘核对未产生范围修改，原范围与参数保持' if status=='unchanged' else
              (f'AI复查发现可能误删真实{part}' if status == 'reject'
               else f'AI复查未确认本次修正保留了真实{part}')+'，原范围与参数保持')
    self._pending_request = None
    self._message('assistant', output+'。请放大检查范围边缘。', state='answered', origin=_origin(pending))
    self._notify(output)


def start_verification(self):
    try:
        state = _current(self, self._pending_request)
        if state['result'] == state['mask']:
            return publish(self)
        state['stage'], state['preparing'] = 'verify_preparing', True
        self._status = '正在准备修改前后对照，核对是否误删真实五官…可随时取消'
        self.changed.emit()
        if self._request('mask_refinement_crop', mask=state['result'], source_sha=self._sha, verify=True,
                         reference_mask=state['mask'], context_crop=state['context_crop'],
                         context={'mask_token': state['token'], 'mask_stage': state['stage']}) is False:
            raise ValueError('修改前后核对未能准备，原范围与参数保留')
    except (ValueError, KeyError) as exc:
        failed(self, str(exc))


def verified(self, result):
    try:
        state = _current(self, self._pending_request)
        if result.get('mode') != 'mask_validate':
            raise ValueError('修改前后核对返回类型不一致，原范围与参数保留')
        plan = parse_verification({'choices': [{'finish_reason': 'stop', 'message': {
            'content': json.dumps({key: result[key] for key in ('status', 'summary')})}}]})
        if plan['status'] != 'accept':
            return preserve_original(self, plan['status'])
        state['verified'] = True
        publish(self)
    except (ValueError, KeyError) as exc:
        failed(self, str(exc))


def publish(self):
    pending = self._pending_request
    try:
        state = _current(self, pending)
        mask = state['result']
        if mask != state['mask'] and not state.get('verified'):
            raise ValueError('范围减少尚未通过修改前后核对，原范围与参数保留')
        lid = state['target_id']
        if state.get('points_fallback') and mask == state['mask']:
            output = 'AI复查未确定可安全排除的误选，原范围与参数保持'
            self._pending_request = None
            self._message('assistant', output+'。\n'+state['summary']+'\n'+state['review_summary'],
                          state='answered', origin=_origin(pending))
            self._notify(output)
            return
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
        if mask==state['mask'] and state['recipe'] is not None:
            output='已建立独立调整层，范围保持' if state['draft'] and not state['target_id'] else '已调整已有层参数，范围保持'
        quality = state['quality']
        warnings = [warning.replace('请补充提示点', '请放大检查边缘')
                    for warning in quality.get('warnings', [])]
        if lid:
            from .conversation import _layer_result_notes
            warnings += _layer_result_notes(self._layers, [{'layer_id': lid}])
        self._selection_quality = self._quality_text(mask)+' · '+quality.get('model', '本地神经修正')+' · AI视觉复查'
        origin = {**_origin(pending), **({'layer_id': lid, 'adjustment_layer_ids': [lid]} if lid else {})}
        self._pending_request = None
        self._message('assistant', output+'。\n'+state['summary']+'\nAI复查：'+state['review_summary']+'\n请放大检查边缘；可一步撤销。'
                      + ('\n'+'；'.join(warnings) if warnings else ''), state=message_state, origin=origin)
        self._notify(output+'；请检查边缘，可一步撤销', scope='draft' if message_state == 'draft' else '')
    except (ValueError, KeyError, StopIteration) as exc:
        failed(self, str(exc))


def failed(self, message, context=None):
    pending = self._pending_request or {}
    if context:
        state = pending.get('mask_refinement', {})
        if (context.get('mask_token') != state.get('token')
                or context.get('mask_stage') != state.get('stage')):
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
