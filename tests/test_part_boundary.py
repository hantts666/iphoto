"""Semantic anchors and exact alpha contracts, not model accuracy."""
from copy import deepcopy
from types import SimpleNamespace
import numpy as np
from PIL import Image
import pytest
from iphoto.ai_mask_refinement import validate_request
from iphoto.document import empty_mask,raster_mask
from iphoto.engine import Recipe
from iphoto.masks import encode_bitmap
from iphoto.segmentation.part_boundary import refine


def scene():
    size=(220,180);labels=np.zeros(size[::-1],np.uint8)
    labels[45:70,50:165]=12;labels[78:115,50:165]=13;labels[70:78,60:155]=11
    alpha=Image.fromarray(np.isin(labels,[12,13]).astype(np.uint8)*255)
    mask=empty_mask();mask.update(bitmap=encode_bitmap(alpha,sampling='alpha',preserve_resolution=True),
        semantic_target='face',face_part='lips',label='已有嘴唇')
    context={'crop':[0,0,1,1],'features':{'eyes':[[.3,.2],[.6,.2]],'mouth':[[.3,.55],[.65,.55]]},'anchor':[.5,.35]}
    return Image.new('RGB',size,(90,110,130)),mask,context,labels


def test_part_anchors_preserve_both_lips_prior_holes_and_other_alpha():
    image,mask,context,field=scene();before=raster_mask(mask,image.size);calls=[]
    def predict(patch,coords,labels,guide,**kwargs):
        calls.append((coords,labels));hard=np.asarray(guide)>127
        hard[:,50:54]=False
        logits=np.where(hard,8,-8).astype(np.float32)[None]
        return logits,np.array([.96],np.float32),{'model':'controlled','embedding_cached':False}
    engine=SimpleNamespace(predict_with_prior=predict)
    parser=SimpleNamespace(predict_native=lambda image,points:field)
    result,quality=refine(image,mask,context,parser=parser,engine=engine)
    after=np.asarray(raster_mask(result,image.size));prior=np.asarray(before)
    assert np.all(after<=prior) and np.count_nonzero(after)<np.count_nonzero(prior)
    coords,labels=calls[0];positive=[tuple(round(float(v)) for v in p) for p,l in zip(coords,labels) if l==1]
    assert len(positive)==2 and {int(field[y,x]) for x,y in positive}=={12,13}
    assert all(after[y,x]>127 for x,y in positive) and not np.any(after[field==11])
    assert quality['semantic_keep_classes']==[12,13] and result['face_part']=='lips'
    assert mask['ops']==[] and result['ops']==[] and result['feather']==0


@pytest.mark.parametrize('case',['missing-class','outside-context','wrong-size','invalid-context','wrong-target','bad-model','ignores-anchor'])
def test_unreliable_boundary_input_never_changes_prior(case):
    image,mask,context,field=scene();original=deepcopy(mask)
    if case=='missing-class':field[field==12]=0
    elif case=='outside-context':context['crop']=[0,0,.1,.1]
    elif case=='wrong-size':mask['bitmap']=encode_bitmap(Image.new('L',(30,30),255),preserve_resolution=True)
    elif case=='invalid-context':context['extra']='unrelated'
    elif case=='wrong-target':mask['semantic_target']='face_skin'
    elif case=='bad-model':field=field.astype(np.float32)
    def predict(patch,coords,labels,guide,**kwargs):
        return np.full((1,patch.height,patch.width),-8,np.float32),np.array([.96],np.float32),{}
    with pytest.raises(ValueError):refine(image,mask,context,
        parser=SimpleNamespace(predict_native=lambda image,points:field),engine=SimpleNamespace(predict_with_prior=predict))
    if case not in ('wrong-target','wrong-size'):assert mask==original


@pytest.mark.parametrize('scope',['current_selection','existing_layers'])
def test_boundary_method_requires_offered_target_conditions(scope):
    current=Recipe().to_dict();workspace={'mask_refinement_available':True,'selection_mask_refinable':True,
        'selection':{'face_part':'lips'},'selection_mask_boundary_refinable':True}
    offered=[{'id':'lip','mask_refinable':True,'mask_part':'lips','recipe':current,'locked':[],'mask_boundary_refinable':True}]
    value={'layer_id':'lip' if scope=='existing_layers' else None,'recipe':None,'method':'boundary'}
    assert validate_request(value,scope,current,offered,workspace)['method']=='boundary'
    workspace['selection_mask_boundary_refinable']=False;offered[0]['mask_boundary_refinable']=False
    with pytest.raises(ValueError):validate_request(value,scope,current,offered,workspace)
    assert validate_request({**value,'method':'exclude'},scope,current,offered,workspace)['method']=='exclude'


@pytest.mark.parametrize('case',['valid','stale','background','wrong-target'])
def test_part_semantic_progress_is_status_only(case):
    import json
    from iphoto.controllers import worker_bridge
    progress={'kind':'object','phase':'semantic_parts','part':1,'total':1}
    active={'id':7,'op':'segment','generation':10,'jobs':[{'hint':{'semantic_target':'object' if case=='wrong-target' else 'face'}}],
        'priority':'low' if case=='background' else 'normal'}
    message={'id':7,'op':'segment','generation':9 if case=='stale' else 10,'progress':progress}
    owner=SimpleNamespace(_pixel_buffer=b'',_pixel_active=active,_generation=10,_closing=False,_status='previous',
        _layers=[{'id':'kept'}],_candidate={'kept':True},_warm_ready_sha='not-ready',changed=SimpleNamespace(emit=lambda:None),
        _pixel_process=SimpleNamespace(readAllStandardOutput=lambda:(json.dumps(message)+'\n').encode()),
        _pump_pixel=lambda:pytest.fail('Progress cannot end a task'))
    worker_bridge._pixel_read(owner)
    assert owner._pixel_active is active and owner._layers==[{'id':'kept'}] and owner._candidate=={'kept':True}
    assert (owner._status!='previous')==(case=='valid')
