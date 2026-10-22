"""Accepting a channel result saves the range and opens adjustments once."""
from copy import deepcopy
import hashlib

import pytest
from PIL import Image
from PySide6.QtCore import QProcess

from iphoto.controllers.layers import applyRangeMask
from iphoto.document import empty_mask,new_layer,raster_mask,read_project,MAX_LAYERS
from iphoto.masks import encode_bitmap,bitmap_has_coverage
from iphoto.workspace import Editor
from test_ai import wait_for
from test_channel_matting import scene
from test_editor import settled


def ready(editor):
    wait_for(lambda:not editor.channelMask.loading and editor.channelMask.previewUrl and settled(editor))


def expected(editor):
    return deepcopy({'generation':editor._generation,'sha':editor._sha,'layer_id':editor._selected,
        'layers':editor._layers,'candidate':editor._candidate,'target_id':editor._selection_target_id})


def state(editor):
    return deepcopy((editor._layers,editor._selected,editor._candidate,editor._selection_target_id,
        editor._cursor,editor._history,editor._draft_history,editor._draft_cursor,
        editor._generation,editor._edit_revision,editor.dirty,editor._recipe,editor._locked))


def bitmap(alpha):
    return {**empty_mask(),'bitmap':encode_bitmap(alpha,sampling='alpha',preserve_resolution=True)}


@pytest.mark.parametrize('entry',['whole','local','free-draft','bound-draft'])
def test_real_use_commits_once_to_expected_recipient_and_can_adjust_save_and_undo(qt_app,ai_store,tmp_path,entry):
    import numpy as np
    image,truth,mask,options=scene();path=tmp_path/'source.png';image.save(path)
    digest=hashlib.sha256(path.read_bytes()).hexdigest();editor=Editor(ai_store=ai_store)
    try:
        editor.openImage(str(path));wait_for(lambda:editor.hasImage and settled(editor))
        editor._layer()['recipe']['exposure']=.35;editor._layer()['locked']=['exposure']
        editor._layer()['opacity']=.6
        if entry in ('local','bound-draft'):editor._layer()['mask']=deepcopy(mask)
        editor._load_layer();editor._commit()
        if entry=='free-draft':editor._set_candidate(mask)
        elif entry=='bound-draft':editor.beginSelection('current')
        wait_for(lambda:settled(editor));before=deepcopy(editor._layers);cursor=editor._cursor
        transitions=[];editor.selection.draftBegan.connect(lambda value:transitions.append('begin'))
        editor.selection.draftEnded.connect(lambda:transitions.append('end'))
        channel=editor.channelMask;channel.open();ready(editor)
        assert channel.savesCurrentLayer==(entry in ('local','bound-draft'))
        values={**options,'ai':False,'color':False}
        for key,value in values.items():
            if not channel.options.get('whole') or key in ('channel','black','white','gamma','invert'):
                channel.setOption(key,value)
        ready(editor);channel.apply();wait_for(lambda:not channel.opened and settled(editor),seconds=35)
        assert not editor.hasSelectionDraft and editor._selection_target_id=='' and editor._draft_history==[]
        assert editor._cursor==cursor+1 and editor.selection.pickedLayerId==editor.activeLayerId
        assert editor.maskView=='adjustment' and 'begin' not in transitions
        assert transitions==([] if entry in ('whole','local') else ['end'])
        alpha=raster_mask(editor._layer()['mask'],image.size)
        assert np.max(np.abs(np.asarray(alpha).astype(float)-np.rint(truth*255)))<=1
        if entry in ('local','bound-draft'):
            wanted=deepcopy(before);wanted[0]['mask']=editor._layer()['mask']
            assert editor._layers==wanted and editor.parameters['exposure']==.35
            assert editor._layer()['locked']==['exposure'] and editor._layer()['opacity']==.6
        else:
            assert editor._layers[:-1]==before and len(editor._layers)==len(before)+1
            assert not any(editor.parameters.values())
        final=deepcopy(editor._layers);editor.undo();wait_for(lambda:settled(editor))
        assert editor._layers==before and not editor.hasSelectionDraft
        editor.redo();wait_for(lambda:settled(editor));assert editor._layers==final
        editor.setParameter('saturation',12);editor.finishGesture();wait_for(lambda:settled(editor))
        assert editor.parameters['saturation']==12 and editor._layer()['mask']==final[-1 if entry in ('whole','free-draft') else 0]['mask']
        saved=tmp_path/'accepted.iphoto';editor.saveProject(str(saved))
        wait_for(lambda:editor._project_save_future is None and saved.is_file())
        project=read_project(saved)
        assert project['layers']==editor._layers and project.get('selection_draft') is None
        assert hashlib.sha256(path.read_bytes()).hexdigest()==digest
    finally:editor.close()


