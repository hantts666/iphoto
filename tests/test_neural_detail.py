"""Native context, conservative topology and optional-model failure contracts."""
from copy import deepcopy
from hashlib import sha256
from types import SimpleNamespace
import importlib.util
from pathlib import Path

import numpy as np
from PIL import Image
import pytest

from iphoto.document import empty_mask, raster_mask
from iphoto.masks import encode_bitmap
from iphoto.matting import neural, models
from iphoto.segmentation import detail, service


def bitmap(pixels):
    return {**empty_mask(),"label":"天空", "bitmap":encode_bitmap(Image.fromarray(pixels),sampling="alpha",preserve_resolution=True)}


class ColorEngine:
    provider = "test-color"
    fallback = ""
    def __init__(self,inverted=False):
        self.inputs = []
        self.inverted = inverted
    def predict(self,pixels):
        self.inputs.append(pixels.copy())
        alpha = (pixels[0,0]+1)/2
        return 1-alpha if self.inverted else alpha


@pytest.mark.parametrize("size",[(80,90),(1100,750)])
def test_fixed_tiles_preserve_source_pixels_constraints_and_seams(size):
    width,height = size
    values = np.tile((np.arange(width)%256).astype(np.uint8),(height,1))
    image = Image.fromarray(np.stack([values,values,values],axis=2))
    before = image.tobytes()
    trimap = np.full(values.shape,128,np.uint8)
    trimap[:3] = 0
    trimap[-3:] = 255
    engine = ColorEngine()
    phases = []
    result,tiles = neural.solve(image,trimap,engine=engine,progress=lambda *x:phases.append(x))
    expected = values.copy();expected[:3] = 0;expected[-3:] = 255
    assert np.array_equal(result,expected) and image.tobytes() == before
    assert tiles == len(engine.inputs) and all(p.shape==(1,4,640,640) for p in engine.inputs)
    assert phases == [(index,tiles) for index in range(1,tiles+1)]
    assert np.array_equal(engine.inputs[0][0,:3,:min(576,height),:min(576,width)],
                          (np.array(image,dtype=np.float32)[:576,:576]/127.5-1).transpose(2,0,1))
    if width>640:
        # Adjacent tiles contain the same original 128-pixel shared context.
        assert np.array_equal(engine.inputs[0][0,:,:min(576,height),448:576],
                              engine.inputs[1][0,:,:min(576,height),:128])


def test_definite_trimap_needs_no_model():
    guide = np.array([[0,255],[255,0]],np.uint8)
    assert np.array_equal(neural.solve(Image.new("RGB",(2,2)),guide)[0],guide)


def test_edge_refine_preserves_known_pixels_explicit_anchors_and_mask_metadata(monkeypatch):
    from iphoto.matting.trimap import make_trimap

    seed = np.zeros((64, 96), np.uint8); seed[:,48:] = 255
    seed[20:24,67:71] = 0  # Small protected hole inside the selected object.
    image = Image.new("RGB", (96,64), (90,90,90))
    mask = {**bitmap(seed), "edge_protection": .8}
    snapshot = deepcopy(mask); before = image.tobytes()
    engine = ColorEngine()
    monkeypatch.setattr(neural, "backend", lambda: engine)
    points = [[48/95,10/63,1], [47/95,10/63,0]]
    result, quality = neural.refine(image, mask, 4, points=points)
    actual = np.array(raster_mask(result, image.size))
    guide = make_trimap(Image.fromarray(seed), 4)
    assert np.array_equal(actual[guide==0], seed[guide==0])
    assert np.array_equal(actual[guide==1], seed[guide==1])
    assert actual[10,48] == 255 and actual[10,47] == 0
    assert np.any((actual>0)&(actual<255)) and quality["tiles"] == 1
    assert result["edge_protection"] == .8 and result["label"] == mask["label"]
    assert mask == snapshot and image.tobytes() == before


