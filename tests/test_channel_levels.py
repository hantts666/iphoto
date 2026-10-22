"""Channel changes use their own numeric domain and retain manual tuning."""
from copy import deepcopy

import numpy as np
from PIL import Image
from PySide6.QtCore import QUrl

from iphoto.document import empty_mask, raster_mask
from iphoto.masks import encode_bitmap
from iphoto.matting import channels
from iphoto.workspace import Editor
from test_ai import wait_for
from test_channel_matting import scene
from test_editor import settled


def colored_scene():
    rgb=np.full((160,240,3),(20,180,25),dtype='uint8')
    rgb[35:125,55:185]=(210,60,100)
    alpha=np.zeros(rgb.shape[:2],dtype='uint8');alpha[35:125,55:185]=255
    mask={**empty_mask(),'bitmap':encode_bitmap(Image.fromarray(alpha),sampling='alpha',preserve_resolution=True)}
    return Image.fromarray(rgb),mask,scene()[3]


def controls(options):
    return {key:options[key] for key in ('channel','black','white','gamma','invert')}


def test_recommendations_share_native_reference_and_use_each_planes_endpoints(monkeypatch):
    image,mask,options=colored_scene();calls=[];original=channels._fields
    def counted(*args):calls.append(1);return original(*args)
    monkeypatch.setattr(channels,'_fields',counted)
    suggestions={'stale':'discard'};cache={}
    channels.native_preview(image,mask,options,initial=True,cache=cache,suggestions=suggestions)
    # Independent foreground/background byte values, including clipped signed
    # subtraction. Green requires the opposite polarity from red.
    expected={'red':(20,210,False),'green':(60,180,True),'blue':(25,100,False),
              'luminance':(95,135,True),'red_green':(0,255,False),
              'red_blue':(123,238,False),'green_blue':(88,255,True)}
    expected={name:{'channel':name,'black':black,'white':white,'gamma':1.,'invert':invert}
              for name,(black,white,invert) in expected.items()}
    expected['auto']={**expected['red_green'],'channel':'auto'}
    assert suggestions==expected
    channels.native_preview(image,mask,{**options,'gamma':1.7},cache=cache,suggestions=suggestions)
    assert len(calls)==1
    channels.native_preview(image,empty_mask(True),channels.whole_options(),suggestions=suggestions)
    assert suggestions=={} and len(calls)==1


def test_constant_difference_plane_has_no_degenerate_recommendation():
    image,_,mask,options=scene();suggestions={}
    channels.native_preview(image,mask,options,suggestions=suggestions)
    assert set(suggestions)=={'red','green','blue','luminance','auto'}


def ready(editor):
    wait_for(lambda:not editor.channelMask.loading and editor.channelMask.previewUrl and settled(editor))


