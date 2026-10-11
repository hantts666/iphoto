"""Both native edge views must carry their own actual source comparison."""
import numpy as np
from PIL import Image, ImageDraw

from iphoto.generated_foreground import review_images


def test_both_edges_include_same_canvas_original_and_actual_white_black_pixels():
    y,x=np.indices((1200,1800))
    source=Image.fromarray(np.stack((x%256,y%256,(x+y)%256),axis=-1).astype('uint8'))
    rgba=Image.new('RGBA',(900,600),(30,80,160,0))
    ImageDraw.Draw(rgba).rectangle((280,80,750,550),fill=(30,80,160,128))
    before=source.tobytes(),rgba.tobytes()
    images=review_images(source,rgba,[200,300,800,700])
    assert len(images)==7
    for index,box in enumerate(((0,44,512,556),(388,44,900,556))):
        pair,label=images[3+index*2]
        black,black_label=images[4+index*2]
        native=tuple(v*2 for v in box)
        expected_source=source.crop(native).resize((512,512),Image.Resampling.LANCZOS)
        expected_white=Image.alpha_composite(Image.new('RGBA',rgba.size,'white'),rgba).convert('RGB').crop(box)
        expected_black=Image.alpha_composite(Image.new('RGBA',rgba.size,'black'),rgba).convert('RGB').crop(box)
        assert pair.size==(1024,512) and black.size==(512,512)
        assert pair.crop((0,0,512,512)).tobytes()==expected_source.tobytes()
        assert pair.crop((512,0,1024,512)).tobytes()==expected_white.tobytes()
        assert black.tobytes()==expected_black.tobytes()
        assert '左原片' in label and '右生成白底' in label and '未验证几何一致' in label
        assert '黑底' in black_label
    assert all(image.mode=='RGB' and max(image.size)<=1280 for image,_ in images)
    assert (source.tobytes(),rgba.tobytes())==before


def test_source_transparency_is_composited_instead_of_revealing_hidden_rgb():
    source=Image.new('RGBA',(600,400),(200,15,80,0))
    ImageDraw.Draw(source).rectangle((300,200,599,399),fill=(30,120,60,255))
    rgba=Image.new('RGBA',(300,200),(30,80,160,128))
    before=source.tobytes()
    images=review_images(source,rgba,[200,300,800,700])
    assert len(images)==5
    visible=Image.alpha_composite(Image.new('RGBA',source.size,'white'),source).convert('RGB')
    assert images[0][0].tobytes()==visible.tobytes()
    expected=visible.resize((300,200),Image.Resampling.LANCZOS)
    assert images[3][0].crop((0,0,300,200)).tobytes()==expected.tobytes()
    assert source.tobytes()==before
