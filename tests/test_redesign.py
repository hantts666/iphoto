from copy import deepcopy
from io import BytesIO
import base64
import json

import numpy as np
import pytest
from PIL import Image, ImageDraw

from iphoto.document import empty_mask, new_layer, raster_mask, render_layers, read_project, validate_project, validate_mask
from iphoto.masks import encode_bitmap, validate_bitmap
from iphoto.plugins import refine, wand, bitmap_mask, run_selection
from iphoto.preview_cache import LayerPreviewCache
from iphoto.ai_tasks import parse_regions
from iphoto.engine import Recipe
from iphoto.workspace import Editor
from test_editor import settled
from test_ai import wait_for, configure, mock_api


def scene(size=(320,240)):
    image=Image.new("RGB",size,(35,80,145))
    truth=Image.new("L",size,0)
    bounds=(size[0]*.3,size[1]*.2,size[0]*.7,size[1]*.8)
    ImageDraw.Draw(image).ellipse(bounds,fill=(210,95,30))
    ImageDraw.Draw(truth).ellipse(bounds,fill=255)
    hole=(size[0]*.46,size[1]*.4,size[0]*.54,size[1]*.6)
    ImageDraw.Draw(image).ellipse(hole,fill=(35,80,145))
    ImageDraw.Draw(truth).ellipse(hole,fill=0)
    return image,truth


def box():
    mask=empty_mask()
    mask["ops"]=[{"kind":"rect","mode":"add","points":[[.24,.14],[.76,.86]]}]
    return mask


def iou(a,b):
    a,b=np.asarray(a)>127,np.asarray(b)>127
    return np.logical_and(a,b).sum()/max(1,np.logical_or(a,b).sum())


def test_grabcut_preserves_hole_and_improves_coarse_box(tmp_path):
    image,truth=scene()
    coarse=box()
    result,quality=refine(image,coarse)
    before=iou(raster_mask(coarse,image.size),truth)
    after=iou(raster_mask(result,image.size),truth)
    assert after > .96 and after > before+.2
    assert raster_mask(result,image.size).getpixel((160,120))==0
    assert quality["coverage"]>0
    assert coarse==box()


def test_wand_contiguity_add_subtract_and_validation():
    image=Image.new("RGB",(200,100),"blue")
    ImageDraw.Draw(image).rectangle((90,0,109,99),fill="red")
    mask,_=wand(image,empty_mask(),[.2,.5],0)
    data=np.asarray(raster_mask(mask,image.size))
    assert data[:,:90].min()==255 and data[:,90:].max()==0
    added,_=wand(image,mask,[.8,.5],0,"add")
    removed,_=wand(image,added,[.2,.5],0,"subtract")
    data=np.asarray(raster_mask(removed,image.size))
    assert data[:,:110].max()==0 and data[:,110:].min()==255
    for point in ([True,.2],[float("nan"),0],[-1,0]):
        with pytest.raises(ValueError): wand(image,mask,point)
    with pytest.raises(ValueError): run_selection("download-and-run",image,mask)


def test_bitmap_grayscale_roundtrip_zero_support_and_malformed_assets(tmp_path):
    image,truth=scene((160,120))
    truth.putpixel((65,35),127)
    mask=bitmap_mask(truth,"细碎蒙版")
    assert np.array_equal(raster_mask(validate_mask(mask),image.size),truth)
    layer=new_layer(); layer["mask"]=mask; layer["recipe"]["exposure"]=1
    before=np.asarray(image); after=np.asarray(render_layers(image,[layer]))
    assert np.array_equal(after[np.asarray(truth)==0],before[np.asarray(truth)==0])
    with pytest.raises(ValueError): validate_bitmap({"png":"bad!!","width":100,"height":100})
    with pytest.raises(ValueError): validate_bitmap({"png":"","width":100000,"height":1})
    output=BytesIO(); Image.new("RGB",(2,2)).save(output,format="PNG")
    with pytest.raises(ValueError): validate_bitmap({"png":base64.b64encode(output.getvalue()).decode(),"width":2,"height":2})
    asset=encode_bitmap(Image.new("L",(2400,1200),255))
    assert asset["width"]==2048


def region_completion():
    regions=[]
    for name,points,value in [("主体",[[300,200],[700,200],[700,800],[300,800]],.5),
                              ("左侧背景",[[0,0],[230,0],[230,999],[0,999]],-.3)]:
        regions.append({"name":name,"reason":"分别调整局部亮度","polygons":[points],"recipe":{**Recipe().to_dict(),"exposure":value}})
    return {"choices":[{"finish_reason":"stop","message":{"content":json.dumps({"status":"planned","summary":"主体提亮，左侧压暗，分别建立两层。","regions":regions})}}]}