@pytest.mark.parametrize("points", [[[.1,.5,1]], [[.9,.5,0]], [[48/95,10/63,1],[48/95,10/63,0]], [[True,.5,1]], "invalid"])
def test_invalid_or_conflicting_edge_anchors_fail_before_model_inference(points, monkeypatch):
    seed = np.zeros((64,96),np.uint8);seed[:,48:] = 255
    monkeypatch.setattr(neural, "backend", lambda: pytest.fail("Invalid constraints reached model"))
    with pytest.raises(ValueError):
        neural.refine(Image.new("RGB",(96,64)),bitmap(seed),4,points=points)


def test_face_protected_holes_cannot_be_filled_by_neural_or_classic_edge_refinement(monkeypatch):
    from iphoto.matting import service as matte_service
    seed=np.zeros((64,96),np.uint8);seed[10:54,10:86]=255;seed[28:33,45:50]=0
    binding={'face_id':'local-face-1','source_sha256':'a'*64}
    mask={**bitmap(seed),'semantic_target':'face_skin','face_binding':binding}
    monkeypatch.setattr(neural,'backend',lambda:ColorEngine())
    guides=[]
    def classic(image,guide,**kwargs):
        guides.append(guide.copy())
        return np.rint(guide*255).astype(np.uint8),1
    monkeypatch.setattr(matte_service,'solve_alpha',classic)
    for result,_ in (neural.refine(Image.new('RGB',(96,64),'gray'),mask,8),
                     matte_service.refine_alpha(Image.new('RGB',(96,64),'gray'),mask,8)):
        alpha=np.array(raster_mask(result,(96,64)))
        assert not alpha[seed==0].any() and result['semantic_target']=='face_skin'
        assert result['face_binding']==binding and result['face_binding'] is not binding
    assert not guides[0][seed==0].any()


def test_inverted_face_refinement_does_not_bind_the_background_to_a_face(monkeypatch):
    from iphoto.matting import service as matte_service
    seed=np.zeros((64,96),np.uint8);seed[10:54,10:86]=255
    mask={**bitmap(seed),'semantic_target':'face_skin','inverted':True,
          'face_binding':{'face_id':'local-face-1','source_sha256':'a'*64}}
    monkeypatch.setattr(neural,'backend',lambda:ColorEngine())
    monkeypatch.setattr(matte_service,'solve_alpha',lambda image,guide,**kwargs:(np.rint(guide*255).astype(np.uint8),1))
    for result,_ in (neural.refine(Image.new('RGB',(96,64),'gray'),mask,8),
                     matte_service.refine_alpha(Image.new('RGB',(96,64),'gray'),mask,8)):
        assert 'semantic_target' not in result and 'face_binding' not in result


def test_classical_refinement_preserves_native_face_holes_and_binding(monkeypatch):
    from iphoto.segmentation import classical
    # A source-pixel exclusion that disappears when reduced to the 1280px proxy.
    seed=np.zeros((1600,2000),np.uint8);seed[200:1400,200:1800]=255;seed[801,1001]=0
    binding={'face_id':'local-face-1','source_sha256':'a'*64}
    mask={**bitmap(seed),'semantic_target':'face_skin','face_binding':binding}
    original=deepcopy(mask)
    cv=classical._cv()
    def broad_foreground(image,labels,*args):
        labels[:]=cv.GC_FGD
    monkeypatch.setattr(cv,'grabCut',broad_foreground)
    result,_=classical.refine(Image.new('RGB',(2000,1600),'gray'),mask)
    alpha=np.array(raster_mask(result,(2000,1600)))
    assert not alpha[seed==0].any() and alpha[800,1000]>0
    assert result['bitmap']['width']==2000 and result['bitmap']['height']==1600
    assert result['semantic_target']=='face_skin' and result['face_binding']==binding
    assert result['face_binding'] is not binding and mask==original


@pytest.mark.parametrize("bad",[np.zeros((10,10),np.float32),np.ones((10,10),np.uint8),np.zeros((8,8),np.uint8)])
def test_invalid_guide_does_not_load_a_model(bad,monkeypatch):
    monkeypatch.setattr(neural,"backend",lambda:pytest.fail("Invalid trimap reached ONNX"))
    with pytest.raises(ValueError,match="三分图"):
        neural.solve(Image.new("RGB",(10,10)),bad)


