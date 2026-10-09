"""Conversation selection -> channel proposal -> native neural alpha, one transaction."""
from copy import deepcopy
from uuid import uuid4

from ..document import validate_mask
from ..ai_protocol import image_data_url
from ..engine import Recipe
from ..channel_advisor import CONTROLS, validate_tune
from ..matting.channels import CHANNEL_NAMES, validate_options
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
        state['options']=validate_options(options)
        if state.get('hair') and not state.get('tune_attempted'):
            state['tune_attempted']=True
            state['tune_preparing']=True
            e._status='2/4 正在准备原像素通道与采样参照，交给AI选择参数…可取消'
            e._request('channel_evidence',mask=state['mask'],options=options,expected_sha256=e._sha,
                       context={'token':state['token']})
            return
        _extract(e,state)
    except (ValueError, KeyError) as exc:
        e._notify(str(exc), True)


def _extract(e,state):
    e._status='3/4 正在结合通道与 AI 细化原图透明边缘…可取消'
    e._request('matte',method='channel',mask=state['mask'],channel_options=state['options'],auto_token=state['token'])


def tune_ready(e,result,context,generation):
    try:
        current=_current(e,context['token'])
        if current is None or generation!=e._generation:return
        pending,state=current
        if not state.pop('tune_preparing',False):return
        if result['options']!=state['options']:
            raise ValueError('通道参照参数已变化，原范围保留')
        state['tuning']=True
        images=[{'label':item['label'],'url':image_data_url(item['path'],lossless=item['lossless'])}
                for item in result['images']]
        e._status='2/4 AI正在查看原像素通道，选择黑白场与灰度参数…可取消'
        e.changed.emit()
        e.ai.plan(pending['text'],Recipe().to_dict(),[],result['images'][0]['path'],generation,
                  'channel_tune',{'target':state['mask']['label'],'options':state['options'],
                                  'current_options':{key:state['options'][key] for key in CONTROLS},
                                  'channel_suggestions':result['channel_suggestions'],'review_images':images})
    except (ValueError,KeyError,OSError) as exc:e._notify(str(exc),True)


def tuned(e,result):
    try:
        state=(e._pending_request or {}).get('channel_auto')
        if not state or not state.get('tuning'):return
        current=_current(e,state['token'])
        if current is None:return
        _,state=current
        state['tuning']=False
        suggestion=validate_tune({key:result[key] for key in ('status','options','summary')},state['options'])
        if result['status']=='propose':
            state['options']=validate_options({**state['options'],**result['options']})
        state['channel_tuning']=deepcopy(suggestion)
        _extract(e,state)
    except (ValueError,KeyError) as exc:e._notify(str(exc),True)


def complete(e, result, token):
    try:
        current = _current(e, token)
        if current is None:
            return
        pending, state = current
        mask = validate_mask(result['mask'])
        if state.get('channel_tuning') and not state.get('result'):
            result={**result,'quality':{**result['quality'],'channel_tuning':deepcopy(state['channel_tuning'])}}
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
            state['channel_result']=deepcopy(state['result'])
            previous=state['result']['quality']
            result={**result,'quality':{**previous,'hair_refinement':deepcopy(result['quality']),
                                      'warnings':previous['warnings']+result['quality']['warnings'],
                                      'elapsed_ms':round(previous['elapsed_ms']+result['quality']['elapsed_ms'],1)}}
        state['result'] = deepcopy(result)
        if state.get('channel_result') and not state['revision']:
            state['alternatives']={'channel':state.pop('channel_result'),'hair':deepcopy(result)}
            e._status='4/4 正在准备通道与 AI 两份独立抠图，比较相同原图边缘…可取消'
            e._request('matte_candidate',layers=deepcopy(e._layers),expected_sha256=e._sha,
                       comparison_masks={key:value['mask'] for key,value in state['alternatives'].items()},
                       comparison_target=(state['target_id'] or e._selected) if state['bound'] else None,
                       context={'token':token})
            return
        _prepare_review(e,state,token)
    except (ValueError, KeyError, StopIteration) as exc:
        e._notify(str(exc), True)


def _prepare_review(e,state,token):
    mask=validate_mask(state['result']['mask'])
    staged = deepcopy(e._layers)
    if state['bound']:
        target = state['target_id'] or e._selected
        next(layer for layer in staged if layer['id']==target)['mask'] = mask
    e._status = '4/4 正在准备实际透明输出与原像素对照，核对抠图质量…可取消'
    e._request('matte_candidate', mask=mask, layers=staged, expected_sha256=e._sha,
               target_context=bool(state.get('hair')),
               final_review=bool(state['revision']),
               **({'detail_points':state['corrections']} if state['revision'] and state.get('corrections') and state.get('hair') else {}),
               **({'review_boxes':state['review_boxes']} if state['revision'] else {}),
               context={'token':token})


