"""Conversation selection -> channel proposal -> native neural alpha, one transaction."""
from copy import deepcopy
from uuid import uuid4

from ..document import validate_mask
from ..ai_protocol import image_data_url
from ..engine import Recipe
from ..matting.channels import CHANNEL_NAMES
from . import pixel_selections


def _current(e, token):
    pending = e._pending_request or {}
    state = pending.get('channel_auto')
    if not state or state['token'] != token:
        return None
    if (state['generation'] != e._generation or pending['binding'] != e._document_signature()
            or pending['layer_snapshot'] != e._layers or state['candidate'] != e._candidate
            or state['target_id'] != e._selection_target_id):
        raise ValueError('照片、图层或范围已变化，通道结果未应用')
    return pending, state


def begin(e, result):
    pending = e._pending_request
    try:
        if pending['binding'] != e._document_signature():
            raise ValueError('照片已变化，通道抠图未启动')
        state = {'token':uuid4().hex, 'generation':e._generation, 'candidate':deepcopy(e._candidate),
                 'revision':0,
                 'hair':result.get('strategy')=='hair_matte',
                 'target_id':e._selection_target_id, 'bound':result['scope']=='current_layer' or bool(e._selection_target_id),
                 'summary':result['summary'], 'interior':any(word in pending['text'] for word in ('薄纱','纱布','玻璃','透明内部','细枝','树枝','针叶','镂空','孔洞'))}
        pending['channel_auto'] = state
        if result['scope'] == 'regions':
            return {**result, 'action':'layers'}
        if state['bound'] and (e._layer().get('heal') or e._layer().get('inpaint') or e._layer().get('pixel_patch')):
            raise ValueError('内容图层不能直接替换作用范围，请使用独立选区')
        prepare(e, e._candidate or e._layer()['mask'], state['token'])
    except (ValueError, KeyError) as exc:
        e._notify(str(exc), True)


def prepare(e, mask, token):
    try:
        current = _current(e, token)
        if current is None:
            return
        _, state = current
        state['mask'] = deepcopy(mask)
        e._status = '2/4 正在比较通道与通道计算，准备原图透明度…可取消'
        options = {'channel':'auto','black':0,'white':255,'gamma':1.,'invert':False,'radius':32,
                   'ai':True,'interior':state['interior'],'detail':True,'color':True}
        e._request('channel_preview',mask=state['mask'],options=options,initial=True,expected_sha256=e._sha,
                   context={'channel_auto':True,'token':token})
    except (ValueError, KeyError) as exc:
        e._notify(str(exc), True)


def ready(e, result, context, generation):
    try:
        current = _current(e, context['token'])
        if current is None or generation != e._generation:
            return
        _, state = current
        options = {**result['options'],'interior':state['interior'],'detail':True,'color':True}
        e._status = '3/4 正在结合通道与 AI 细化原图透明边缘…可取消'
        e._request('matte',method='channel',mask=state['mask'],channel_options=options,auto_token=state['token'])
    except (ValueError, KeyError) as exc:
        e._notify(str(exc), True)


def complete(e, result, token):
    try:
        current = _current(e, token)
        if current is None:
            return
        pending, state = current
        mask = validate_mask(result['mask'])
        if state.get('hair') and not state.get('hair_prepared'):
            state['hair_prepared']=True
            state['result']=deepcopy(result)
            e._status='3/4 正在结合人物外缘与头发分区处理原像素边缘…可取消'
            e._request('matte',method='hair',mask=mask,auto_token=state['token'])
            return
        if state['revision']:
            previous=state['result']['quality']
            result={**result,'quality':{**previous,'correction':deepcopy(result['quality']),
                                      'warnings':previous['warnings']+result['quality']['warnings'],
                                      'elapsed_ms':round(previous['elapsed_ms']+result['quality']['elapsed_ms'],1)}}
        elif result['quality'].get('edge_refinement'):
            previous=state['result']['quality']
            result={**result,'quality':{**previous,'hair_refinement':deepcopy(result['quality']),
                                      'warnings':previous['warnings']+result['quality']['warnings'],
                                      'elapsed_ms':round(previous['elapsed_ms']+result['quality']['elapsed_ms'],1)}}
        state['result'] = deepcopy(result)
        staged = deepcopy(e._layers)
        if state['bound']:
            target = state['target_id'] or e._selected
            next(layer for layer in staged if layer['id']==target)['mask'] = mask
        e._status = '4/4 正在准备实际透明输出与原像素对照，核对抠图质量…可取消'
        e._request('matte_candidate', mask=mask, layers=staged, expected_sha256=e._sha,
                   target_context=bool(state.get('hair')),
                   **({'review_boxes':state['review_boxes']} if state['revision'] else {}),
                   context={'token':token})
    except (ValueError, KeyError, StopIteration) as exc:
        e._notify(str(exc), True)


