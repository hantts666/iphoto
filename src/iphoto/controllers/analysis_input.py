"""Bind spatial AI and pixel jobs to the same prepared, ungraded content."""
from copy import deepcopy
from hashlib import sha256
from pathlib import Path
from uuid import uuid4

from ..analysis_content import key

CROP_OPERATIONS={'object_crop','repair_crop','mask_refinement_crop','mask_refinement_apply'}


def invalidate(e):
    current=key(getattr(e,'_layers',[]))
    if current==getattr(e,'_analysis_key',''):return
    e._analysis_key=current
    e._analysis_input=None
    e._analysis_waiters=[]
    e._analysis_token=None
    e._scene.set(None)
    e._face_hints=[] if current else deepcopy(getattr(e,'_source_face_hints',[]))
    e._stop_warm()
    e._warm_ready_sha=''
    e._pixel_queue=type(e._pixel_queue)(request for request in e._pixel_queue if request.get('priority')!='low')
    if e._pixel_active and e._pixel_active.get('priority')=='low':e._stop_pixel()


def _usable(value):
    if not value:return False
    try:
        path=Path(value['path'])
        if path.stat().st_size>16*1024*1024 or sha256(path.read_bytes()).hexdigest()!=value['proxy_sha256']:return False
        ref=value['content_reference'];snapshot=Path(ref['path'])
        return snapshot.stat().st_size<=128*1024*1024 and sha256(snapshot.read_bytes()).hexdigest()==ref['sha256']
    except (OSError,KeyError,TypeError):return False


def defer(e,kind,data):
    """Return False only for the original-content fast path."""
    identity=key(getattr(e,'_layers',[]))
    if not identity:return False
    invalidate(e)
    bound=(e._sha,identity)
    prepared=getattr(e,'_analysis_input',None)
    if prepared and prepared['binding']==bound and _usable(prepared):
        _dispatch(e,kind,data,prepared)
        return True
    waiters=getattr(e,'_analysis_waiters',[])
    waiters.append({'kind':kind,'data':deepcopy(data),'generation':e._generation,'binding':bound})
    e._analysis_waiters=waiters
    if getattr(e,'_analysis_token',None):return True
    token=uuid4().hex;e._analysis_token=token
    e._status='正在准备已修改图像的分析内容（调色前）…可取消'
    if e._request('analysis_input',analysis_layers=deepcopy(e._layers),expected_sha256=e._sha,
                  content_key=identity,context={'token':token,'binding':bound}) is False:
        failed(e,'图像分析内容未能准备，原范围保留',{'token':token})
    return True


def _dispatch(e,kind,data,prepared):
    if kind=='segment':
        e._request('segment',**data,_analysis_prepared=prepared)
    elif kind=='operation':
        e._request(data['op'],**data['request'],_analysis_prepared=prepared)
    elif kind=='faces':
        from ..scene import with_local_faces
        from .pixel_selections import precache
        if not e._face_hints:return e._notify('当前图像内容未定位到清晰人脸，原范围保留',background=True)
        e._scene.set(with_local_faces(None,e._face_hints))
        precache(e,[face['id'] for face in e._face_hints])
        e._notify('已根据当前图像内容定位人脸，可直接选择人脸或皮肤',background=True)
    else:
        pending=e._pending_request
        if not pending or pending['binding']!=e._document_signature():return
        from .conversation import _spatial_context
        e.ai.plan(data['text'],data['recipe'],data['locked'],prepared['path'],e._generation,
                  data['mode'],_spatial_context(e))
        e.changed.emit()


def _unavailable(e,waiters):
    ids=[]
    for waiter in waiters:
        data=waiter['data'];context=data.get('context',{})
        if (waiter['kind']=='segment' and context.get('purpose')=='precache'
                and context.get('source_sha')==e._sha and context.get('scene_revision')==e._scene.revision):
            ids.extend(job['id'] for job in data.get('jobs',[]))
    if ids:e._scene.mark_pixel_status(ids,'unavailable')


def ready(e,result,active):
    context=active['context']
    if context['token']!=getattr(e,'_analysis_token',None):return
    e._analysis_token=None
    waiters=e._analysis_waiters;e._analysis_waiters=[]
    bound=(e._sha,key(e._layers))
    if active.get('cancelled') or tuple(context['binding'])!=bound:
        _unavailable(e,waiters)
        return
    if (result.get('source_sha')!=e._sha or result.get('content_key')!=bound[1]
            or not result.get('content_reference')):
        _unavailable(e,waiters)
        return failed(e,'图像分析内容已变化，原范围保留',context,force=True)
    prepared={**result,'binding':bound}
    if not _usable(prepared):
        _unavailable(e,waiters)
        return failed(e,'图像分析内容校验失败，原范围保留',context,force=True)
    e._analysis_input=prepared
    e._face_hints=deepcopy(result.get('faces',[]))
    eligible=[waiter for waiter in waiters if waiter['generation']==e._generation and waiter['binding']==bound]
    _unavailable(e,[waiter for waiter in waiters if waiter not in eligible])
    if not eligible and any(waiter['data'].get('priority')!='low' for waiter in waiters):
        return e._notify('照片或图层已变化，分析结果未应用',True)
    for waiter in eligible:_dispatch(e,waiter['kind'],waiter['data'],prepared)


def failed(e,message,context,*,force=False):
    if not force and context.get('token')!=getattr(e,'_analysis_token',None):return
    _unavailable(e,getattr(e,'_analysis_waiters',[]))
    e._analysis_token=None;e._analysis_waiters=[]
    e._notify(message,True)


def cancel(e):
    _unavailable(e,getattr(e,'_analysis_waiters',[]))
    e._analysis_token=None;e._analysis_waiters=[]
    if e._active and e._active['op']=='analysis_input':e._active['cancelled']=True
    e._queue=type(e._queue)(request for request in e._queue if request['op']!='analysis_input')
