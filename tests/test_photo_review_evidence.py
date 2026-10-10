"""Inspection evidence covers edited scopes; fixtures do not prove aesthetics."""
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw

from iphoto.document import empty_mask, new_layer
from iphoto.masks import encode_bitmap
from iphoto.photo_strategy import render_candidate, review_regions


def layer(name, boxes, target=None, size=(640, 480)):
    alpha=Image.new('L',size)
    draw=ImageDraw.Draw(alpha)
    for left,top,right,bottom in boxes:
        draw.rectangle((left,top,right-1,bottom-1),fill=255)
    result=new_layer(name)
    result['mask']={**empty_mask(),'bitmap':encode_bitmap(alpha,sampling='alpha',preserve_resolution=True)}
    if target:result['mask']['semantic_target']=target
    result['recipe']['exposure']=.2
    return result


def test_face_and_disconnected_arms_get_separate_matched_lossless_crops(tmp_path):
    yy,xx=np.indices((480,640))
    image=Image.fromarray(np.stack([xx%256,yy%256,(xx+yy)%256],axis=-1).astype(np.uint8))
    candidates=[new_layer('Overall',True),
                layer('../face',[(220,70,420,240)],'face_skin'),
                layer('Arms',[(100,250,170,430),(470,250,540,430)],'body_skin')]
    boxes=[(184,34,456,276),(68,218,202,462),(438,218,572,462)]
    assert [box for box,_,_ in review_regions(candidates,[],image.size)]==boxes
    result=render_candidate(image,[new_layer('Original',True)],candidates,[],tmp_path,7)
    assert len(result['images'])==8
    assert [item['lossless'] for item in result['images']]==[False,False]+[True]*6
    for index,box in enumerate(boxes):
        before,after=result['images'][2+index*2:4+index*2]
        for item in (before,after):assert Path(item['path']).parent==tmp_path
        with Image.open(before['path']) as picture:
            assert picture.tobytes()==image.crop(box).tobytes()
        with Image.open(after['path']) as picture:
            assert picture.size==(box[2]-box[0],box[3]-box[1])


def test_missing_face_detection_still_inspects_local_edit_and_whole_image_is_not_duplicated():
    scope=layer('Manual skin edit',[(220,70,420,240)])
    regions=review_regions([new_layer('Overall',True),scope],[],(640,480))
    assert len(regions)==1 and regions[0][0]==(184,34,456,276)


def test_known_face_context_is_reserved_when_body_has_multiple_parts():
    arms=layer('Body',[(30,260,80,400),(180,260,230,400),(380,260,430,400)],'body_skin')
    face=layer('Catalog face',[(260,50,360,170)],'face')
    regions=review_regions([arms],[{'mask':face['mask']}],(640,480))
    assert len(regions)==3 and regions[-1][1]=='人脸1'
    assert all(box!=(0,0,640,480) for box,_,_ in regions)


def test_nearly_transparent_body_specks_do_not_consume_inspection_slots():
    body=layer('Arm',[(100,250,170,430)],'body_skin')
    alpha=Image.new('L',(640,480))
    ImageDraw.Draw(alpha).rectangle((100,250,169,429),fill=255)
    ImageDraw.Draw(alpha).rectangle((450,50,452,52),fill=1)
    body['mask']['bitmap']=encode_bitmap(alpha,sampling='alpha',preserve_resolution=True)
    original=body['mask']['bitmap'].copy()
    regions=review_regions([body],[],alpha.size)
    assert len(regions)==1 and regions[0][0]==(68,218,202,462)
    assert body['mask']['bitmap']==original
    # A genuinely translucent mask still gets evidence when it has no core.
    body['mask']['bitmap']=encode_bitmap(alpha.point(lambda v:60 if v else 0),sampling='alpha',preserve_resolution=True)
    assert len(review_regions([body],[],alpha.size))==1