def review_ready(e, result, context, generation):
    try:
        current = _current(e, context['token'])
        if current is None or generation != e._generation:
            return
        pending, state = current
        state['reviewing'] = True
        state['review_boxes'] = deepcopy(result['boxes'])
        from ..segmentation.precise_sam import available
        state['correction_available'] = available() and bool(result['boxes']) and not state['revision']
        images = [{'label':item['label'],'url':image_data_url(item['path'])} for item in result['review_images']]
        e._status = '4/4 AI 正在对照原片、透明度与实际抠图，检查误选和灰边…可取消'
        e.changed.emit()
        e.ai.plan(pending['text'], Recipe().to_dict(), [], result['images'][0]['path'], generation,
                  'matte_review', {'target':state['mask']['label'],
                                   'revision':state['revision'],'edge_count':len(result['boxes']),
                                   'keep_candidates':result['keep_candidates'],
                                   'exclude_candidates':result['exclude_candidates'],
                                   'correction_available':state['correction_available'],
                                   'correction_method':'hair' if state.get('hair') else 'semantic',
                                   'visual_exclusions':bool(state.get('hair')),
                                   'strand_points':bool(state.get('hair')),
                                   'previous_check':state.get('previous_check',''),
                                   'quality':state['result']['quality'],'review_images':images})
    except (ValueError, KeyError, OSError) as exc:
        e._notify(str(exc), True)


def reviewed(e, review):
    try:
        state = (e._pending_request or {}).get('channel_auto')
        if not state or not state.get('reviewing'):
            return
        current = _current(e,state['token'])
        if current is None:
            return
        pending,state = current
        state['reviewing'] = False
        if review['status']=='revise':
            from ..matte_review import validate_corrections
            if state['revision'] or not state.get('correction_available'):
                raise ValueError('本轮不能再次纠错，原范围保留')
            corrections=validate_corrections(review['corrections'],len(state['review_boxes']),strand_points=bool(state.get('hair')))
            state['revision']=1
            state['previous_check']=review['summary']
            e._status='4/4 已发现局部误选，AI 正在修正范围后重新检查…可取消'
            e._request('matte',method='correction',mask=state['result']['mask'],
                       corrections=deepcopy(corrections),review_boxes=state['review_boxes'],auto_token=state['token'],hair=bool(state.get('hair')))
            return
        if review['status'] != 'accept':
            e._pending_request = None
            e._message('assistant','候选抠图未通过实际效果检查，原范围保留。\n'+review['summary'],
                       state='unsupported',origin=pending)
            return e._notify('候选抠图未通过质量检查，原范围保留')
        result=state['result']
        mask=validate_mask(result['mask'])
        e._pending_request = None
        e._set_candidate(mask)
        if state['bound']:
            e.acceptSelection()
        quality = result['quality']
        detail = f"{CHANNEL_NAMES[quality['channel']]}通道 + AI 透明边缘 · {quality['elapsed_ms']/1000:.1f}s"
        if quality.get('native_detail'):
            detail += ' · 原像素细纹理'
        if quality.get('color_recovery'):
            detail += ' · 透明输出去背景串色'
        if quality.get('hair_refinement'):
            detail += ' · 人像外缘与头发分区'
        if quality.get('correction'):
            detail += ' · AI 已按实际效果纠错并复查'
            if quality['correction'].get('hair_matting'):
                detail += ' · 人像外缘与头发分区'
        if quality['warnings']:
            detail += ' · ' + '；'.join(quality['warnings'])
        e._selection_quality = detail
        e._message('assistant',detail + '\n效果核对：'+review['summary'] + ('\n已更新原层范围，颜色和强度保留；可一步撤销。' if state['bound']
                   else '\n范围已准备好，可以直接调整或继续修边。') + '\n可切换白底或黑底检查，透明 PNG 使用相同的前景颜色恢复；仍请检查细丝、孔洞与透明内部。',
                   state='applied' if state['bound'] else 'draft',origin=pending)
        e._notify('通道与 AI 透明度处理已完成；可撤销或检查边缘')
    except (ValueError, KeyError) as exc:
        e._notify(str(exc), True)


def discard(e, token):
    state = (e._pending_request or {}).get('channel_auto',{})
    if state.get('token') == token:
        e._notify('照片或范围已变化，过期通道结果未应用；原范围保留', True)


def cancel(e):
    pixel_selections.cancel(e)
    e._stop_matte()
    state = (e._pending_request or {}).get('channel_auto', {})
    token = state.get('token')
    e._queue = type(e._queue)(r for r in e._queue if r.get('context',{}).get('token') != token)
    if e._active and e._active.get('context',{}).get('token') == token:
        e._active['cancelled'] = True
    pending, e._pending_request = e._pending_request, None
    e._message('assistant','已取消通道抠图，原范围和照片保留。',state='answered',origin=pending)
    e._notify('已取消通道抠图，原范围保留')
