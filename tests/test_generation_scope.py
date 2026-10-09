"""The actual selected area reaches generation as a separate lossless image."""
import base64
from copy import deepcopy
from io import BytesIO
import json
from types import SimpleNamespace

from PIL import Image, ImageDraw
import pytest

from iphoto.ai_protocol import image_data_url
from iphoto.ai_settings import AISettings
from iphoto.document import new_layer, raster_mask
from iphoto.engine import file_hash
from iphoto.generation_scope import SCOPE_PROMPT, validate_scope_reference
from iphoto.image_edit import ImageEditController
from iphoto.masks import encode_bitmap
from iphoto.paths import ROOT


def url(image, format='PNG'):
    buffer = BytesIO()
    image.save(buffer,format=format)
    return 'data:image/'+('jpeg' if format=='JPEG' else 'png')+';base64,'+base64.b64encode(buffer.getvalue()).decode('ascii')


def guide():
    mask = Image.new('L',(640,512))
    d = ImageDraw.Draw(mask)
    d.rectangle((80,50,550,450),fill=255)
    d.rectangle((80,50,95,450),fill=64)
    d.ellipse((250,190,320,260),fill=0)
    return mask


@pytest.mark.parametrize('case', ['missing','empty','aspect','smaller_same_aspect','color','alpha','jpeg','malformed','too_large'])
def test_invalid_reference_declines_before_credentials_or_network(qt_app,case):
    photo = url(Image.new('RGB',(640,512),(70,100,130)))
    mask = guide()
    if case=='empty':mask.paste(0,(0,0,*mask.size))
    elif case=='aspect':mask=mask.resize((512,640))
    elif case=='smaller_same_aspect':mask=mask.resize((320,256))
    elif case=='color':mask=mask.convert('RGB');mask.putpixel((100,100),(255,0,0))
    elif case=='alpha':mask=mask.convert('RGBA')
    elif case=='too_large':mask=mask.resize((1281,512))
    scope = url(mask.convert('RGB') if case=='jpeg' else mask,'JPEG' if case=='jpeg' else 'PNG')
    if case=='malformed':scope='data:image/png;base64,not base64!'
    elif case=='missing':scope=None
    class Store:
        def resolve_key(self,*_):raise AssertionError('Invalid scope must not access credentials')
    controller=ImageEditController(SimpleNamespace(store=Store()))
    controller._send=lambda *_:pytest.fail('Invalid scope must not reach network')
    failures=[];controller.failure.connect(failures.append)
    assert not controller.start(photo,'精修',[640,512],'token',7,scope_url=scope)
    assert len(failures)==1 and controller.reply is None


def test_payload_labels_photo_and_exact_scope_without_rendering_mask_into_photo(qt_app,tmp_path):
    path=tmp_path/'photo.png'
    pixels=Image.new('RGB',(640,512),(70,100,130));pixels.save(path)
    scope_path=tmp_path/'scope.png';guide().save(scope_path)
    photo, scope=image_data_url(path,lossless=True),image_data_url(scope_path,lossless=True)
    validate_scope_reference(photo,scope)
    sent=[]
    ai=SimpleNamespace(settings=AISettings.validated('qwen','https://dashscope.aliyuncs.com/compatible-mode/v1','qwen3.8-max'),
        store=SimpleNamespace(resolve_key=lambda *_:'fixture'),changed=SimpleNamespace(emit=lambda:None))
    controller=ImageEditController(ai)
    controller._send=lambda request,body,stage:sent.append((request,json.loads(body),stage))
    try:
        assert controller.start(photo,'整理发丝',[640,512],'token',7,scope_url=scope)
        assert len(sent)==1 and sent[0][2]=='generate'
        content=sent[0][1]['input']['messages'][0]['content']
        assert content[:2]==[{'image':photo},{'image':scope}]
        decoded_photo=Image.open(BytesIO(base64.b64decode(content[0]['image'].partition(',')[2])))
        assert decoded_photo.format=='PNG' and decoded_photo.tobytes()==pixels.tobytes()
        assert SCOPE_PROMPT in content[2]['text'] and '内部黑色孔洞' in content[2]['text']
        assert '合成一次' in content[2]['text']
        decoded=Image.open(BytesIO(base64.b64decode(content[1]['image'].partition(',')[2])))
        assert decoded.format=='PNG' and decoded.convert('L').tobytes()==guide().tobytes()
        assert decoded.getpixel((90,100))==(64,64,64)
        assert decoded.getpixel((280,220))==(0,0,0)
        assert Image.open(path).tobytes()==pixels.tobytes()
    finally:controller.timer.stop()


def test_real_worker_scope_shares_crop_coordinates_and_preserves_holes_and_gray(tmp_path):
    import subprocess
    import sys
    source=tmp_path/'source.png'
    Image.new('RGB',(2200,1600),(40,80,120)).save(source)
    alpha=Image.new('L',(2200,1600))
    d=ImageDraw.Draw(alpha)
    d.rectangle((150,200,2050,1400),fill=255)
    d.rectangle((150,200,210,1400),fill=64)
    d.ellipse((700,500,1100,900),fill=0)
    proposed=new_layer('生成精修')
    proposed['mask']['bitmap']=encode_bitmap(alpha,sampling='alpha',preserve_resolution=True)
    before=[new_layer('原图',True)]
    snapshot=deepcopy((before,proposed))
    jobs=[{'id':1,'op':'open','generation':7,'path':str(source)},
          {'id':2,'op':'generative_crop','generation':7,'expected_sha256':file_hash(source),
           'before':before,'proposed':[proposed]}]
    child=subprocess.run([sys.executable,str(ROOT/'run.py'),'--worker',str(tmp_path/'cache')],
        input=''.join(json.dumps(job)+'\n' for job in jobs),capture_output=True,text=True,encoding='utf8',timeout=30)
    replies=[json.loads(line) for line in child.stdout.splitlines() if line.startswith('{')]
    assert child.returncode==0 and len(replies)==2 and all(r['ok'] for r in replies)
    result=replies[-1]['result']
    with Image.open(result['scope_path']) as scope,Image.open(result['path']) as photo:
        assert scope.mode=='L' and scope.size==photo.size and max(scope.size)==1280
        expected=raster_mask(proposed['mask'],alpha.size).crop(result['box']).resize(photo.size,Image.Resampling.NEAREST)
        assert scope.tobytes()==expected.tobytes()
        assert {0,64,255}==set(scope.tobytes())
        validate_scope_reference(image_data_url(result['path']),image_data_url(result['scope_path'],lossless=True))
    assert (before,proposed)==snapshot
