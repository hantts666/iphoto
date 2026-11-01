"""Skin adjustment transitions share one policy without changing raw selections."""
from copy import deepcopy
import io
import json

import numpy as np
import pytest
from PIL import Image,ImageDraw

from iphoto.document import empty_mask,raster_mask
from iphoto.masks import encode_bitmap
from iphoto.photo_strategy import soften_effect_mask
from iphoto.skin_effects import prepare_items


def item(identifier='0',target='face_skin'):
    alpha=Image.new('L',(280,220));draw=ImageDraw.Draw(alpha)
    draw.ellipse((20,10,260,210),fill=255);draw.ellipse((100,130,180,155),fill=0)
    mask={**empty_mask(),'semantic_target':target,'bitmap':encode_bitmap(alpha,sampling='alpha')}
    return {'id':identifier,'mask':mask,'quality':{'model':'neural-test','warnings':[]}}


@pytest.mark.parametrize('cached',[False,True])
@pytest.mark.parametrize('target',['face_skin','body_skin'])
def test_exact_shared_transition_and_selection_cache_remains_unchanged(cached,target):
    original=item(target=target);before=deepcopy(original);untouched=item('other')
    spec={'size':[280,220],'targets':{'0':target},'seeds':{'0':original} if cached else {}}
    values=[untouched] if cached else [original,untouched]
    result=prepare_items(values,spec)
    revised=next(value for value in result if value['id']=='0')
    assert revised['mask']==soften_effect_mask(original['mask'],(280,220))
    assert revised['quality']['skin_effect_transition'] and original==before
    assert next(value for value in result if value['id']=='other') is untouched
    alpha=np.asarray(raster_mask(original['mask'],(280,220)))
    effect=np.asarray(raster_mask(revised['mask'],(280,220)))
    assert np.all(effect<=alpha) and not np.any(effect[alpha==0]) and effect.max()==255
    assert np.any(effect!=alpha) and effect[145,140]==0


@pytest.mark.parametrize('case',['size','bool-size','large','target','ids','seeds','missing','wrong-target','part','quality','duplicate'])
def test_invalid_transition_spec_or_semantic_result_is_atomic(case):
    original=item();values=[original];spec={'size':[280,220],'targets':{'0':'face_skin'}}
    if case=='size':spec['size']=[280]
    elif case=='bool-size':spec['size']=[True,220]
    elif case=='large':spec['size']=[10000,10000]
    elif case=='target':spec['targets']['0']='object'
    elif case=='ids':spec['targets']={'':'face_skin'}
    elif case=='seeds':spec['seeds']={'extra':item('extra')}
    elif case=='missing':values=[]
    elif case=='wrong-target':original['mask']['semantic_target']='face'
    elif case=='part':original['mask']['face_part']='nose';original['mask']['semantic_target']='face_skin'
    elif case=='quality':original['quality']=None
    else:values.append(original)
    before=deepcopy(values)
    with pytest.raises(ValueError):prepare_items(values,spec)
    assert values==before


def test_cached_transition_in_real_pixel_protocol_never_loads_rgb_or_a_segmentation_model(tmp_path,monkeypatch):
    from iphoto.segmentation import pixel_worker,service
    proxy=tmp_path/'proxy.png';Image.new('RGB',(280,220)).save(proxy)
    original=item();before=deepcopy(original)
    request={'op':'segment','id':1,'generation':2,'proxy_path':str(proxy),'jobs':[],
             'skin_effects':{'size':[280,220],'targets':{'0':'face_skin'},'seeds':{'0':original}}}
    class Stream(io.StringIO):
        def reconfigure(self,**kwargs):pass
    output=Stream()
    monkeypatch.setattr(pixel_worker.sys,'stdin',Stream(json.dumps(request)+'\n'))
    monkeypatch.setattr(pixel_worker.sys,'stdout',output)
    monkeypatch.setattr(pixel_worker,'load_native',lambda *args:pytest.fail('Cached mask needs no native RGB'))
    monkeypatch.setattr(service,'segment_jobs',lambda *args,**kwargs:pytest.fail('Cached mask needs no model'))
    pixel_worker.main();response=json.loads(output.getvalue())
    assert response['ok'] and response['generation']==2
    revised=response['result']['items'][0]
    assert revised['mask']==soften_effect_mask(original['mask'],(280,220)) and original==before


def test_mixed_region_relocalization_does_not_fade_staged_skin_siblings_twice():
    original=item();spec={'size':[280,220],'targets':{'0':'face_skin'}}
    first=prepare_items([original],spec)[0];before=deepcopy(first)
    # The object-localization round trip retains already completed siblings.
    other=item('1');other['mask'].pop('semantic_target')
    second=prepare_items([other],{**spec,'seeds':{'0':first}})
    skin=next(value for value in second if value['id']=='0')
    assert skin==first and first==before
    assert skin['quality']['skin_effect_transition']=={'policy':'inward-v1','size':[280,220]}
    assert skin['mask']!=soften_effect_mask(first['mask'],(280,220))


@pytest.mark.parametrize('case',['direct','cached','strategy','channel','object','face','nose','draft'])
def test_real_editor_schedules_only_unstaged_automatic_skin_effects(qt_app,ai_store,tmp_path,monkeypatch,case):
    from iphoto.workspace import Editor
    from iphoto.controllers import pixel_selections
    from iphoto.engine import Recipe
    from test_ai import wait_for
    from test_editor import settled
    source=tmp_path/'source.png';Image.new('RGB',(280,220),(150,100,80)).save(source)
    editor=Editor(ai_store=ai_store);sent=[]
    try:
        editor.openImage(str(source));wait_for(lambda:editor.hasImage and settled(editor))
        monkeypatch.setattr(editor,'_request',lambda op,**data:sent.append({'op':op,**data}))
        target='body_skin' if case=='cached' else 'object' if case=='object' else 'face' if case=='face' else 'face_skin'
        region={'name':'skin','reason':'natural effect','mask_target':target,'mask':empty_mask(True),
                'recipe':Recipe(exposure=.25).to_dict(),'anchor':[.5,.5]}
        if case=='nose':region['face_part']='nose'
        origin={'photo_strategy':{'token':'private'}} if case=='strategy' else {'channel_auto':{'token':'private'}} if case=='channel' else {}
        seeds={'0':item(target=target)} if case=='cached' else {}
        pixel_selections.select_regions(editor,[region],'skin',case!='draft',origin=origin,seed_items=seeds)
        assert len(sent)==1
        expected=case in ('direct','cached')
        assert ('skin_effects' in sent[0])==expected
        if expected:
            assert sent[0]['skin_effects']=={'size':[280,220],'targets':{'0':target},'seeds':seeds}
        if case=='cached':assert sent[0]['jobs']==[]
    finally:editor.close()
