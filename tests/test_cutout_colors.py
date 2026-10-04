"""Native straight RGB recovery: known colors, alpha once and real worker views."""
from copy import deepcopy
import json
import subprocess
import sys

import numpy as np
from PIL import Image, ImageChops
import pytest

from iphoto.cutout import background_view, color_patch, compose_cutout, recovery_box
from iphoto.document import empty_mask, new_layer, raster_mask, validate_mask, project_version
from iphoto.engine import load_source
from iphoto.masks import encode_bitmap
from iphoto.paths import ROOT


def scene():
    alpha = np.zeros((160,240), np.uint8)
    alpha[30:130,50:190] = 255
    alpha[30:130,50:70] = np.linspace(30,245,20,dtype=np.uint8)[None,:]
    alpha[30:130,170:190] = np.linspace(245,30,20,dtype=np.uint8)[None,:]
    foreground = np.array([174,57,38],np.float32)
    background = np.array([30,143,106],np.float32)
    a = alpha.astype(np.float32)/255
    photo = Image.fromarray(np.rint(a[...,None]*foreground+(1-a[...,None])*background).astype(np.uint8))
    mask = {**empty_mask(),'bitmap':encode_bitmap(Image.fromarray(alpha),sampling='alpha',preserve_resolution=True),
            'color_recovery':True}
    return photo,mask,alpha,foreground


def test_recovery_removes_known_background_color_without_squaring_alpha():
    source,mask,alpha,truth = scene()
    source = source.convert('RGBA');source.putalpha(192)
    original = source.tobytes();before=deepcopy(mask)
    selected = raster_mask(mask,source.size)
    patch = color_patch(source,[],mask,selected)
    output = compose_cutout(source,selected,patch)
    pixels = np.asarray(output)[...,:3];old = np.asarray(source)[...,:3]
    partial=(alpha>0)&(alpha<255)
    assert np.abs(old.astype(float)[partial]-truth).mean()>30
    assert np.abs(pixels.astype(float)[partial]-truth).mean()<3
    assert np.array_equal(pixels[~partial],old[~partial])
    assert output.getchannel('A').tobytes()==ImageChops.multiply(source.getchannel('A'),selected).tobytes()
    assert source.tobytes()==original and mask==before


def test_classic_refinement_keeps_recovery_for_actual_preview_and_output():
    from iphoto.matting.service import refine_alpha
    source,mask,_,truth=scene()
    before=deepcopy(mask);photo=source.tobytes()
    result,_=refine_alpha(source,mask,4,linear=False)
    alpha=raster_mask(result,source.size)
    assert result['color_recovery'] is True
    recovered=compose_cutout(source,alpha,color_patch(source,[],result,alpha))
    raw=compose_cutout(source,alpha)
    partial=(np.asarray(alpha)>20)&(np.asarray(alpha)<235)
    assert partial.any()
    error=lambda output:np.abs(np.asarray(output)[...,:3].astype(float)[partial]-truth).mean()
    assert error(recovered)<error(raw)/4
    for mode in ('white','black'):
        actual=background_view(source,[],result,mode,source)
        expected=Image.alpha_composite(Image.new('RGBA',source.size,mode),recovered).convert('RGB')
        assert actual.tobytes()==expected.tobytes()
    assert recovered.getchannel('A').tobytes()==alpha.tobytes()
    assert source.tobytes()==photo and mask==before


@pytest.mark.parametrize('mode',['white','black'])
def test_native_and_small_background_checks_match_the_cutout(mode):
    source,mask,alpha,_ = scene()
    layers=[new_layer('照片',True)]
    cutout=compose_cutout(source,Image.fromarray(alpha),color_patch(source,layers,mask))
    expected=Image.alpha_composite(Image.new('RGBA',source.size,mode),cutout).convert('RGB')
    box=(80,40,210,140)
    actual=background_view(source,layers,mask,mode,source.crop(box),box)
    assert actual.tobytes()==expected.crop(box).tobytes()
    small=source.resize((120,80),Image.Resampling.LANCZOS)
    actual=background_view(source,layers,mask,mode,small)
    expected=Image.alpha_composite(Image.new('RGBA',small.size,mode),cutout.resize(small.size,Image.Resampling.LANCZOS)).convert('RGB')
    assert actual.tobytes()==expected.tobytes()


