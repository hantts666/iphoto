"""Display crops must preserve alpha and never feed back into native masks."""
from copy import deepcopy
import numpy as np
from PIL import Image
import pytest
from iphoto.document import empty_mask
from iphoto.masks import encode_bitmap
from iphoto.matting.channel_view import previews


def test_local_views_share_one_source_crop_and_preserve_continuous_alpha():
    image=Image.new('RGB',(1000,900),(72,94,120))
    old=np.zeros((900,1000),np.uint8);old[400:600,400:600]=255
    mask={**empty_mask(),'bitmap':encode_bitmap(Image.fromarray(old),sampling='alpha',preserve_resolution=True)}
    a=old.copy();a[450:500,450:500]=128;alpha=Image.fromarray(a)
    before=deepcopy(mask),image.tobytes(),alpha.tobytes()
    views,focused=previews(image,alpha,mask,10)
    assert focused and {picture.size for picture in views.values()}=={(260,260)}
    assert views['alpha'].getpixel((90,90))==128
    assert views['white'].getpixel((90,90))==(163,174,187)
    assert views['black'].getpixel((90,90))==(36,47,60)
    assert views['source'].getpixel((0,0))==(72,94,120)
    assert views['white'].getpixel((0,0))==(255,255,255)
    assert (mask,image.tobytes(),alpha.tobytes())==before
    full,focused=previews(image,alpha,mask,10,whole=True)
    assert not focused and {picture.size for picture in full.values()}=={image.size}


def test_preview_rejects_alpha_from_another_size_or_color_image():
    image=Image.new('RGB',(80,80))
    for alpha in (Image.new('L',(40,80)),image):
        with pytest.raises(ValueError,match='尺寸不一致'):
            previews(image,alpha,empty_mask(),8)
