"""Native channel coverage survives display resize, levels and source changes."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import subprocess
import sys

import numpy as np
from PIL import Image
import pytest

from iphoto.document import empty_mask, raster_mask
from iphoto.engine import load_source, preview
from iphoto.matting import channels
from test_channel_matting import scene


@pytest.mark.parametrize('channel', channels.CHANNELS)
def test_single_plane_retains_every_native_byte(channel):
    y,x=np.mgrid[:256,:256]
    rgb=np.stack((x,y,(x*37+y*97)%256),axis=-1).astype('uint8')
    image=Image.fromarray(rgb);before=image.tobytes()
    if channel in channels.CHANNELS[:3]:
        expected=rgb[...,channels.CHANNELS.index(channel)]
    elif channel=='luminance':
        expected=np.rint(rgb[...,0]*.2126+rgb[...,1]*.7152+rgb[...,2]*.0722).astype('uint8')
    else:
        a,b={'red_green':(0,1),'red_blue':(0,2),'green_blue':(1,2)}[channel]
        expected=np.clip(rgb[...,a].astype('int16')-rgb[...,b].astype('int16')+128,0,255).astype('uint8')
    assert np.array_equal(channels.channel_plane(image,channel),expected)
    assert image.tobytes()==before


@pytest.mark.parametrize('interior',[False,True])
@pytest.mark.parametrize('semantic',[False,True])
def test_native_local_draft_matches_manual_application_and_keeps_scope(interior,semantic):
    image,_,mask,options=scene()
    if semantic:mask={**mask,'semantic_target':'face_skin'}
    before=deepcopy(mask);source_before=image.tobytes()
    options={**options,'interior':interior,'ai':False}
    draft,actual,_,_=channels.native_preview(image,mask,options)
    final,_=channels.estimate(image,mask,options)
    assert draft.tobytes()==raster_mask(final,image.size).tobytes()
    assert actual==options and mask==before and image.tobytes()==source_before
    if semantic:
        assert (np.asarray(draft)[np.asarray(raster_mask(mask,image.size))==0]==0).all()


def test_initial_references_and_level_cache_stay_bound_to_native_image_and_mask(monkeypatch):
    image,_,mask,options=scene();fields=channels._fields;calls=[]
    def counted(*args):
        calls.append(args[0].size)
        return fields(*args)
    monkeypatch.setattr(channels,'_fields',counted)
    cache={}
    _,initial,_,_=channels.native_preview(image,mask,options,initial=True,cache=cache)
    assert initial['radius']==8 and initial['interior'] is True
    altered={**initial,'black':30,'white':220,'gamma':1.4}
    draft,_,_,_=channels.native_preview(image,mask,altered,cache=cache)
    assert len(calls)==1
    assert draft.tobytes()==channels.native_preview(image,mask,altered)[0].tobytes()
    assert len(calls)==2
    channels.native_preview(image,mask,{**altered,'radius':9},cache=cache)
    assert len(calls)==3
    moved={**mask,'inverted':True}
    channels.native_preview(image,moved,{**altered,'radius':9},cache=cache)
    assert len(calls)==4
    channels.native_preview(image.copy(),mask,altered,cache=cache)
    assert len(calls)==5
    channels.native_preview(image,empty_mask(True),channels.whole_options(),cache=cache)
    assert cache=={}


def test_large_native_reference_crop_is_not_retained():
    image,_,mask,options=scene()
    image=image.resize((2400,1800),Image.Resampling.NEAREST)
    cache={}
    channels.native_preview(image,mask,{**options,'radius':256},cache=cache)
    assert cache=={}


def test_real_worker_curves_fine_pixels_before_resizing_and_keeps_source(tmp_path):
    # Independent clipping oracle: thin bright filaments among dark pixels.
    # Curving the mixed RGB proxy deletes coverage that exists in the Source.
    pixels=np.full((128,1920,3),20,'uint8');pixels[:,::4]=200
    image=Image.fromarray(pixels);path=tmp_path/'fine.png';image.save(path)
    loaded=load_source(path);display=preview(loaded.image,1600)
    options={**channels.whole_options(),'channel':'red','black':100,'white':180}
    request=[{'id':1,'op':'open','path':str(path),'generation':0},
             {'id':2,'op':'channel_preview','generation':0,'mask':empty_mask(True),'options':options,
              'expected_sha256':loaded.digest,'display_preview':True}]
    root=Path(__file__).resolve().parents[1]
    result=subprocess.run([sys.executable,str(root/'run.py'),'--worker',str(tmp_path/'cache')],
                          input=''.join(json.dumps(item)+'\n' for item in request),capture_output=True,text=True,
                          encoding='utf8',timeout=30)
    assert result.returncode==0,result.stderr
    replies=[json.loads(line) for line in result.stdout.splitlines()]
    assert len(replies)==2 and all(item['ok'] for item in replies),replies
    reply=replies[-1]['result']
    truth=np.rint(np.clip((pixels[...,0].astype(float)-100)/80,0,1)*255).astype('uint8')
    expected=Image.fromarray(truth).resize(display.size,Image.Resampling.LANCZOS)
    actual=Image.open(reply['path'])
    assert actual.mode=='L' and actual.size==display.size and actual.tobytes()==expected.tobytes()
    old=np.rint(np.clip((np.asarray(display)[...,0].astype(float)-100)/80,0,1)*255).astype('uint8')
    assert np.abs(np.asarray(actual).astype(int)-old.astype(int)).max()>40
    assert all(Image.open(value).size==display.size for value in reply['views'].values())
    assert hashlib.sha256(path.read_bytes()).hexdigest()==loaded.digest
