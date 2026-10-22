"""Extraction must see applied pixels once, without learning a tone-mask seam."""
from copy import deepcopy
from hashlib import sha256
import json
from pathlib import Path

from PIL import Image
import pytest

from iphoto.analysis_content import ContentCache,project,resolve
from iphoto.document import empty_mask,new_layer,raster_mask,render_layers
from iphoto.engine import load_source
from iphoto.pixel_patch import encode_patch


def scene(tmp_path):
    path=tmp_path/'source.png';Image.new('RGB',(128,96),(160,30,20)).save(path)
    source=load_source(path)
    base=new_layer('tone',True);base['recipe']['exposure']=1.;base['locked']=['exposure']
    patch=new_layer('pixels',True);patch['opacity']=.5
    patch['pixel_patch']=encode_patch(Image.new('RGB',source.image.size,(20,30,160)),
                                     list(source.image.size),[0,0,*source.image.size])
    return source,[base,patch]


def test_projection_retains_pixels_masks_and_opacity_without_tone_or_mutation(tmp_path):
    source,layers=scene(tmp_path);before=deepcopy(layers);original=source.image.tobytes()
    projected=project(layers);cache=ContentCache(tmp_path)
    content,reference=cache.get(source,layers=layers)
    expected=Image.blend(source.image.convert('RGB'),Image.new('RGB',source.image.size,(20,30,160)),.5)
    assert content.convert('RGB').tobytes()==expected.tobytes()
    assert content.getpixel((40,30))[:3]==(90,30,90)
    assert render_layers(source.image,layers).tobytes()!=content.tobytes()
    assert all(not any(item['recipe'].values()) and not item['locked'] for item in projected)
    assert resolve(source,reference).tobytes()==content.tobytes()
    assert layers==before and source.image.tobytes()==original


def test_group_mask_and_opacity_are_part_of_extraction_content(tmp_path):
    source,layers=scene(tmp_path);group=new_layer('group',True,kind='group');group['opacity']=.5
    layers[-1]['parent_id']=group['id'];layers.append(group)
    content,_=ContentCache(tmp_path).get(source,layers=layers)
    half=Image.blend(source.image.convert('RGB'),Image.new('RGB',source.image.size,(20,30,160)),.5)
    expected=Image.blend(source.image.convert('RGB'),half,.5)
    assert content.convert('RGB').tobytes()==expected.tobytes()
    assert content.getpixel((40,30))[:3]==(125,30,55)


@pytest.mark.parametrize('kind',['tone','hidden','zero-opacity','hidden-group'])
def test_documents_without_visible_pixel_content_use_original_fast_path(tmp_path,kind):
    source,layers=scene(tmp_path)
    if kind=='tone':layers=layers[:1]
    elif kind=='hidden':layers[-1]['visible']=False
    elif kind=='zero-opacity':layers[-1]['opacity']=0
    else:
        group=new_layer('hidden',True,kind='group');group['visible']=False
        layers[-1]['parent_id']=group['id'];layers.append(group)
    cache=ContentCache(tmp_path);content,ref=cache.get(source,layers=layers)
    assert content is source.image and ref is None and list(tmp_path.glob('content-*.json'))==[]


def test_one_cache_frame_survives_tone_changes_but_replaces_changed_pixels(tmp_path):
    source,layers=scene(tmp_path);cache=ContentCache(tmp_path)
    first,ref=cache.get(source,layers=layers);saved=deepcopy(ref)
    ref['canvas_size'][0]=1
    layers[0]['recipe']['exposure']=2
    second,actual=cache.get(source,layers=layers)
    assert first is second and actual==saved
    layers[-1]['opacity']=1
    third,new=cache.get(source,layers=layers)
    assert third is not second and new!=saved and not Path(saved['path']).exists()
    assert len(list(tmp_path.glob('content-*.json')))==1


