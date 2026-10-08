"""Native local hair alpha from portrait support, semantic parts and exact scope.

Portrait alpha recovers outward strands without the seed's 32px growth limit.
Head parsing excludes skin, hat and clothing. Both are guides; only the painted
or reviewed area is committed. Whole-head automatic matting is not exposed.
"""
from copy import deepcopy
from time import perf_counter
import cv2
import numpy as np
from PIL import Image
from scipy.ndimage import distance_transform_edt

from ..document import empty_mask, raster_mask, validate_mask
from ..masks import encode_bitmap
from ..segmentation.prompts import validate_points
from . import neural
from .local import stroke_mask
from .metadata import copy_metadata

MAX_CONTEXT = 8_000_000


class HairContext:
    def __init__(self, image, original, *, portrait=None, parser=None, progress=None, learned=None):
        self.learned = learned
        bounds = original.getbbox()
        if bounds is None or original.getextrema() == (255,255):
            raise ValueError('请先定位人物头发，再涂抹需要修正的发丝边缘')
        self.box = (max(0,bounds[0]-256),max(0,bounds[1]-512),
                    min(image.width,bounds[2]+256),min(image.height,bounds[3]+128))
        if (self.box[2]-self.box[0])*(self.box[3]-self.box[1]) > MAX_CONTEXT:
            raise ValueError('头发定位范围过大，请先限定单个人物的头发')
        if progress: progress(phase='portrait')
        if portrait is None:
            from .portrait import PortraitMatte
            portrait = PortraitMatte()
        self.parent = portrait.predict_image(image)
        self.fallback = portrait.fallback
        if progress: progress(phase='hair_partition')
        if parser is None:
            from ..segmentation.face_skin import backend
            parser = backend()
        from ..segmentation.face_skin import native_labels
        context = image.crop(self.box)
        self.labels = Image.fromarray(native_labels(parser._scores(context),context.size))
        old = np.asarray(original.crop(self.box))
        labels = np.asarray(self.labels)
        selected = old >= 245
        if selected.sum() < 64 or (selected & (labels==17)).sum()/selected.sum() < .5:
            raise ValueError('当前范围不能可靠确认为头发，请使用通用细化或重新定位头发')

    def solve(self, image, previous, box, *, semantic=None, semantic_radius=32, semantic_band=None, points=None, engine=None, progress=None):
        points=validate_points(points or [],allow_strands=semantic is not None,max_points=8 if semantic is not None else 6)
        if (max(box[0],self.box[0])>=min(box[2],self.box[2])
                or max(box[1],self.box[1])>=min(box[3],self.box[3])):
            raise ValueError('笔触超出头发定位上下文，请在目标附近分次修边')
        crop = image.crop(box)
        local = (box[0]-self.box[0],box[1]-self.box[1],box[2]-self.box[0],box[3]-self.box[1])
        labels = np.asarray(self.labels.crop(local))
        coarse = np.asarray(self.parent.crop(box))
        trimap = np.full(coarse.shape,128,np.uint8)
        kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE,(25,25))
        trimap[cv2.erode((coarse>250).astype(np.uint8),kernel)>0] = 255
        trimap[cv2.erode((coarse<2).astype(np.uint8),kernel)>0] = 0
        prior = np.asarray(previous)
        if semantic_band is not None and (semantic is None or not isinstance(semantic_band,np.ndarray)
                or semantic_band.dtype!=np.uint8 or semantic_band.shape!=prior.shape
                or not ((semantic_band>=12)&(semantic_band<=48)).all()):
            raise ValueError('发丝纠错边缘宽度图无效，原范围保留')
        anchors = {}
        yy,xx = np.ogrid[:prior.shape[0],:prior.shape[1]]
        for x,y,label in points:
            x = round(x*(image.width-1))-box[0]; y = round(y*(image.height-1))-box[1]
            if not (0<=x<crop.width and 0<=y<crop.height): continue
            if (y,x) in anchors and anchors[y,x]!=label:
                raise ValueError('保留点和排除点重叠，请先修正提示点')
            if label==1 and (prior[y,x]<245 or labels[y,x]!=17):
                raise ValueError('保留点不能确认为不透明头发，原范围保留')
            if label==2:
                if labels[y,x] in tuple(range(1,17))+(18,):
                    raise ValueError('发丝参照不能落在皮肤、帽子或衣物上，原范围保留')
                # A missing wisp can have zero coarse person support. Reopen
                # only its tiny observed neighborhood; never paint it opaque.
                trimap[(xx-x)**2+(yy-y)**2<=2**2]=128
            anchors[y,x]=label
        engine = engine or neural.backend()
        def report(phase):
            if progress: progress(phase=phase)
            return lambda tile,tiles: progress(tile,tiles,phase=phase) if progress else None
        learned_detail = {}
        if self.learned is None:
            parent, outer_tiles = neural.solve(crop,trimap,engine=engine,progress=report('hair_outer'))
        else:
            native_points = [[int(x), int(y), int(label)] for (y,x),label in anchors.items()]
            trimap, learned_detail = self.learned.predict(crop,native_points,progress=progress)
            if (not isinstance(trimap,np.ndarray) or trimap.dtype!=np.uint8
                    or trimap.shape!=prior.shape or not np.isin(trimap,[0,128,255]).all()):
                raise ValueError('AI 发丝区域无效，原范围保留')
            trimap = trimap.copy()
            parent, outer_tiles = coarse, 0
        target = (prior>127) if semantic is None else np.asarray(semantic,dtype=bool)
        hard = (target & (labels==17)).astype(np.uint8)
        inside = cv2.distanceTransform(np.pad(hard,1),cv2.DIST_L2,5)[1:-1,1:-1]
        if self.learned is None:
            trimap = np.full(prior.shape,128,np.uint8)
            trimap[(inside>32)&(parent>250)] = 255
        nonhair = np.isin(labels,tuple(range(1,17))+(18,)).astype(np.uint8)
        protected = cv2.erode(nonhair,cv2.getStructuringElement(cv2.MORPH_ELLIPSE,(33,33)))>0
        if self.learned is not None: protected = nonhair>0
        trimap[protected | ((labels==0)&(parent>245))] = 0
        if self.learned is None: trimap[parent==0] = 0
        # The original strategy bounds uncertainty by semantic distance.
        # Learned uncertainty replaces that binary boundary, not explicit N
        # observations or head-part protection. Both keep exact edit scopes.
        if semantic is not None:
            if type(semantic_radius) is not int or not 12<=semantic_radius<=48:
                raise ValueError('发丝纠错边缘宽度无效，原范围保留')
            width = semantic_radius if semantic_band is None else semantic_band
            if self.learned is None:
                outside = cv2.distanceTransform((~target).astype(np.uint8),cv2.DIST_L2,5)
                trimap[outside>width] = 0
            else:
                # A binary semantic boundary is not a background observation.
                # Preserve the model's learned uncertainty and fine foreground;
                # the requested band still protects known opaque hair interiors.
                trimap[(inside>width)&(prior>=245)&(labels==17)] = 255
                learned_detail['trimap_policy']='learned_uncertainty_with_nonhair_protection'
        for (y,x),label in anchors.items():
            if label==1 and self.learned is None and parent[y,x]<128:
                raise ValueError('保留点不能确认为不透明头发，原范围保留')
            radius = 8 if label==1 else 2
            trimap[(xx-x)**2+(yy-y)**2<=radius**2] = 128 if label==2 else 255 if label else 0
        if (trimap==255).sum()<16 or (trimap==0).sum()<16:
            raise ValueError('附近缺少可靠的头发或背景，请保留部分内部参照')
        pixels, split_tiles = neural.solve(crop,trimap,engine=engine,progress=report('hair_split'))
        if self.learned is None: pixels = np.minimum(pixels,parent)
        else: pixels[protected] = 0
        for (y,x),label in anchors.items():
            if label!=2:pixels[y,x] = 255 if label else 0
        return pixels, {**learned_detail,'outer_tiles':outer_tiles,'split_tiles':split_tiles,
                        'unknown_pixels':int((trimap==128).sum()),'tiles':outer_tiles+split_tiles}