@pytest.mark.parametrize("mutation",["too_many","missing","invalid","truncated","unsupported_regions"])
def test_reject_invalid_region_plans_without_partial_result(mutation):
    data=region_completion()
    plan=json.loads(data["choices"][0]["message"]["content"])
    if mutation=="too_many": plan["regions"]*=3
    if mutation=="missing": del plan["regions"][1]["recipe"]["exposure"]
    if mutation=="invalid": plan["regions"][0]["polygons"][0][0][0]=1001
    if mutation=="truncated": data["choices"][0]["finish_reason"]="length"
    if mutation=="unsupported_regions": plan["status"]="unsupported"
    data["choices"][0]["message"]["content"]=json.dumps(plan)
    with pytest.raises(ValueError): parse_regions(data)


def test_draft_transaction_separate_undo_and_project_roundtrip(qt_app,ai_store,tmp_path):
    image,_=scene(); path=tmp_path/"photo.png"; image.save(path)
    editor=Editor(ai_store=ai_store)
    try:
        editor.openImage(str(path)); wait_for(lambda: editor.hasImage and settled(editor))
        original=deepcopy(editor._layers)
        editor.beginSelection("empty")
        editor.drawDraft("rect","replace",[[.2,.1],[.8,.9]],.02)
        first=deepcopy(editor._candidate)
        editor.drawDraft("brush","subtract",[[.5,.5]],.05)
        assert editor._layers==original
        editor.undo(); assert editor._candidate==first
        editor.redo(); assert editor._candidate!=first
        editor.discardSelection(); assert editor._layers==original and not editor.hasSelectionDraft
        editor.beginSelection("empty"); editor.drawDraft("rect","replace",[[.3,.2],[.7,.8]],.02)
        candidate=deepcopy(editor._candidate)
        project=tmp_path/"draft.iphoto"; editor.saveProject(str(project))
        editor.discardSelection(); editor.openProject(str(project)); wait_for(lambda: settled(editor))
        assert editor._candidate==candidate
        editor.selectionToLayer()
        assert len(editor.layers)==2 and editor._layer()["mask"]==candidate
        editor.undo(); assert editor._layers==original
        editor.redo(); assert len(editor.layers)==2
    finally: editor.close()


def test_region_plan_preview_atomic_apply_cancel_and_restore(qt_app,ai_store,tmp_path,pixel_protocol_stub):
    image,_=scene(); path=tmp_path/"photo.png"; image.save(path)
    editor=Editor(ai_store=ai_store)
    try:
        editor.openImage(str(path)); wait_for(lambda: editor.hasImage and settled(editor))
        original=deepcopy(editor._layers)
        with mock_api(region_completion()) as (url,requests):
            configure(editor.ai,url); editor.sendMessage("主体提亮，左边暗一点","regions")
            wait_for(lambda: editor.hasRegionDraft and settled(editor))
            assert editor._layers==original and len(editor.regionDrafts)==2
            assert json.loads(requests[0][2]["messages"][1]["content"][0]["text"])["mode"]=="regions"
        editor.exportImage(str(tmp_path/"forbidden.png")); assert not (tmp_path/"forbidden.png").exists()
        project=tmp_path/"regions.iphoto"; editor.saveProject(str(project))
        assert read_project(project)["region_draft"]
        editor.discardRegions(); assert editor._layers==original
        editor.openProject(str(project)); wait_for(lambda: editor.hasRegionDraft and settled(editor))
        editor.toggleRegion(1); wait_for(lambda: settled(editor))
        editor.acceptRegions()
        assert len(editor.layers)==2 and editor.activeLayerName=="主体"
        assert editor.conversation[-2]["state"]=="applied"
        editor.undo(); assert editor._layers==original
        editor.redo(); assert len(editor.layers)==2
        bad=read_project(project); bad["selection_draft"]=empty_mask()
        with pytest.raises(ValueError): validate_project(bad)
    finally: editor.close()