def test_color_cache_changes_with_pixels_and_correction_is_optional():
    source,mask,alpha,_=scene()
    patch=color_patch(source,[],mask)
    changed=source.copy();changed.putpixel((60,70),(210,40,30))
    again=color_patch(changed,[],mask)
    assert again[1].tobytes()!=patch[1].tobytes()
    assert color_patch(source,[],{**mask,'color_recovery':False}) is None
    assert compose_cutout(source,Image.fromarray(alpha)).convert('RGB').tobytes()==source.tobytes()


def test_unanchored_and_large_recovery_declines_and_hard_alpha_needs_no_solver(monkeypatch):
    assert recovery_box(Image.new('L',(200,160),255)) is None
    with pytest.raises(ValueError,match='参照'):recovery_box(Image.new('L',(200,160),128))
    monkeypatch.setattr('iphoto.cutout.MAX_AREA',100)
    with pytest.raises(ValueError,match='范围超过'):recovery_box(Image.fromarray(scene()[2]))


def test_project_preserves_color_policy_including_uncommitted_draft():
    _,mask,_,_=scene()
    assert validate_mask(mask)==mask
    assert project_version([new_layer('原图',True)],mask)=='1.12'
    assert project_version([new_layer('原图',True)])=='1.10'
    with pytest.raises(ValueError):validate_mask({**mask,'color_recovery':'yes'})


@pytest.mark.parametrize('mode',['white','black'])
def test_real_detail_worker_accepts_background_views_and_uses_native_recovery(tmp_path,mode):
    source,mask,alpha,_=scene();path=tmp_path/'photo.png';source.save(path)
    layers=[new_layer('照片',True)];digest=load_source(path).digest
    box=[50,20,220,150]
    request={'op':'detail','id':1,'generation':7,'source_path':str(path),'source_sha':digest,
             'layers':layers,'mask':mask,'mask_view':mode,'box':box}
    process=subprocess.run([sys.executable,str(ROOT/'run.py'),'--detail-worker',str(tmp_path/'cache')],
                           input=json.dumps(request)+'\n',capture_output=True,text=True,encoding='utf-8',timeout=40)
    response=json.loads(process.stdout.splitlines()[-1])
    assert process.returncode==0 and response['ok'],response
    cutout=compose_cutout(source,Image.fromarray(alpha),color_patch(source,layers,mask))
    expected=Image.alpha_composite(Image.new('RGBA',source.size,mode),cutout).convert('RGB').crop(box)
    with Image.open(response['result']['mask']) as output:
        assert output.tobytes()==expected.tobytes()


def test_real_export_worker_keeps_native_alpha_and_uses_foreground_colors(tmp_path):
    source,mask,alpha,truth=scene();path=tmp_path/'photo.png';source.save(path)
    target=tmp_path/'cutout.png';layers=[new_layer('照片',True)]
    request={'op':'export','id':1,'source_path':str(path),'source_sha':load_source(path).digest,
             'layers':layers,'mask':mask,'output':'cutout','stage_path':str(target),'jpeg_quality':100}
    process=subprocess.run([sys.executable,str(ROOT/'run.py'),'--export-worker'],input=json.dumps(request)+'\n',
                           capture_output=True,text=True,encoding='utf-8',timeout=40)
    response=json.loads(process.stdout.splitlines()[-1])
    assert process.returncode==0 and response['ok'],response
    with Image.open(target) as output:
        assert output.size==source.size and output.mode=='RGBA'
        assert output.getchannel('A').tobytes()==alpha.tobytes()
        partial=(alpha>0)&(alpha<255)
        assert np.abs(np.asarray(output)[...,:3].astype(float)[partial]-truth).mean()<3


def test_real_preview_worker_reuses_photo_cache_and_refreshes_background_colors(qt_app,ai_store,tmp_path):
    from PySide6.QtCore import QUrl
    from iphoto.workspace import Editor
    from test_ai import wait_for
    from test_editor import settled
    source,mask,alpha,_=scene();path=tmp_path/'photo.png';source.save(path)
    editor=Editor(ai_store=ai_store)
    try:
        editor.openImage(str(path));wait_for(lambda:editor.hasImage and settled(editor))
        editor._set_candidate(mask);wait_for(lambda:settled(editor))
        layers=deepcopy(editor._layers);original=editor.previewUrl
        for mode in ('white','black'):
            editor.setMaskView(mode);wait_for(lambda:settled(editor) and bool(editor.maskUrl))
            assert editor.previewUrl==original and editor._layers==layers
            expected=background_view(source,layers,mask,mode,source)
            with Image.open(QUrl(editor.maskUrl).toLocalFile()) as displayed:
                assert displayed.tobytes()==expected.tobytes()
    finally:editor.close()