def refine_local(image, mask, stroke, *, points=None, progress=None, engine=None, portrait=None, parser=None):
    started = perf_counter()
    mask = validate_mask(mask)
    if mask.get('semantic_target'):
        raise ValueError('皮肤或五官范围请使用通用细化，人物发丝模式只用于头发')
    points = validate_points(points or [])
    scope_image = stroke_mask(stroke,image.size)
    bounds = scope_image.getbbox()
    if bounds is None: raise ValueError('请在发丝边缘涂抹')
    box = (max(0,bounds[0]-96),max(0,bounds[1]-96),min(image.width,bounds[2]+96),min(image.height,bounds[3]+96))
    if (box[2]-box[0])*(box[3]-box[1])>4_000_000:
        raise ValueError('发丝笔触跨度过大，请分区域涂抹')
    scope = np.asarray(scope_image.crop(box))>0
    original = raster_mask(mask,image.size)
    previous = np.asarray(original.crop(box))
    context = HairContext(image,original,portrait=portrait,parser=parser,progress=progress)
    engine = engine or neural.backend()
    pixels, detail = context.solve(image,previous,box,points=points,engine=engine,progress=progress)
    join = max(2,min(32,stroke['radius']*min(image.size)*.5))
    weight = np.clip(distance_transform_edt(scope)/join,0,1)
    weight = weight*weight*(3-2*weight)
    pixels = np.rint(previous+weight*(pixels.astype(np.float32)-previous)).astype(np.uint8)
    for x,y,label in points:
        x=round(x*(image.width-1))-box[0];y=round(y*(image.height-1))-box[1]
        if 0<=x<pixels.shape[1] and 0<=y<pixels.shape[0] and scope[y,x]: pixels[y,x]=255 if label else 0
    changed = scope & (pixels!=previous)
    output = np.array(original)
    output[box[1]:box[3],box[0]:box[2]][changed] = pixels[changed]
    warnings = [value for value in (context.fallback,engine.fallback) if value]
    result = copy_metadata(mask,empty_mask())
    result.update(label=mask['label'],bitmap=encode_bitmap(Image.fromarray(output),sampling='alpha',preserve_resolution=True))
    from ..cutout import available, recovery_box
    try:
        if not available(): raise ValueError('前景颜色组件未安装')
        recovery_box(Image.fromarray(output))
        result['color_recovery'] = True
    except ValueError as exc: warnings.append('未去背景串色：'+str(exc))
    quality = {**detail,'hair_matting':True,'local_refinement':True,'backend':'MODNet + BiSeNet + ViTMatte-S',
               'provider':engine.provider,'elapsed_ms':round((perf_counter()-started)*1000,1),
               'scope_box':list(box),'scope_pixels':int(scope.sum()),'changed_pixels':int(changed.sum()),
               'partial_pixels':int(((output>0)&(output<255)).sum()),'mask_size':list(image.size),
               'join_width':join,'warnings':warnings,'color_recovery':result.get('color_recovery',False)}
    return (validate_mask(result) if changed.any() else deepcopy(mask)), quality


