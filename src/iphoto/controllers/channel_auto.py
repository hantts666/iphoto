"""Conversation selection -> channel proposal -> native neural alpha, one transaction."""
from copy import deepcopy
from uuid import uuid4

from ..document import validate_mask
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
        e._status = '2/3 正在比较通道，准备原图透明度…可取消'
        options = {'channel':'auto','black':0,'white':255,'gamma':1.,'invert':False,'radius':32,
                   'ai':True,'interior':state['interior']}
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
        options = {**result['options'],'interior':state['interior']}
        e._status = '3/3 正在结合通道与 AI 细化原图透明边缘…可取消'
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
        e._pending_request = None
        e._set_candidate(mask)
        if state['bound']:
            e.acceptSelection()
        quality = result['quality']
        names = {'red':'红','green':'绿','blue':'蓝','luminance':'亮度'}
        detail = f"{names[quality['channel']]}通道 + AI 透明边缘 · {quality['elapsed_ms']/1000:.1f}s"
        if quality['warnings']:
            detail += ' · ' + '；'.join(quality['warnings'])
        e._selection_quality = detail
        e._message('assistant',detail + ('\n已更新原层范围，颜色和强度保留；可一步撤销。' if state['bound']
                   else '\n范围已准备好，可以直接调整或继续修边。') + '\n请放大检查细丝、孔洞与透明内部。',
                   state='applied' if state['bound'] else 'draft',origin=pending)
        e._notify('通道与 AI 透明度处理已完成；可撤销或检查边缘')
    except (ValueError, KeyError) as exc:
        e._notify(str(exc), True)


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