def test_tile_and_unknown_budgets_fail_before_inference(monkeypatch):
    monkeypatch.setattr(neural,"backend",lambda:pytest.fail("Exceeded budget reached ONNX"))
    guide = np.full((600,600),128,np.uint8)
    monkeypatch.setattr(neural,"MAX_TILES",1)
    with pytest.raises(ValueError,match="范围过大"):
        neural.solve(Image.new("RGB",(600,600)),guide)
    monkeypatch.setattr(neural,"MAX_UNKNOWN",100)
    with pytest.raises(ValueError,match="细节过多"):
        neural.solve(Image.new("RGB",(600,600)),guide)


def hole_scene():
    pixels = np.full((320,600),240,np.uint8)
    # A grid creates fine holes inside the coarse object envelope.
    pixels[20:170,20:24] = 10
    for x in range(20,181,20): pixels[20:170,x:x+5] = 10
    for y in range(20,171,20): pixels[y:y+5,20:190] = 10
    pixels[25:170,25] = 90  # A genuinely uncertain antialiased branch edge.
    pixels[230:,300:] = 10  # Unrelated dark background.
    image = Image.fromarray(np.stack([pixels,pixels,pixels],axis=2))
    seed = np.full(pixels.shape,255,np.uint8)
    seed[20:176,20:196] = 0
    seed[230:,300:] = 0
    points = [[450/599,60/319,1],[42/599,82/319,0]]
    return image,seed,points,pixels


def test_high_contrast_recovers_internal_holes_without_touching_unrelated_dark_background():
    image,seed,points,truth = hole_scene()
    before = image.tobytes()
    result,quality = detail.recover(image,bitmap(seed),points,engine=ColorEngine(inverted=True))
    actual = np.array(raster_mask(result,image.size))
    assert actual[50,50] == 255 and truth[50,50] > 127 and seed[50,50] == 0
    assert actual[82,42] == 0 and actual[60,450] == 255
    assert np.array_equal(actual[230:,300:],seed[230:,300:])
    assert np.array_equal(actual[180:225,250:],seed[180:225,250:])
    assert quality["reopened_pixels"] > 1000 and quality["tiles"] > 0
    assert result["label"] == "天空" and result["bitmap"]["sampling"] == "alpha"
    assert image.tobytes() == before


def test_one_pixel_branch_uses_clicked_color_without_averaging_in_sky():
    image,seed,points,_ = hole_scene()
    rgb = np.array(image)
    rgb[45:65,28:33] = 240
    rgb[45:65,30] = 10
    points[1] = [30/599,55/319,0]
    result,quality = detail.recover(Image.fromarray(rgb),bitmap(seed),points,engine=ColorEngine(inverted=True))
    actual = np.array(raster_mask(result,image.size))
    assert actual[55,30] == 0 and actual[55,29] == 255
    assert quality["reopened_pixels"] > 1000


@pytest.mark.parametrize("case",["no_negative","same_color","incoherent","wrong_anchor","large_region","large_roi"])
def test_ambiguous_or_unbounded_topology_retains_the_original(case,monkeypatch):
    image,seed,points,_ = hole_scene()
    if case == "no_negative": points = points[:1]
    if case == "same_color": image = Image.new("RGB",image.size,(180,180,180))
    if case == "incoherent":
        rgb = np.array(image);rgb[118:123,138:143] = 120;image = Image.fromarray(rgb)
        points.append([140/599,120/319,0])
    if case == "wrong_anchor": points[1][0:2] = [450/599,60/319]
    if case == "large_region": seed[10:210,10:210] = 0
    if case == "large_roi": monkeypatch.setattr(detail,"MAX_ROI",100)
    before = deepcopy(bitmap(seed))
    monkeypatch.setattr(detail,"solve",lambda *args,**kwargs:pytest.fail("Unreliable color constraints reached matting"))
    assert detail.recover(image,before,points) is None
    assert before == bitmap(seed)