def test_prefix_cache_reuses_lower_layers_and_respects_memory_limit():
    image,_=scene((120,80))
    layers=[new_layer("第一层",True),new_layer("第二层",True),new_layer("第三层",True)]
    for i,layer in enumerate(layers): layer["recipe"]["exposure"]=(i+1)*.1
    cache=LayerPreviewCache(max_bytes=120*80*3*4)
    assert np.array_equal(cache.render(image,layers),render_layers(image,layers))
    layers[2]["recipe"]["contrast"]=15
    result=cache.render(image,layers)
    assert cache.reused==2
    assert np.array_equal(result,render_layers(image,layers))
    assert cache.bytes<=cache.max_bytes
    layers[0]["mask"]=box()
    assert np.array_equal(cache.render(image,layers),render_layers(image,layers))
    assert cache.reused==0
    cache.clear(); assert cache.bytes==0 and not cache.entries


def test_cloud_seed_is_refined_locally_and_raster_context_is_an_image(qt_app,ai_store,tmp_path):
    from iphoto.segmentation.models import available
    if not available(): pytest.skip("Optional EfficientSAM weights not installed")
    from test_layers import selection_completion
    image,truth=scene(); path=tmp_path/"photo.png"; image.save(path)
    editor=Editor(ai_store=ai_store)
    try:
        editor.openImage(str(path)); wait_for(lambda: editor.hasImage and settled(editor))
        seed=selection_completion([[[240,140],[760,140],[760,860],[240,860]]])
        with mock_api(seed) as (url,_):
            configure(editor.ai,url); editor.sendMessage("选中橙色主体","selection")
            wait_for(lambda: editor.hasSelectionDraft and settled(editor), seconds=60)
        assert "bitmap" in editor._candidate
        assert iou(raster_mask(editor._candidate,image.size),truth)>.90
        # Tiny holes can remain filled; real positive/negative correction must
        # recover them, without replacing the previous draft on failure.
        editor.startPixelSelection(True)
        editor.pixelPoint([.35,.5],True)
        wait_for(lambda: settled(editor), seconds=60)
        editor.pixelPoint([.5,.5],False)
        wait_for(lambda: settled(editor), seconds=60)
        assert iou(raster_mask(editor._candidate,image.size),truth)>.98
        assert raster_mask(editor._candidate,image.size).getpixel((160,120))==0
        final=deepcopy(editor._candidate)
        editor.draftAction("invert")
        editor.undo(); assert editor._candidate==final
        editor.redo(); assert editor._candidate["inverted"]
        editor.undo(); assert editor._candidate==final
        editor.acceptSelection(); wait_for(lambda: settled(editor))
        with mock_api() as (url,requests):
            configure(editor.ai,url); editor.sendMessage("稍微提亮主体","advice")
            wait_for(lambda: not editor.busy)
            parts=requests[0][2]["messages"][1]["content"]
            assert len(parts)==4 and parts[-1]["image_url"]["url"].startswith("data:image/png;base64,")
            context=json.loads(parts[0]["text"])
            assert "bitmap" not in context["selection"] and "selection_image" not in context
        project=tmp_path/"bitmap.iphoto"; editor.saveProject(str(project))
        assert read_project(project)["layers"][-1]["mask"]==final
    finally: editor.close()


def test_missing_capability_does_not_create_selection(qt_app,ai_store,tmp_path,monkeypatch):
    path=tmp_path/"photo.png"; scene()[0].save(path)
    editor=Editor(ai_store=ai_store)
    try:
        editor.openImage(str(path)); wait_for(lambda: editor.hasImage and settled(editor))
        monkeypatch.setattr("iphoto.controllers.selections.capabilities",lambda: [{"id":"u2net","available":False,"status":"缺少模型"}])
        editor.refineSelection("u2net")
        assert not editor.hasSelectionDraft and "缺少模型" in editor.status
    finally: editor.close()


def test_optional_local_subject_model_uses_real_weights_and_worker(qt_app,ai_store,tmp_path):
    from iphoto.plugins import MODEL_DIR
    from iphoto.workspace import ROOT
    if not (MODEL_DIR/"u2netp.onnx").is_file(): pytest.skip("Optional U2Net model not installed")
    editor=Editor(ai_store=ai_store)
    try:
        editor.openImage(str(ROOT/"assets/lake.jpg"));wait_for(lambda:editor.hasImage and settled(editor))
        original=deepcopy(editor._layers)
        editor.refineSelection("u2net")
        wait_for(lambda:editor.hasSelectionDraft and settled(editor))
        assert editor._layers==original and "bitmap" in editor._candidate
        pixels=np.asarray(raster_mask(editor._candidate,(320,213)))
        assert pixels.max()==255 and pixels.min()==0 and .01<float(np.mean(pixels>0))<.9
        assert not editor.conversation  # No cloud service or key is involved.
        editor.selectionToLayer()
        assert len(editor.layers)==2 and not editor.hasSelectionDraft
    finally: editor.close()
