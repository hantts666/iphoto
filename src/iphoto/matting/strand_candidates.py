"""Native image features propose locations; only a source review assigns identity."""


def line_fields(plane):
    """Separate native bright/dark curvature; neither is an alpha estimate."""
    import cv2
    import numpy as np
    bright=np.zeros(plane.shape,np.float32);dark=bright.copy()
    # Thin bright and dark structures both matter. Curvature proposes a
    # location, not foreground coverage, opacity or a connection to hair.
    for scale in (1.5,3.,5.):
        smooth=cv2.GaussianBlur(plane,(0,0),scale)
        xx=cv2.Sobel(smooth,cv2.CV_32F,2,0,ksize=3)*scale**2
        yy=cv2.Sobel(smooth,cv2.CV_32F,0,2,ksize=3)*scale**2
        xy=cv2.Sobel(smooth,cv2.CV_32F,1,1,ksize=3)*scale**2
        delta=np.sqrt((xx-yy)**2+4*xy**2)
        first=(xx+yy-delta)*.5;second=(xx+yy+delta)*.5
        small=np.where(np.abs(first)<np.abs(second),first,second)
        large=np.where(np.abs(first)<np.abs(second),second,first)
        strength=np.exp(-2*(small/np.maximum(np.abs(large),1e-6))**2)
        # Keep amplitude unsaturated: otherwise a high-contrast filament and
        # its side lobes tie at 1, and argmax proposes the adjacent background.
        strength*=np.abs(large)
        bright=np.maximum(bright,np.where(large<0,strength,0))
        dark=np.maximum(dark,np.where(large>=0,strength,0))
    return {'bright':bright,'dark':dark}


def propose(source, core, frame, points, bounds):
    import cv2
    import numpy as np
    from ..matte_review import inside_bounds, point_window

    image=source.crop(core).convert('RGB')
    red=np.asarray(image,np.float32)[...,0]/255
    fields=line_fields(red)
    response=np.maximum(fields['bright'],fields['dark'])
    allowed=np.zeros(response.shape,bool)
    for point in points:
        if point[2]!=2:continue
        window,_=point_window(source.size,frame,point)
        left,top=max(core[0],window[0]),max(core[1],window[1])
        right,bottom=min(core[2],window[2]),min(core[3],window[3])
        allowed[top-core[1]:bottom-core[1],left-core[0]:right-core[0]]=True
    if not allowed.any():return []
    local=response.copy();local[~allowed]=0
    y,x=np.indices(response.shape);candidates=[]
    def offer(row,column):
        coordinate=[round((core[0]+int(column)-frame[0])/max(1,frame[2]-frame[0]-1)*999),
                    round((core[1]+int(row)-frame[1])/max(1,frame[3]-frame[1]-1)*999)]
        if (inside_bounds(coordinate,bounds)
                and all((coordinate[0]-p[0])**2+(coordinate[1]-p[1])**2>=32**2 for p in points)
                and all((coordinate[0]-p[0])**2+(coordinate[1]-p[1])**2>=32**2 for p in candidates)):
            candidates.append(coordinate);return True
        return False
    # Keep local evidence, but reserve half the budget for complete native
    # structures in this same core. A misplaced T window must not hide the
    # real curl elsewhere in the already-authorized correction area.
    while len(candidates)<8:
        row,column=np.unravel_index(local.argmax(),local.shape)
        if local[row,column]<.025:break
        local[(y-row)**2+(x-column)**2<24**2]=0
        offer(row,column)
    structures=[]
    blue=line_fields(np.asarray(image,np.float32)[...,2]/255)
    for polarities in (fields,blue):
        for field in polarities.values():
            for threshold in (.025,.05):
                binary=cv2.morphologyEx((field>=threshold).astype(np.uint8),cv2.MORPH_CLOSE,np.ones((3,3),np.uint8))
                count,labels,stats,_=cv2.connectedComponentsWithStats(binary)
                for identity in range(1,count):
                    left,top,width,height,area=(int(v) for v in stats[identity])
                    if area<48 or area>12000 or max(width,height)<30:continue
                    component=labels[top:top+height,left:left+width]==identity
                    values=field[top:top+height,left:left+width]
                    row,column=np.unravel_index(np.where(component,values,-1).argmax(),component.shape)
                    structures.append((float(np.percentile(values[component],75)),top+row,left+column))
    for _,row,column in sorted(structures,reverse=True):
        if len(candidates)>=16:break
        offer(row,column)
    # Sparse scenes can use the rest of the local peaks. The list is always
    # source-native, deterministic and subject to the same bounds and review.
    while len(candidates)<16:
        row,column=np.unravel_index(local.argmax(),local.shape)
        if local[row,column]<.025:break
        local[(y-row)**2+(x-column)**2<24**2]=0
        offer(row,column)
    return candidates