def test_external_reader_never_deletes_owners_snapshot(tmp_path):
    source,layers=scene(tmp_path);owner=ContentCache(tmp_path)
    _,ref=owner.get(source,layers=layers);reader=ContentCache(tmp_path)
    content,actual=reader.get(source,reference=ref);actual['canvas_size'][0]=1
    assert content.convert('RGB').getpixel((0,0))==(90,30,90)
    assert reader.reference==ref
    passed=deepcopy(ref);reader.clear();reader.get(source,reference=passed);passed['canvas_size'][0]=1
    assert reader.reference==ref
    reader.clear();assert Path(ref['path']).is_file()
    owner.clear();assert not Path(ref['path']).exists()


@pytest.mark.parametrize('damage',['tampered','missing'])
def test_refuse_damaged_reference_but_can_rebuild_from_trusted_document(tmp_path,damage):
    source,layers=scene(tmp_path);cache=ContentCache(tmp_path)
    first,ref=cache.get(source,layers=layers);path=Path(ref['path'])
    if damage=='tampered':path.write_bytes(b'corrupt')
    else:path.unlink()
    with pytest.raises(ValueError):cache.get(source,reference=ref)
    repaired,new=cache.get(source,layers=layers)
    assert repaired is first and new==ref and resolve(source,ref).tobytes()==first.tobytes()


@pytest.mark.parametrize('field,value',[('source_sha','0'*64),('sha256','z'*64),
    ('canvas_size',[96,128]),('path','relative.json')])
def test_reference_is_bound_to_original_and_canvas(tmp_path,field,value):
    source,layers=scene(tmp_path);_,ref=ContentCache(tmp_path).get(source,layers=layers)
    with pytest.raises(ValueError):resolve(source,{**ref,field:value})


def test_snapshot_cannot_hide_a_second_tone_application(tmp_path):
    source,layers=scene(tmp_path);_,ref=ContentCache(tmp_path).get(source,layers=layers)
    path=Path(ref['path']);payload=json.loads(path.read_bytes())
    payload['layers'][0]['recipe']['exposure']=1
    data=json.dumps(payload).encode();path.write_bytes(data)
    with pytest.raises(ValueError,match='重复应用调色'):
        resolve(source,{**ref,'sha256':sha256(data).hexdigest()})


def test_source_reference_and_actual_review_output_use_their_respective_frames(tmp_path):
    from iphoto.cutout import compose_cutout
    from iphoto.matte_review import render_review
    source,layers=scene(tmp_path);content,_=ContentCache(tmp_path).get(source,layers=layers)
    mask=empty_mask(True);box=[0,0,*source.image.size]
    result=render_review(source.image,layers,mask,tmp_path,7,boxes=[box],final_review=True,content=content)
    original=next(item for item in result['images'] if item['label']=='边缘1原像素原照片')
    with Image.open(original['path']) as image:assert image.convert('RGB').tobytes()==content.convert('RGB').tobytes()
    for color,label in [('white','白底'),('black','黑底')]:
        entry=next(item for item in result['images'] if item['label']==f'边缘1原像素候选{label}')
        rendered=render_layers(source.image,layers)
        expected=Image.alpha_composite(Image.new('RGBA',source.image.size,color),
                                      compose_cutout(rendered,raster_mask(mask,source.image.size))).convert('RGB')
        with Image.open(entry['path']) as image:assert image.tobytes()==expected.tobytes()


def test_real_matting_execution_uses_snapshot_and_rejects_cross_content_session(tmp_path):
    from iphoto.matting.channels import whole_options
    from iphoto.matting.worker import execute,channel_session
    source,layers=scene(tmp_path);_,ref=ContentCache(tmp_path).get(source,layers=layers)
    request={'op':'matte','id':1,'generation':1,'method':'channel','source_path':str(source.path),
        'source_sha':source.digest,'mask':empty_mask(True),
        'channel_options':{**whole_options(),'channel':'red'},'content_reference':ref,
        'preview_only':True,'channel_token':'a'*32,'channel_session':'a'*32}
    result=execute(request);assert result['ok'],result.get('error')
    assert raster_mask(result['result']['mask'],source.image.size).getextrema()==(90,90)
    assert result['result']['quality']['content_sha256']==ref['sha256']
    key=channel_session(request)
    changed={**request,'content_reference':{**ref,'sha256':'b'*64}}
    rejected=execute(changed,session=key)
    assert not rejected['ok'] and '会话已变化' in rejected['error']


