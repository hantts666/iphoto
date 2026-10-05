"""Native image features propose locations; only a source review assigns identity."""


def propose(source, core, frame, points, bounds):
    import cv2
    import numpy as np
    from ..matte_review import inside_bounds, point_window

    image=source.crop(core).convert('RGB')
    red=np.asarray(image,np.float32)[...,0]/255
    response=np.zeros(red.shape,np.float32)
    # Thin bright and dark structures both matter. Curvature proposes a
    # location, not foreground coverage, opacity or a connection to hair.
    for scale in (1.5,3.,5.):
        smooth=cv2.GaussianBlur(red,(0,0),scale)
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
        response=np.maximum(response,strength)
    allowed=np.zeros(response.shape,bool)
    for point in points:
        if point[2]!=2:continue
        window,_=point_window(source.size,frame,point)
        left,top=max(core[0],window[0]),max(core[1],window[1])
        right,bottom=min(core[2],window[2]),min(core[3],window[3])
        allowed[top-core[1]:bottom-core[1],left-core[0]:right-core[0]]=True
    response[~allowed]=0
    y,x=np.indices(response.shape);candidates=[]
    while len(candidates)<16:
        row,column=np.unravel_index(response.argmax(),response.shape)
        if response[row,column]<.025:break
        coordinate=[round((core[0]+int(column)-frame[0])/max(1,frame[2]-frame[0]-1)*999),
                    round((core[1]+int(row)-frame[1])/max(1,frame[3]-frame[1]-1)*999)]
        response[(y-row)**2+(x-column)**2<24**2]=0
        if (inside_bounds(coordinate,bounds)
                and all((coordinate[0]-p[0])**2+(coordinate[1]-p[1])**2>=32**2 for p in points)):
            candidates.append(coordinate)
    return candidates