def test_switching_restores_levels_but_keeps_shared_controls_and_exact_apply(qt_app,ai_store,tmp_path):
    image,mask,options=colored_scene();path=tmp_path/'colored.png';image.save(path)
    editor=Editor(ai_store=ai_store)
    try:
        editor.openImage(str(path));wait_for(lambda:editor.hasImage and settled(editor))
        editor._set_candidate(mask);wait_for(lambda:settled(editor))
        before=deepcopy((editor._candidate,editor._layers,editor._cursor,editor._draft_history))
        channel=editor.channelMask;channel.open();ready(editor)
        channel.setOption('channel','red');ready(editor)
        assert controls(channel.options)=={'channel':'red','black':20,'white':210,'gamma':1.,'invert':False}
        for key,value in {'black':45,'white':190,'gamma':1.4,'invert':True,
                          'radius':12,'ai':False,'color':False,'interior':False}.items():channel.setOption(key,value)
        ready(editor);red=controls(channel.options)
        channel.setView('white');channel.setOption('channel','green');ready(editor)
        assert channel.view=='alpha'
        assert controls(channel.options)=={'channel':'green','black':60,'white':180,'gamma':1.,'invert':True}
        channel.setOption('white',170);channel.setOption('gamma',.8);channel.setOption('invert',False);ready(editor)
        green=controls(channel.options)
        channel.setOption('channel','red');ready(editor);assert controls(channel.options)==red
        assert {key:channel.options[key] for key in ('radius','ai','color','interior')}=={
            'radius':12,'ai':False,'color':False,'interior':False}
        channel.setOption('channel','green');ready(editor);assert controls(channel.options)==green
        channel.setOption('channel','auto');ready(editor)
        assert controls(channel.options)=={'channel':'auto','black':0,'white':255,'gamma':1.,'invert':False}
        channel.setOption('gamma',1.2);ready(editor)
        channel.setOption('channel','red');ready(editor);assert controls(channel.options)==red
        channel.setOption('channel','auto');ready(editor);assert channel.options['gamma']==1.2
        channel.setOption('channel','green');ready(editor);assert controls(channel.options)==green
        assert (editor._candidate,editor._layers,editor._cursor,editor._draft_history)==before
        expected=channels.native_preview(image,mask,channel.options)[0]
        actual=Image.open(QUrl(channel.previewUrl).toLocalFile())
        # The native view contains bbox + 3*12px context, while application
        # preserves the full canvas. The fixture's independent bbox is known.
        assert actual.tobytes()==expected.crop((19,0,221,160)).tobytes()
        channel.apply();wait_for(lambda:not channel.opened and settled(editor))
        final=deepcopy(editor._layer()['mask'])
        assert raster_mask(final,image.size).tobytes()==expected.tobytes()
        assert editor._layers[:-1]==before[1] and editor._cursor==before[2]+1
        assert not editor.hasSelectionDraft
        editor.undo();wait_for(lambda:settled(editor));assert editor._layers==before[1]
        editor.redo();wait_for(lambda:settled(editor));assert editor._layer()['mask']==final
    finally:editor.close()


def test_first_whole_photo_plane_uses_neutral_levels_and_reopen_resets_memory(qt_app,ai_store,tmp_path):
    image,_,_=colored_scene();path=tmp_path/'whole.png';image.save(path)
    editor=Editor(ai_store=ai_store)
    try:
        editor.openImage(str(path));wait_for(lambda:editor.hasImage and settled(editor))
        channel=editor.channelMask;channel.open();ready(editor)
        assert channel.options['whole'] is True
        channel.setOption('channel','red');ready(editor)
        channel.setOption('black',50);channel.setOption('white',190);channel.setOption('invert',True);ready(editor)
        saved=controls(channel.options)
        channel.setOption('channel','green');ready(editor)
        assert controls(channel.options)=={'channel':'green','black':0,'white':255,'gamma':1.,'invert':False}
        channel.setOption('channel','red');ready(editor);assert controls(channel.options)==saved
        channel.close();channel.open();ready(editor)
        channel.setOption('channel','red');ready(editor)
        assert controls(channel.options)=={'channel':'red','black':0,'white':255,'gamma':1.,'invert':False}
    finally:editor.close()


def test_invalid_control_and_stale_reply_cannot_overwrite_plane_memory(qt_app,ai_store,tmp_path):
    image,mask,_=colored_scene();path=tmp_path/'stale.png';image.save(path)
    editor=Editor(ai_store=ai_store)
    try:
        editor.openImage(str(path));wait_for(lambda:editor.hasImage and settled(editor))
        editor._set_candidate(mask);wait_for(lambda:settled(editor))
        channel=editor.channelMask;channel.open();ready(editor)
        before=deepcopy((channel.options,channel._levels,channel._suggestions,channel.previewUrl))
        channel.setOption('channel',[]);channel.setOption('black',999)
        assert (channel.options,channel._levels,channel._suggestions,channel.previewUrl)==before
        context={'token':channel.state['token'],'revision':channel.state['revision']}
        channel.setOption('channel','green')
        channel.ready({'options':before[0],'path':str(path),'channel':'red','score':5,
                       'level_suggestions':{'green':{'channel':'green','black':2,'white':3,'gamma':5.,'invert':False}}},
                      context,editor._generation)
        assert channel._suggestions==before[2]
        ready(editor)
        assert controls(channel.options)=={'channel':'green','black':60,'white':180,'gamma':1.,'invert':True}
    finally:editor.close()