@pytest.mark.parametrize('stage',['complete','tune','review','points'])
def test_ai_channel_rejects_evidence_from_another_content_frame(qt_app,ai_store,tmp_path,monkeypatch,stage):
    from iphoto.controllers import channel_auto
    from iphoto.matting.channels import whole_options
    from iphoto.workspace import Editor
    from test_ai import wait_for
    from test_editor import settled
    source,layers=scene(tmp_path);editor=Editor(ai_store=ai_store)
    try:
        editor.openImage(str(source.path));wait_for(lambda:editor.hasImage and settled(editor))
        state={'token':'a'*32,'generation':editor._generation,'candidate':deepcopy(editor._candidate),
            'target_id':editor._selection_target_id,'revision':0,'options':whole_options(),
            'content_reference':{'sha256':'b'*64},'tune_preparing':True}
        editor._pending_request={'text':'检查头发','binding':editor._document_signature(),
            'layer_snapshot':deepcopy(editor._layers),'channel_auto':state}
        calls=[];errors=[]
        monkeypatch.setattr(editor,'_request',lambda *args,**kwargs:calls.append((args,kwargs)))
        monkeypatch.setattr(editor,'_notify',lambda message,*args:errors.append(message))
        monkeypatch.setattr(editor.ai,'plan',lambda *args,**kwargs:calls.append((args,kwargs)))
        wrong={'content_reference':{'sha256':'c'*64},'options':state['options']}
        if stage=='complete':channel_auto.complete(editor,{'quality':{'content_sha256':'c'*64}},state['token'])
        elif stage=='tune':channel_auto.tune_ready(editor,wrong,{'token':state['token']},editor._generation)
        elif stage=='review':channel_auto.review_ready(editor,wrong,{'token':state['token']},editor._generation)
        else:
            state['points_pending']=True
            channel_auto.points_ready(editor,wrong,{'token':state['token']},editor._generation)
        assert len(errors)==1 and '内容' in errors[0] and not calls
    finally:editor.close()


@pytest.mark.parametrize('tamper',[False,True])
def test_real_editor_reads_applied_pixels_and_direct_use_errors_are_visible(qt_app,ai_store,tmp_path,tamper):
    from PySide6.QtCore import QUrl
    from iphoto.workspace import Editor
    from test_ai import wait_for
    from test_editor import settled
    source,layers=scene(tmp_path);editor=Editor(ai_store=ai_store)
    def ready():return not editor.channelMask.loading and editor.channelMask.previewUrl and settled(editor)
    try:
        editor.openImage(str(source.path));wait_for(lambda:editor.hasImage and settled(editor))
        editor._layers=deepcopy(layers);editor._selected=layers[0]['id']
        editor._load_layer();editor._commit();editor._change();wait_for(lambda:settled(editor))
        before=deepcopy(editor._layers);cursor=editor._cursor;channel=editor.channelMask
        channel.open();wait_for(ready)
        with Image.open(QUrl(channel._views['source']).toLocalFile()) as image:
            assert image.convert('RGB').getpixel((40,30))==(90,30,90)
        assert channel.state['content_reference'] and '调色前' in channel.note
        channel.setOption('channel','red');wait_for(ready)
        if tamper:
            Path(channel.state['content_reference']['path']).write_bytes(b'corrupted')
            channel.apply();wait_for(lambda:not editor.busy and not channel.loading and settled(editor))
            assert channel.opened and '校验失败' in channel.note
            assert editor._layers==before and editor._cursor==cursor and not editor.hasSelectionDraft
            channel.close();channel.open();wait_for(ready)
            channel.setOption('channel','red');wait_for(ready)
        channel.apply();wait_for(lambda:not channel.opened and settled(editor))
        assert editor._layers[:-1]==before and editor._cursor==cursor+1
        assert raster_mask(editor._layer()['mask'],source.image.size).getextrema()==(90,90)
        assert not editor.hasSelectionDraft
        editor.undo();wait_for(lambda:settled(editor));assert editor._layers==before
        editor.redo();wait_for(lambda:settled(editor));assert len(editor._layers)==len(before)+1
        assert sha256(source.path.read_bytes()).hexdigest()==source.digest
    finally:editor.close()