def test_bad_model_data_is_rejected_before_loading_runtime(tmp_path,monkeypatch):
    path = tmp_path/models.NAME;path.write_bytes(b"wrong")
    monkeypatch.setattr(models,"MODEL_DIR",tmp_path)
    monkeypatch.setattr(models,"SIZE",5)
    with pytest.raises(ValueError,match="校验失败"): models.verified_path()
    monkeypatch.setattr(models,"DIGEST",sha256(b"wrong").hexdigest())
    assert models.verified_path() == path


def test_model_install_rejects_bad_data_without_replacing_previous_verified_file(tmp_path,monkeypatch):
    spec = importlib.util.spec_from_file_location("matting_setup",Path(__file__).resolve().parents[1]/"scripts/setup_matting.py")
    module = importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    directory = tmp_path/"models";directory.mkdir()
    target = directory/models.NAME;target.write_bytes(b"verified")
    digest = sha256(b"verified").hexdigest()
    for owner in (models,module):
        monkeypatch.setattr(owner,"MODEL_DIR",directory)
        monkeypatch.setattr(owner,"SIZE",8)
        monkeypatch.setattr(owner,"DIGEST",digest)
    source = tmp_path/"new.onnx";source.write_bytes(b"corrupt!")
    with pytest.raises(ValueError,match="SHA256"):
        module.install(source)
    assert target.read_bytes() == b"verified"
    source.write_bytes(b"verified")
    assert module.install(source) == target and target.read_bytes() == b"verified"


@pytest.mark.parametrize("response",[np.full((1,1,640,640),np.nan,np.float32),np.zeros((1,1,32,32),np.float32)])
def test_invalid_model_output_is_never_accepted(response):
    model = object.__new__(neural.NativeMatte)
    model._session = SimpleNamespace(run=lambda *args:[response],get_providers=lambda:["CPUExecutionProvider"])
    with pytest.raises(ValueError,match="尺寸或数值"):
        model.predict(np.zeros((1,4,640,640),np.float32))


def test_failed_gpu_retries_identical_input_once_on_cpu():
    calls = []
    pixels = np.zeros((1,4,640,640),np.float32)
    def gpu(*args):
        calls.append(args[1]["pixel_values"])
        raise RuntimeError("Device lost")
    def cpu(*args):
        calls.append(args[1]["pixel_values"])
        return [np.full((1,1,640,640),.4,np.float32)]
    model = object.__new__(neural.NativeMatte)
    model._session = SimpleNamespace(run=gpu,get_providers=lambda:["DmlExecutionProvider"])
    model._create = lambda provider:SimpleNamespace(run=cpu,get_providers=lambda:[provider])
    assert np.all(model.predict(pixels)==.4) and calls[0] is pixels and calls[1] is pixels
    assert model.provider == "CPUExecutionProvider" and "CPU" in model.fallback


def test_native_selection_uses_detail_model_but_background_preview_does_not(monkeypatch):
    image,seed,points,_ = hole_scene()
    monkeypatch.setattr(models,"available",lambda:True)
    class Engine:
        def predict(self,*args):
            return np.where(seed[None]>127,4.,-4.),np.array([.99]),{}
    monkeypatch.setattr(service,"backend",lambda:Engine())
    monkeypatch.setattr(service,"guided_edge",lambda *args,**kwargs:Image.fromarray(seed))
    monkeypatch.setattr(detail,"backend",lambda:ColorEngine(inverted=True))
    monkeypatch.setattr(neural,"backend",lambda:ColorEngine(inverted=True))
    phases = []
    foreground = service.segment_jobs(image,[{"id":"sky","points":points}],source=image,detail_progress=lambda *x:phases.append(x))
    assert foreground["items"][0]["quality"]["detail_recovery"]["reopened_pixels"]>1000
    assert phases[:3] == [("segment",1,1),("details_plan",1,1),("details",1,1)]
    assert phases[3:] and all(p[0]=="details" and len(p)==5 for p in phases[3:])
    phases.clear()
    background = service.segment_jobs(image,[{"id":"sky","points":points}],tolerant=True,detail_progress=lambda *x:phases.append(x))
    assert "original_matting" not in background["items"][0]["quality"]
    assert phases == [("segment",1,1)]