def review_ready(e, result, context, generation):
    try:
        current = _current(e, context['token'])
        if current is None or generation != e._generation:
            return
        pending, state = current
        state['reviewing'] = True
        if result.get('comparison_candidates'):
            if not state.get('alternatives') or state['revision']:
                raise ValueError('抠图比较已过期，原范围保留')
            state['comparing']=True
            images=[{'label':item['label'],'url':image_data_url(item['path'],lossless=item.get('lossless',False))}
                    for item in result['review_images']]
            e._status='4/4 AI 正在比较通道与头发细化的断丝、灰雾和误选…可取消'
            e.changed.emit()
            e.ai.plan(pending['text'],Recipe().to_dict(),[],result['images'][0]['path'],generation,
                      'matte_review',{'target':state['mask']['label'],'comparison_candidates':result['comparison_candidates'],
                                      'edge_count':len(result['boxes']),'review_images':images})
            return
        state['review_boxes'] = deepcopy(result['boxes'])
        state['context_points'] = bool(state.get('hair')) and result.get('context_points') is True
        state['point_bounds'] = deepcopy(result.get('point_bounds')) if state['context_points'] else None
        state['keep_candidates'] = deepcopy(result['keep_candidates'])
        strand_details = result.get('strand_detail_count',0) if state['context_points'] else 0
        from ..segmentation.precise_sam import available
        state['correction_available'] = available() and bool(result['boxes']) and not state['revision']
        images = [{'label':item['label'],'url':image_data_url(item['path'],lossless=item.get('lossless',False))}
                  for item in result['review_images']]
        e._status = '4/4 AI 正在对照原片、透明度与实际抠图，检查误选和灰边…可取消'
        if type(strand_details) is int and 1<=strand_details<=4:
            e._status = '4/4 AI 正在细查原片发丝的卷曲、分叉、灰雾和漏选…可取消'
        e.changed.emit()
        # Keep solver scores and prior verdicts on the local candidate. They
        # are not evidence that the pixels preserve the requested fine detail.
        e.ai.plan(pending['text'], Recipe().to_dict(), [], result['images'][0]['path'], generation,
                  'matte_review', {'target':state['mask']['label'],
                                   'revision':state['revision'],'edge_count':len(result['boxes']),
                                   'keep_candidates':result['keep_candidates'],
                                   'exclude_candidates':result['exclude_candidates'],
                                   'correction_available':state['correction_available'],
                                   'correction_method':'hair' if state.get('hair') else 'semantic',
                                   'visual_exclusions':bool(state.get('hair')),
                                   'strand_points':bool(state.get('hair')),
                                   'point_budget':8 if state.get('hair') else 6,
                                   'context_points':state['context_points'],
                                   'strand_detail_count':strand_details,
                                   'point_bounds':state['point_bounds'],
                                   'review_images':images})
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
        if state.pop('comparing',False):
            from ..matte_compare import CANDIDATES
            if review['status']=='select' and review.get('candidate') in CANDIDATES:
                alternatives=state.pop('alternatives')
                state['result']=deepcopy(alternatives[review['candidate']])
                state['result']['quality']['candidate_comparison']={'selected':review['candidate'],'summary':review['summary']}
                _prepare_review(e,state,state['token'])
                return
            e._pending_request=None
            e._message('assistant','通道与 AI 候选均未选用，原范围保留。\n'+review['summary'],
                       state='unsupported',origin=pending)
            return e._notify('通道与 AI 候选未通过比较，原范围保留')
        if review['status']=='revise':
            from ..matte_review import validate_corrections
            if state['revision'] or not state.get('correction_available'):
                raise ValueError('本轮不能再次纠错，原范围保留')
            corrections=validate_corrections(review['corrections'],len(state['review_boxes']),strand_points=bool(state.get('hair')),bounds=state.get('point_bounds'))
            state['previous_check']=review['summary']
            if any(p[2]==2 for patch in corrections for p in patch['points']):
                state['point_corrections']=deepcopy(corrections)
                state['points_pending']=True
                e._status='4/4 正在放大发丝参照原片，核对 AI 的真实落点…可取消'
                e._request('matte_point_evidence',corrections=deepcopy(corrections),review_boxes=state['review_boxes'],
                           context_points=state.get('context_points',False),expected_sha256=e._sha,context={'token':state['token']})
            else:
                _correct(e,state,corrections)
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
        if quality.get('channel_tuning',{}).get('status')=='propose':
            detail += ' · AI 已依据原像素通道设置黑白场与灰度'
        if quality.get('hair_refinement'):
            detail += ' · 人像外缘与头发分区'
        if quality.get('candidate_comparison'):
            detail += ' · 已比较两份实际抠图，选用'+('通道候选' if quality['candidate_comparison']['selected']=='channel' else '头发细化候选')
        if quality.get('correction'):
            detail += ' · AI 已按实际效果纠错并复查'
            if quality['correction'].get('hair_matting'):
                detail += ' · 人像外缘与头发分区'
            if quality['correction'].get('learned_trimap'):
                detail += ' · AI 细丝区域预测'
        if quality['warnings']:
            detail += ' · ' + '；'.join(quality['warnings'])
        e._selection_quality = detail
        e._message('assistant',detail + '\n效果核对：'+review['summary'] + ('\n已更新原层范围，颜色和强度保留；可一步撤销。' if state['bound']
                   else '\n范围已准备好，可以直接调整或继续修边。') + '\n可切换白底或黑底检查，透明 PNG 使用相同的前景颜色恢复；仍请检查细丝、孔洞与透明内部。',
                   state='applied' if state['bound'] else 'draft',origin=pending)
        e._notify('通道与 AI 透明度处理已完成；可撤销或检查边缘')
    except (ValueError, KeyError) as exc:
        e._notify(str(exc), True)


