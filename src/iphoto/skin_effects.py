"""Prepare adjustment transitions in a worker, keeping selection masks intact."""
from copy import deepcopy

from .masks import MAX_MASK_PIXELS,MAX_MASK_SIDE


def prepare_items(items,spec):
    size=spec.get('size');targets=spec.get('targets');seeds=spec.get('seeds',{})
    if (not isinstance(size,list) or len(size)!=2
            or any(type(value) is not int or not 1<=value<=MAX_MASK_SIDE for value in size)
            or size[0]*size[1]>MAX_MASK_PIXELS
            or not isinstance(targets,dict) or not 1<=len(targets)<=4
            or any(not isinstance(key,str) or not key or len(key)>80
                   or target not in ('face','face_skin','body_skin') for key,target in targets.items())
            or not isinstance(seeds,dict) or set(seeds)-set(targets)):
        raise ValueError('皮肤调整过渡的范围或尺寸无效，已有图层保留')
    originals={item['id']:item for item in items}
    if len(originals)!=len(items):raise ValueError('皮肤调整结果重复，已有图层保留')
    merged={**seeds,**originals};prepared={}
    transition={'policy':'inward-v1','size':list(size)}
    from .document import validate_mask
    from .photo_strategy import soften_effect_mask
    for identifier,target in targets.items():
        item=merged.get(identifier)
        if not isinstance(item,dict) or item.get('id')!=identifier or not isinstance(item.get('quality'),dict):
            raise ValueError('皮肤调整缺少完整分区，已有图层保留')
        mask=validate_mask(item['mask'])
        if mask.get('semantic_target')!=target or mask.get('face_part'):
            raise ValueError('皮肤调整过渡与当前分区不一致，已有图层保留')
        # A different object may need a second localization pass. Its staged
        # skin siblings arrive again as seeds and must not fade a second time.
        effect=(mask if item['quality'].get('skin_effect_transition')==transition
                else soften_effect_mask(mask,tuple(size)))
        prepared[identifier]={**deepcopy(item),'mask':effect,
                              'quality':{**item['quality'],'skin_effect_transition':deepcopy(transition)}}
    return [prepared.get(identifier,item) for identifier,item in originals.items()]+[
        prepared[identifier] for identifier in targets if identifier not in originals]