def refine_edges(image, mask, *, boxes=None, progress=None, engine=None, portrait=None, parser=None):
    """Process bounded native evidence areas before asking vision to review them.

    This is not a whole-head replacement. One shared portrait/head context
    serves at most four crops, and overlapping predictions blend symmetrically.
    """
    started = perf_counter()
    mask = validate_mask(mask)
    if mask.get('semantic_target') or mask['inverted']:
        raise ValueError('人物发丝处理不能修改皮肤或反选范围')
    original = raster_mask(mask,image.size)
    if boxes is None:
        from ..matte_review import edge_boxes
        boxes = [list(box) for box in edge_boxes(original)]
    if (not isinstance(boxes,list) or not 1<=len(boxes)<=4 or any(
            not isinstance(box,(list,tuple)) or len(box)!=4 or any(type(v) is not int for v in box)
            or not 0<=box[0]<box[2]<=image.width or not 0<=box[1]<box[3]<=image.height
            or box[2]-box[0]>512 or box[3]-box[1]>512 for box in boxes)):
        raise ValueError('人物发丝原像素范围无效，原范围保留')
    union = (min(box[0] for box in boxes),min(box[1] for box in boxes),
             max(box[2] for box in boxes),max(box[3] for box in boxes))
    if (union[2]-union[0])*(union[3]-union[1])>MAX_CONTEXT:
        raise ValueError('发丝边缘跨度过大，请分区域修边')
    context = HairContext(image,original,portrait=portrait,parser=parser,progress=progress)
    engine = engine or neural.backend()
    old = np.asarray(original.crop(union))
    total,mass = np.zeros(old.shape,np.float32),np.zeros(old.shape,np.float32)
    records=[]
    for index,core in enumerate(boxes,1):
        if progress: progress(phase='hair_region',region=index,regions=len(boxes))
        box=(max(0,core[0]-96),max(0,core[1]-96),min(image.width,core[2]+96),min(image.height,core[3]+96))
        pixels,detail=context.solve(image,np.asarray(original.crop(box)),box,engine=engine,progress=progress)
        height,width=core[3]-core[1],core[2]-core[0]
        yy,xx=np.ogrid[:height,:width]
        distance=np.minimum(np.minimum(xx+1,width-xx),np.minimum(yy+1,height-yy))
        weight=np.minimum(distance/32,1).astype(np.float32)
        weight=weight*weight*(3-2*weight)
        local=pixels[core[1]-box[1]:core[3]-box[1],core[0]-box[0]:core[2]-box[0]]
        area=np.s_[core[1]-union[1]:core[3]-union[1],core[0]-union[0]:core[2]-union[0]]
        total[area]+=local*weight;mass[area]+=weight
        records.append({'box':list(core),**detail})
    average=total/np.maximum(mass,1e-8)
    pixels=np.rint(old+np.minimum(mass,1)*(average-old)).astype(np.uint8)
    changed=(mass>0)&(pixels!=old)
    output=original.copy()
    output.paste(Image.fromarray(pixels),union[:2])
    result=copy_metadata(mask,empty_mask())
    result.update(label=mask['label'],bitmap=encode_bitmap(output,sampling='alpha',preserve_resolution=True))
    quality={'backend':'MODNet + BiSeNet + ViTMatte-S','hair_matting':True,'edge_refinement':True,
             'provider':engine.provider,'regions':records,'changed_pixels':int(changed.sum()),
             'tiles':sum(record['tiles'] for record in records),'mask_size':list(image.size),
             'elapsed_ms':round((perf_counter()-started)*1000,1),
             'warnings':[value for value in (context.fallback,engine.fallback) if value]}
    return (validate_mask(result) if changed.any() else deepcopy(mask)),quality