def _correct(e, state, corrections):
    if state['revision']:
        raise ValueError('本轮不能再次纠错，原范围保留')
    state['revision']=1
    state['corrections']=deepcopy(corrections)
    e._status='4/4 已发现局部误选，AI 正在修正范围后重新检查…可取消'
    e._request('matte',method='correction',mask=state['result']['mask'],
               corrections=deepcopy(corrections),review_boxes=state['review_boxes'],auto_token=state['token'],
               **({'source_point_proposal':deepcopy(state['point_corrections'])}
                  if state.get('point_check') and 'strand_candidates' in state.get('point_workspace',{}) else {}),
               hair=bool(state.get('hair')),context_points=state.get('context_points',False))


def points_ready(e, result, context, generation):
    try:
        current=_current(e,context['token'])
        if current is None or generation!=e._generation:return
        pending,state=current
        if not state.get('points_pending') or state['revision']:return
        state['points_pending']=False;state['points_reviewing']=True
        state['point_workspace']={'correction_method':'hair','revision':0,'edge_count':len(state['review_boxes']),
                                  'point_budget':8,
                                  'corrections':deepcopy(state['point_corrections']),
                                  'context_points':state.get('context_points',False),'point_bounds':result['point_bounds'],
                                  'keep_candidates':state['keep_candidates'],'windows':result['windows']}
        if 'strand_candidates' in result:
            state['point_workspace']['strand_candidates']=deepcopy(result['strand_candidates'])
        if 'strand_structures' in result:
            state['point_workspace']['strand_structures']=deepcopy(result['strand_structures'])
        images=[{'label':item['label'],'url':image_data_url(item['path'],lossless=item.get('lossless',False))}
                for item in result['images']]
        e._status='4/4 AI 正在核对细丝走向和背景误选…可取消'
        e.changed.emit()
        e.ai.plan(pending['text'],Recipe().to_dict(),[],result['images'][0]['path'],generation,
                  'matte_points',{**state['point_workspace'],'review_images':images})
    except (ValueError,KeyError,OSError) as exc:e._notify(str(exc),True)


def points_reviewed(e, result):
    try:
        state=(e._pending_request or {}).get('channel_auto')
        if not state or not state.get('points_reviewing'):return
        current=_current(e,state['token'])
        if current is None:return
        pending,state=current
        state['points_reviewing']=False
        if result['status'] not in ('keep','revise'):
            e._pending_request=None
            e._message('assistant','AI 未能可靠定位细发丝，原范围保留。\n'+result['summary'],state='unsupported',origin=pending)
            return e._notify('AI 未能可靠定位细发丝，原范围保留')
        from ..matte_points import validate_replacement
        corrections=validate_replacement(result['corrections'] if result['status']=='revise' else state['point_corrections'],state['point_workspace'])
        state['point_check']=result['summary']
        _correct(e,state,corrections)
    except (ValueError,KeyError) as exc:e._notify(str(exc),True)


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
    if e.ai.busy:
        e.ai.cancel()
    e._message('assistant','已取消通道抠图，原范围和照片保留。',state='answered',origin=pending)
    e._notify('已取消通道抠图，原范围保留')
