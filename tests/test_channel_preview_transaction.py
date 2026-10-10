"""A channel preview can run real workers without creating a document draft."""
from copy import deepcopy

import pytest

from iphoto.workspace import Editor
from test_ai import wait_for
from test_channel_matting import scene
from test_editor import settled


def snapshot(editor):
    return deepcopy((editor._layers,editor._candidate,editor._selection_target_id,
                     editor._cursor,editor._draft_history,editor._draft_cursor,
                     editor._generation,editor.dirty,editor._edit_revision))


def prepared(editor,tmp_path):
    image,_,mask,_=scene();path=tmp_path/'local-layer.png';image.save(path)
    editor.openImage(str(path));wait_for(lambda:editor.hasImage and settled(editor))
    editor._layer()['mask']=deepcopy(mask)
    editor._layer()['recipe']['exposure']=.35
    editor._load_layer();editor._commit()
    assert not editor.hasSelectionDraft
    return mask


@pytest.mark.parametrize('preview_first',[False,True])
def test_real_channel_result_creates_layer_bound_draft_only_when_applied(qt_app,ai_store,tmp_path,monkeypatch,preview_first):
    editor=Editor(ai_store=ai_store)
    try:
        original=prepared(editor,tmp_path);before=snapshot(editor);channel=editor.channelMask
        requests=[];request=editor._request
        def counted(op,**fields):
            requests.append(op)
            return request(op,**fields)
        monkeypatch.setattr(editor,'_request',counted)
        channel.open();wait_for(lambda:not channel.loading and channel.previewUrl and settled(editor))
        assert snapshot(editor)==before and not channel.options.get('whole')
        if preview_first:
            channel.previewResult()
            wait_for(lambda:channel.hasResult and not channel.loading and settled(editor),seconds=45)
            assert snapshot(editor)==before
        channel.apply()
        wait_for(lambda:editor.hasSelectionDraft and not channel.opened and settled(editor),seconds=45)
        assert requests.count('matte')==1
        assert editor._layers==before[0] and editor._cursor==before[3]
        assert editor._selection_target_id==editor._selected
        final=deepcopy(editor._candidate)
        assert final!=original and editor._draft_history==[original,final]
        editor.undo();assert editor._candidate==original
        editor.redo();assert editor._candidate==final
    finally:editor.close()


@pytest.mark.parametrize('preview_only',[False,True])
def test_closing_real_channel_calculation_preserves_absent_draft(qt_app,ai_store,tmp_path,preview_only):
    editor=Editor(ai_store=ai_store)
    try:
        prepared(editor,tmp_path);before=snapshot(editor);channel=editor.channelMask
        channel.open();wait_for(lambda:not channel.loading and channel.previewUrl and settled(editor))
        if preview_only:channel.previewResult()
        else:channel.apply()
        wait_for(lambda:editor.matteBusy)
        channel.close()
        wait_for(lambda:not editor.matteBusy and settled(editor),seconds=30)
        assert not channel.opened and not channel.hasResult and snapshot(editor)==before
    finally:editor.close()