def test_native_one_byte_coverage_is_not_erased_by_an_occupancy_thumbnail(qt_app,ai_store,tmp_path):
    image=Image.new('RGB',(2048,2048),'gray');path=tmp_path/'fine.png';image.save(path)
    alpha=Image.new('L',image.size,0);alpha.putpixel((1001,1501),1);mask=bitmap(alpha)
    assert raster_mask(mask,(512,512)).getbbox() is None and bitmap_has_coverage(mask['bitmap'])
    editor=Editor(ai_store=ai_store)
    try:
        editor.openImage(str(path));wait_for(lambda:editor.hasImage and settled(editor))
        applyRangeMask(editor,mask,expected(editor))
        assert not editor.hasSelectionDraft and len(editor._layers)==2
        assert raster_mask(editor._layer()['mask'],image.size).tobytes()==alpha.tobytes()
    finally:editor.close()


@pytest.mark.parametrize('failure',['empty','limit','generation','candidate','target','vector'])
def test_invalid_result_never_mutates_document_or_selection(qt_app,ai_store,tmp_path,failure):
    image,_,_,_=scene();path=tmp_path/'protected.png';image.save(path);editor=Editor(ai_store=ai_store)
    try:
        editor.openImage(str(path));wait_for(lambda:editor.hasImage and settled(editor))
        if failure=='limit':
            editor._layers.extend(new_layer(str(i),True) for i in range(MAX_LAYERS-1));editor._commit()
        request=expected(editor);mask=bitmap(Image.new('L',image.size,0 if failure=='empty' else 128))
        if failure=='generation':request['generation']-=1
        if failure=='candidate':request['candidate']=empty_mask()
        if failure=='vector':mask=empty_mask(True)
        before=state(editor)
        with pytest.raises(ValueError):
            applyRangeMask(editor,mask,request,target_id='another' if failure=='target' else '')
        assert state(editor)==before
    finally:editor.close()


@pytest.mark.parametrize('selected_group',[False,True])
def test_new_range_keeps_manual_selection_parent_policy(qt_app,ai_store,tmp_path,selected_group):
    image,_,_,_=scene();path=tmp_path/'group.png';image.save(path);editor=Editor(ai_store=ai_store)
    try:
        editor.openImage(str(path));wait_for(lambda:editor.hasImage and settled(editor))
        group=new_layer('Group',True,kind='group');group['opacity']=.45
        editor._layer()['parent_id']=group['id'];editor._layers.append(group)
        if selected_group:editor._selected=group['id'];editor._load_layer()
        editor._commit();before=deepcopy(editor._layers)
        applyRangeMask(editor,bitmap(Image.new('L',image.size,128)),expected(editor))
        assert editor._layer()['parent_id']==group['id'] and editor._layers[:-1]==before
        assert editor.selection.pickedLayerId==editor.activeLayerId
    finally:editor.close()


def test_empty_use_keeps_dialog_retryable_then_invert_can_publish(qt_app,ai_store,tmp_path):
    path=tmp_path/'black.png';Image.new('RGB',(120,90),'black').save(path);editor=Editor(ai_store=ai_store)
    try:
        editor.openImage(str(path));wait_for(lambda:editor.hasImage and settled(editor))
        before=deepcopy(editor._layers);cursor=editor._cursor
        channel=editor.channelMask;channel.open();ready(editor);channel.apply()
        wait_for(lambda:not editor.busy and not channel.loading and settled(editor),seconds=30)
        assert channel.opened and '为空' in channel.note and channel.sessionTimer.isActive()
        assert editor._layers==before and editor._cursor==cursor and not editor.hasSelectionDraft
        channel.setOption('invert',True);ready(editor);channel.apply()
        wait_for(lambda:not channel.opened and settled(editor),seconds=30)
        assert editor._layers[:-1]==before and editor._cursor==cursor+1 and not editor.hasSelectionDraft
        wait_for(lambda:editor._matte_process.state()==QProcess.NotRunning)
    finally:editor.close()
