"""Face provenance survives renaming and editing, without changing pixels."""
from copy import deepcopy
from types import SimpleNamespace

import pytest
from PIL import Image

from iphoto.controllers import face_inventory,pixel_selections
from iphoto.document import empty_mask,new_layer,validate_mask,validate_layers,raster_mask,render_layers

REF={'face_id':'local-face-1','source_sha256':'a'*64}


def skin(ref=REF,label='人脸 1 · 面部皮肤'):
    mask={**empty_mask(),'label':label,'semantic_target':'face_skin',
          'ops':[{'kind':'polygon','mode':'add','points':[[.2,.2],[.5,.2],[.5,.5],[.2,.5]]}]}
    if ref is not None:mask['face_binding']=deepcopy(ref)
    return mask


def layer(mask=None,name='已改名'):
    result=new_layer(name);result['mask']=skin() if mask is None else mask
    result['recipe']['exposure']=.2
    return result


def owner(layers,selected=0):
    return SimpleNamespace(_sha='a'*64,_layers=layers,_layer=lambda:layers[selected])


FACE={'id':'local-face-1','mask':{'label':'人脸 1'}}


def test_binding_round_trips_validation_and_does_not_change_alpha_or_render():
    bound=layer();plain=deepcopy(bound);plain['mask'].pop('face_binding')
    source=Image.new('RGB',(100,70),(30,60,80))
    assert validate_layers([bound])[0]['mask']['face_binding']==REF
    assert raster_mask(bound['mask'],source.size).tobytes()==raster_mask(plain['mask'],source.size).tobytes()
    assert render_layers(source,[bound]).tobytes()==render_layers(source,[plain]).tobytes()
    upper=skin({**REF,'source_sha256':'A'*64})
    assert validate_mask(upper)['face_binding']==REF


@pytest.mark.parametrize('ref',[{},None,{'face_id':True,'source_sha256':'a'*64},
                              {**REF,'face_id':''},{**REF,'source_sha256':'a'*63},
                              {**REF,'source_sha256':True},{**REF,'extra':'opaque'}])
def test_invalid_explicit_bindings_rejected(ref):
    mask=skin();mask['face_binding']=ref
    if ref is None:
        assert 'face_binding' not in validate_mask(mask)
    else:
        with pytest.raises(ValueError,match='关联'):validate_mask(mask)


def test_non_face_mask_cannot_claim_a_face():
    mask=skin();mask['semantic_target']='body_skin'
    with pytest.raises(ValueError,match='关联'):validate_mask(mask)


def test_selected_duplicate_is_preferred_and_user_labels_do_not_define_identity():
    first=layer();second=layer(skin(label='皮肤边缘 · 已手工修正'),'我的第二层')
    assert face_inventory.retouch_layer(owner([first,second],selected=0),FACE) is first
    assert face_inventory.retouch_layer(owner([first,second],selected=1),FACE) is second
    assert face_inventory.retouch_layer(owner([second]),FACE) is second


def test_legacy_full_skin_layer_reuses_by_semantics_even_after_renaming():
    old=layer(skin(None))
    assert face_inventory.retouch_layer(owner([old]),FACE) is old
    old['mask']['label']='人脸 1 · 面部局部'
    assert face_inventory.retouch_layer(owner([old]),FACE) is None


@pytest.mark.parametrize('operation',('heal','inpaint'))
def test_repair_layer_is_not_repurposed_as_a_face_adjustment(operation):
    adjustment=layer();repair=layer();repair[operation]={'method':'telea'}
    assert face_inventory.retouch_layer(owner([adjustment,repair],selected=1),FACE) is adjustment


@pytest.mark.parametrize('change',('photo','face','inverted','object'))
def test_stale_other_face_or_inverted_mask_is_not_reused(change):
    item=layer()
    if change=='photo':item['mask']['face_binding']['source_sha256']='b'*64
    elif change=='face':item['mask']['face_binding']['face_id']='local-face-2'
    elif change=='inverted':item['mask']['inverted']=True
    elif change=='object':item['mask'].pop('semantic_target')
    assert face_inventory.retouch_layer(owner([item]),FACE) is None


@pytest.mark.parametrize('purpose',('hint','regions'))
def test_stale_async_face_result_is_rejected_before_document_mutation(purpose):
    e=owner([layer()]);before=deepcopy(e._layers)
    bad={**REF,'source_sha256':'b'*64}
    context={'purpose':purpose,**({'face_binding':bad} if purpose=='hint' else {'regions':[{'face_binding':bad}]})}
    with pytest.raises(ValueError,match='照片已更新'):pixel_selections.complete(e,{},context)
    assert e._layers==before
