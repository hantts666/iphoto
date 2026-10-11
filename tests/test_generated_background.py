"""Observed work-background colour, independent compositing truth and constraints."""
from types import SimpleNamespace

import numpy as np
from PIL import Image, ImageDraw
import pytest

from iphoto.generated_background import observe, validate
from iphoto.generated_foreground import white_guide, white_foreground, extract


def work(size=(192,256)):
    # Independently specified background, with enough spatial references and
    # a white garment which must remain foreground despite matching its colour.
    x,y=np.meshgrid(np.arange(size[0])/(size[0]-1),np.arange(size[1])/(size[1]-1))
    field=np.stack((252-x-y,253-x-y,253-x-y),axis=2).astype(np.float32)
    rgb=Image.fromarray(np.rint(field).astype(np.uint8))
    coarse=Image.new('L',size)
    ImageDraw.Draw(coarse).rectangle((60,70,140,240),fill=255)
    ImageDraw.Draw(rgb).rectangle((60,70,140,240),fill='white')
    rgb.putpixel((15,120),(50,70,100))  # model misses a real coloured wisp
    return rgb,coarse,field


def test_observed_gradient_fits_real_samples_and_preserves_white_garment():
    rgb,coarse,expected=work()
    before=rgb.tobytes(),coarse.tobytes()
    field,quality=observe(rgb,coarse)
    assert quality['background_reference']=='observed_near_white'
    assert np.abs(field-expected).max()<.25
    assert quality['background_residual_p95']<.6
    guide=white_guide(rgb,coarse,background=field)
    assert guide[100,100]==255 and guide[5,5]==0 and guide[120,15]==128
    assert (rgb.tobytes(),coarse.tobytes())==before


def test_exact_white_references_keep_existing_path():
    rgb,coarse,_=work()
    rgb=Image.new('RGB',rgb.size,'white')
    field,quality=observe(rgb,coarse)
    assert field is None and quality=={'background_reference':'requested_white'}
    assert np.array_equal(white_guide(rgb,coarse),white_guide(rgb,coarse,background=field))


@pytest.mark.parametrize('case',['dark','coloured','textured','few','cluster','wrong_mode','wrong_size','budget'])
def test_background_calibration_declines_unreliable_references(case,monkeypatch):
    from iphoto import generated_background
    rgb,coarse,_=work()
    if case=='dark':rgb=Image.new('RGB',rgb.size,(240,240,240))
    elif case=='coloured':rgb=Image.new('RGB',rgb.size,(245,252,253))
    elif case=='textured':
        pixels=np.asarray(rgb).copy()
        # The sample grid itself sees unrelated broad light patches; a plane
        # cannot explain them even though all remain near-white and neutral.
        yy,xx=np.indices(pixels.shape[:2]);v=np.where((xx//20+yy//20)%2,246,253).astype(np.uint8)
        pixels[:]=v[:,:,None];rgb=Image.fromarray(pixels)
    elif case=='few':
        coarse=Image.new('L',rgb.size,255);ImageDraw.Draw(coarse).rectangle((0,0,10,10),fill=0)
    elif case=='cluster':
        coarse=Image.new('L',rgb.size,255);ImageDraw.Draw(coarse).rectangle((20,20,80,80),fill=0)
    elif case=='wrong_mode':rgb=rgb.convert('RGBA')
    elif case=='wrong_size':coarse=coarse.resize((128,128))
    else:monkeypatch.setattr(generated_background,'MAX_PIXELS',1000)
    with pytest.raises(ValueError):observe(rgb,coarse)


def test_observed_colour_inverse_matches_independent_known_composites():
    rgb=Image.new('RGB',(4,1));rgb.putdata([(210,212,214),(220,222,224),(250,252,254),(20,40,60)])
    alpha=Image.new('L',rgb.size);alpha.putdata([51,1,0,255])
    field=np.broadcast_to(np.float32([250,252,254]),(1,4,3)).copy()
    before=rgb.tobytes(),alpha.tobytes(),field.tobytes()
    rgba,count=white_foreground(rgb,alpha,background=field)
    assert rgba.getpixel((0,0))==(50,52,54,51)
    assert rgba.getpixel((2,0))==(250,252,254,0) and rgba.getpixel((3,0))==(20,40,60,255)
    assert rgba.getpixel((1,0))[3]==31 and count==1
    pixels=np.asarray(rgba,dtype=np.float32);a=pixels[:,:,3:]/255
    recomposed=pixels[:,:,:3]*a+field*(1-a)
    assert np.abs(recomposed-np.asarray(rgb)).max()<=1
    assert (rgb.tobytes(),alpha.tobytes(),field.tobytes())==before


@pytest.mark.parametrize('case',['shape','dtype','nan','dark','over'])
def test_invalid_background_cannot_enter_guide_or_inverse(case):
    field=np.full((128,128,3),252,dtype=np.float32)
    if case=='shape':field=field[:64]
    elif case=='dtype':field=field.astype(np.uint8)
    elif case=='nan':field[0,0,0]=np.nan
    elif case=='dark':field[0,0,0]=244
    else:field[0,0,0]=256
    rgb=Image.new('RGB',(128,128),'white');coarse=Image.new('L',rgb.size)
    with pytest.raises(ValueError):validate(field,rgb.size)
    with pytest.raises(ValueError):white_guide(rgb,coarse,background=field)
    with pytest.raises(ValueError):white_foreground(rgb,coarse,background=field)


def test_extraction_uses_observed_background_for_same_rgb_and_alpha(monkeypatch):
    from iphoto.matting import portrait,neural
    rgb,coarse,_=work()
    monkeypatch.setattr(portrait,'PortraitMatte',lambda:SimpleNamespace(provider='fixture',predict_image=lambda _:coarse))
    monkeypatch.setattr(neural,'NativeMatte',lambda:SimpleNamespace(provider='fixture'))
    def solve(image,guide,*,engine):
        assert image.tobytes()==rgb.tobytes() and guide[5,5]==0 and guide[100,100]==255
        alpha=np.where(guide==255,255,0).astype(np.uint8);alpha[120,15]=80
        return alpha,1
    monkeypatch.setattr(neural,'solve',solve)
    rgba,quality=extract(rgb)
    assert rgba.getchannel('A').getpixel((5,5))==0
    assert rgba.getpixel((100,100))==(255,255,255,255)
    assert quality['colour_recovery']=='observed_near_white' and not quality['geometry_verified']
    assert rgba.getpixel((15,120))[3]>80
