"""Real crop worker/HTTP/QML routing; controlled masks test transactions only."""
from copy import deepcopy
import base64
from io import BytesIO
import json

from PIL import Image, ImageDraw
import pytest
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest

from iphoto.ai_grounding import crop_pixels, needs_object_detail, object_crop
from iphoto.controllers import object_grounding, pixel_selections
from iphoto.document import empty_mask, raster_mask
from iphoto.engine import Recipe
from iphoto.segmentation.classical import bitmap_mask
from iphoto.segmentation.object_composition import compose
from test_ai import configure, mock_api, wait_for
from test_ai_auto_layers import auto_response
from test_canvas_ui import canvas  # noqa: F401
from test_editor import settled


def mask(box, label="小目标"):
    left, top, right, bottom = box
    return {**empty_mask(), "label": label, "ops": [{"kind": "polygon", "mode": "add",
            "points": [[left,top],[right,top],[right,bottom],[left,bottom]]}]}


def localized_reply(status="selected", point=None):
    result = {"status":status, "summary":"已定位目标实体内部", "box":[100,100,900,900],
              "point":point or [750,750]}
    if status == "unsupported": result.update(box=[],point=[])
    return {"choices":[{"finish_reason":"stop","message":{"content":json.dumps(result)}}]}


def install(ui, cached=True, count=1):
    objects = [{"id":f"object-{index+1}","name":f"装饰{index+1}","category":"饰品",
                "mask":mask(box),"anchor":[(box[0]+box[2])/2,(box[1]+box[3])/2]}
               for index,box in enumerate([[.12,.15,.24,.28],[.57,.55,.70,.69],[.14,.15,.18,.21]][:count])]
    ui.e._scene.set({"summary":"受控范围事务清单","objects":objects})
    if cached:
        for obj in ui.e._scene.catalog["objects"]:
            ui.e._scene.set_precise(obj["id"],bitmap_mask(raster_mask(obj["mask"],(300,200)),obj["name"]),
                                    {"predicted_iou":.6 if obj["id"] != "object-3" else .95,
                                     "warnings":["模型对边界信心较低"]})
    ui.e.changed.emit()
    QTest.qWait(50)
    return deepcopy(ui.e._scene.catalog["objects"])


def delivery(monkeypatch, editor, *, weak_first=False, final_score=.95, omit=False):
    """Only pixel outputs are controlled. Crop preparation is the real worker."""
    calls = []
    original = editor._request
    def request(op, **data):
        if op != "segment": return original(op, **data)
        calls.append(deepcopy(data))
        score = .6 if weak_first and len(calls) == 1 else final_score
        items = []
        for job in data["jobs"]:
            image = raster_mask(job["hint"],(300,200))
            bounds = image.getbbox()
            if bounds:
                l,t,r,b = bounds
                ImageDraw.Draw(image).rectangle((l+(r-l)//3,t+(b-t)//3,l+2*(r-l)//3,t+2*(b-t)//3),fill=0)
            items.append({"id":job["id"],"mask":bitmap_mask(image,"受控修正轮廓"),
                          "quality":{"predicted_iou":score,"elapsed_ms":17,"warnings":[]}})
        result = {"items":[] if omit else items}
        if "composition" in data:
            result["mask"] = compose(data["composition"],items)
        pixel_selections.complete(editor,result,data["context"])
        return True
    monkeypatch.setattr(editor,"_request",request)
    return calls


def done(ui):
    wait_for(lambda:not ui.e.busy and ui.e._pending_request is None and settled(ui.e))


@pytest.mark.parametrize("quality,small,expected", [
    ({"predicted_iou":.6},True,True),({"predicted_iou":.85},True,False),
    ({"predicted_iou":.6,"detail_grounded":True},True,False),
    ({"predicted_iou":.6},False,False),({},True,False),
    ({"predicted_iou":True},True,False),({"predicted_iou":float("nan")},True,False),
])
def test_close_up_only_for_small_weak_results(quality,small,expected):
    assert needs_object_detail(mask([.1,.1,.2,.2] if small else [0,0,1,1]),quality) is expected


def test_bounded_geometry_covers_original_target_and_handles_edges():
    for box in ([.1,.2,.15,.25],[0,0,.1,.1],[.9,.9,1,1]):
        crop = object_crop(mask(box),(4016,6016))
        assert 0 <= crop[0] <= box[0] < box[2] <= crop[2] <= 1
        assert 0 <= crop[1] <= box[1] < box[3] <= crop[3] <= 1
        assert crop_pixels(crop,(4016,6016))[2] > crop_pixels(crop,(4016,6016))[0]
    with pytest.raises(ValueError,match="为空"):
        object_crop(empty_mask(),(4016,6016))


@pytest.mark.parametrize("cached",[True,False])
def test_real_row_click_automatically_grounds_cached_or_first_pixel_result(canvas,monkeypatch,cached):  # noqa: F811
    ui,editor=canvas,canvas.e
    objects=install(ui,cached)
    calls=delivery(monkeypatch,editor,weak_first=not cached)
    before,cursor=deepcopy(editor._layers),editor._cursor
    with mock_api(localized_reply()) as (url,requests):
        configure(editor.ai,url)
        ui.click("sceneSelect_object-1")
        done(ui)
        assert len(requests)==1 and len(calls)==(1 if cached else 2)
        assert editor._layers==before and editor._cursor==cursor and editor.hasSelectionDraft
        assert editor._scene.precise["object-1"]["quality"]["detail_grounded"]
        crop=object_crop(objects[0]["mask"],(2400,1600))
        l,t,r,b=crop_pixels(crop,(2400,1600))
        assert calls[-1]["jobs"][0]["points"][0][:2]==pytest.approx([(l+750/999*(r-l))/2400,(t+750/999*(b-t))/1600])
        assert calls[-1]["jobs"][0]["points"][0][:2]!=objects[0]["anchor"]
        content=requests[0][2]["messages"][1]["content"]
        assert len(content)==2 and json.loads(content[0]["text"])["mode"]=="selection"
        with Image.open(BytesIO(base64.b64decode(content[1]["image_url"]["url"].split(",",1)[1]))) as cropped:
            assert cropped.size==(r-l,b-t) and cropped.width>300
        assert "已按原图局部细节重新定位" in editor.selectionQuality
        wait_for(lambda:ui.find("selectionToLayerButton").property("enabled") and ui.w.property("selectionPreviewReady"))
        ui.click("selectionToLayerButton")
        done(ui)
        assert editor._cursor==cursor+1 and len(editor._layers)==len(before)+1
        editor.undo()
        wait_for(lambda:settled(editor))
        assert editor._layers==before


def test_checked_batch_builds_one_layer_after_grounding_and_cache_reuse_makes_no_request(canvas,monkeypatch):  # noqa: F811
    ui,editor=canvas,canvas.e
    install(ui)
    calls=delivery(monkeypatch,editor,final_score=.7)
    before,cursor=deepcopy(editor._layers),editor._cursor
    with mock_api(localized_reply()) as (url,requests):
        configure(editor.ai,url)
        ui.click("sceneCheck_object-1")
        ui.click("adjustCheckedObjectsButton")
        done(ui)
        assert len(editor._layers)==2 and not editor.hasSelectionDraft and editor._cursor==cursor+1
        assert len(requests)==1 and len(calls)==1
        assert editor._layer()["mask"]["bitmap"]==editor._scene.precise["object-1"]["mask"]["bitmap"]
        editor.undo()
        wait_for(lambda:settled(editor))
        assert editor._layers==before
        editor.selection.rowSelect("object-1","replace")
        done(ui)
        assert editor.hasSelectionDraft and len(requests)==1 and len(calls)==1


@pytest.mark.parametrize("cancel",["button","escape"])
def test_preparation_can_be_cancelled_and_late_crop_is_ignored(canvas,monkeypatch,cancel):  # noqa: F811
    ui,editor=canvas,canvas.e
    install(ui)
    editor.beginSelection()
    editor._set_candidate(mask([.4,.3,.5,.4],"已有范围"))
    wait_for(lambda:settled(editor))
    before=(deepcopy(editor._candidate),deepcopy(editor._layers),deepcopy(editor._scene.precise),editor._cursor,deepcopy(editor._draft_history))
    original,captured=editor._request,[]
    def request(op,**data):
        if op=="object_crop": captured.append(data);return None
        return original(op,**data)
    monkeypatch.setattr(editor,"_request",request)
    with mock_api(localized_reply()) as (url,requests):
        configure(editor.ai,url)
        editor.selection.rowSelect("object-1","add")
        assert editor.aiObjectPreparing and editor.busy and not editor.ai.busy
        assert ui.find("cancelAiRequest").isVisible() and editor.selection.taskKind=="ai"
        assert "原图" in ui.find("aiRequestProgressText").property("text")
        if cancel=="button":ui.click("cancelAiRequest")
        else:QTest.keyClick(ui.w,Qt.Key_Escape)
        assert editor._pending_request is None and not editor.aiObjectPreparing
        object_grounding.crop_ready(editor,{"path":"late.png","crop_size":[1,1]},captured[0]["context"],editor._generation)
        assert not requests and before==(editor._candidate,editor._layers,editor._scene.precise,editor._cursor,editor._draft_history)
        assert editor.conversation[-1]["state"]=="failed"


def test_cancel_during_live_wait_preserves_original_draft_and_cache(canvas):  # noqa: F811
    ui,editor=canvas,canvas.e
    install(ui)
    editor.beginSelection()
    editor._set_candidate(mask([.4,.3,.5,.4]))
    wait_for(lambda:settled(editor))
    before=deepcopy((editor._candidate,editor._layers,editor._scene.precise,editor._draft_history))
    with mock_api(localized_reply(),delay=3) as (url,requests):
        configure(editor.ai,url)
        editor.selection.rowSelect("object-1","add")
        wait_for(lambda:editor.ai.busy and bool(requests))
        assert "1/1" in editor.selection.taskText and "装饰1" in editor.selection.taskText
        ui.w.setProperty("chatOpen",False)
        ui.click("cancelAiRequest")
        done(ui)
        assert before==(editor._candidate,editor._layers,editor._scene.precise,editor._draft_history)
        assert editor.conversation[-1]["state"]=="failed"


@pytest.mark.parametrize("failure",["unsupported","invalid_coordinates","http","missing_pixels"])
def test_localization_failure_preserves_document_and_cache(canvas,monkeypatch,failure):  # noqa: F811
    ui,editor=canvas,canvas.e
    install(ui)
    calls=delivery(monkeypatch,editor,omit=failure=="missing_pixels")
    before=deepcopy((editor._layers,editor._scene.precise))
    response=localized_reply("unsupported") if failure=="unsupported" else localized_reply(point=[1001,400]) if failure=="invalid_coordinates" else localized_reply()
    with mock_api(response,status=401 if failure=="http" else 200) as (url,requests):
        configure(editor.ai,url)
        ui.click("sceneSelect_object-1")
        done(ui)
        assert (editor._layers,editor._scene.precise)==before and editor._cursor==0 and not editor.hasSelectionDraft
        assert editor.conversation[-1]["state"]=="failed"
        assert len(requests)==(2 if failure=="invalid_coordinates" else 1)
        assert len(calls)==(1 if failure=="missing_pixels" else 0)


@pytest.mark.parametrize("stale",["catalog","draft","locks","generation"])
def test_changed_catalog_draft_or_lock_cannot_consume_reply(canvas,stale):  # noqa: F811
    ui,editor=canvas,canvas.e
    install(ui)
    before=deepcopy(editor._layers)
    with mock_api(localized_reply(),delay=.4) as (url,requests):
        configure(editor.ai,url)
        ui.click("sceneSelect_object-1")
        wait_for(lambda:editor.ai.busy and bool(requests))
        if stale=="catalog":editor._scene.set(deepcopy(editor._scene.catalog))
        elif stale=="draft":editor._candidate=mask([.3,.4,.4,.5])
        elif stale=="locks":editor._locked.add("warmth")
        else:editor._generation+=1
        done(ui)
        assert editor._cursor==0 and editor.conversation[-1]["state"]=="failed"
        assert len(editor._layers)==len(before)
        if stale=="draft":assert editor._candidate==mask([.3,.4,.4,.5])
        elif stale=="locks":assert "warmth" in editor._locked
        elif stale=="catalog":assert not editor._scene.precise


def test_two_targets_and_exclusion_wait_for_all_and_compose_original_draft_once(canvas,monkeypatch):  # noqa: F811
    ui,editor=canvas,canvas.e
    install(ui,count=3)
    calls=delivery(monkeypatch,editor)
    editor.beginSelection()
    editor._set_candidate(mask([.3,.1,.5,.3],"旧范围"))
    wait_for(lambda:settled(editor))
    base=deepcopy(editor._candidate)
    with mock_api(localized_reply()) as (url,requests):
        configure(editor.ai,url)
        pixel_selections.select_objects(editor,["object-1","object-2"],"add",exclude=["object-3"])
        done(ui)
        assert len(requests)==2 and len(calls)==1
        assert {job["id"] for job in calls[0]["jobs"]}=={"object-1","object-2"}
        assert calls[0]["composition"]["base"]==base
        assert set(calls[0]["composition"]["cached"])=={"object-3"}
        pixels=raster_mask(editor._candidate,(300,200))
        assert pixels.getpixel((95,30))==255 and pixels.getpixel((120,40))==255  # Existing base remains.
        assert pixels.getpixel((45,33))==0 and pixels.getpixel((34,30))==255  # New target exclusion applies.
        assert editor._candidate["bitmap"]["width"]==2400
        assert editor._cursor==0 and len(editor._layers)==1


def test_explicit_local_combination_skips_cloud_even_for_weak_object(canvas):  # noqa: F811
    ui,editor=canvas,canvas.e
    install(ui)
    with mock_api(localized_reply()) as (url,requests):
        configure(editor.ai,url)
        ui.click("sceneCheck_object-1")
        ui.click("objectCombineMenuButton")
        ui.click("combineObjectsButton")
        done(ui)
        assert not requests and editor.hasSelectionDraft and not editor._pending_request


def test_auto_layer_uses_close_up_after_weak_mask_and_keeps_recipe_and_one_undo(canvas,monkeypatch):  # noqa: F811
    ui,editor=canvas,canvas.e
    calls=delivery(monkeypatch,editor,weak_first=True)
    before=deepcopy(editor._layers)
    region={"name":"局部装饰","reason":"稍微提亮","box":[120,150,240,280],"point":[180,210],"recipe":Recipe(exposure=.25).to_dict()}
    def reply(payload):
        mode=json.loads(payload["messages"][1]["content"][0]["text"])["mode"]
        return auto_response(regions=[region]) if mode=="auto" else localized_reply()
    with mock_api(reply) as (url,requests):
        configure(editor.ai,url)
        editor.sendMessage("只提亮局部装饰，自动分层","auto")
        done(ui)
        assert len(requests)==2 and len(calls)==2
        assert len(editor._layers)==2 and editor._cursor==1 and not editor.hasRegionDraft
        assert editor.parameters==Recipe(exposure=.25).to_dict()
        assert "已按原图局部细节" in editor.conversation[-1]["text"]
        editor.undo()
        wait_for(lambda:settled(editor))
        assert editor._layers==before


@pytest.mark.parametrize("mode",["targets","selection"])
def test_text_selection_keeps_request_through_close_up_callback(canvas,monkeypatch,mode):  # noqa: F811
    ui,editor=canvas,canvas.e
    install(ui)
    calls=delivery(monkeypatch,editor,weak_first=mode=="selection")
    def reply(payload):
        received=json.loads(payload["messages"][1]["content"][0]["text"]).get("mode","targets")
        if received=="targets":
            result={"status":"selected","summary":"选择装饰1","object_ids":["object-1"],"exclude_ids":[]}
            return {"choices":[{"finish_reason":"stop","message":{"content":json.dumps(result)}}]}
        response=localized_reply()
        if len(requests)==1:
            result=json.loads(response["choices"][0]["message"]["content"])
            result.update(box=[120,150,240,280],point=[180,210])
            response["choices"][0]["message"]["content"]=json.dumps(result)
        return response
    with mock_api(reply) as (url,requests):
        configure(editor.ai,url)
        editor.sendMessage("选择装饰1，不包含内部背景",mode)
        done(ui)
        assert len(requests)==2 and editor.hasSelectionDraft and editor._cursor==0
        assert calls[-1]["context"]["detail_grounded_ids"]
        assert editor.conversation[-1]["state"]=="draft"
        assert editor.conversation[-1]["model"]=="test-vision"


def test_worker_crops_original_pixels_with_bounded_output_and_checks_source_sha(canvas,monkeypatch,tmp_path):  # noqa: F811
    ui,editor=canvas,canvas.e
    path=tmp_path/"large-crop-source.png"
    source=Image.new("RGBA",(4800,3200),(73,119,155,255))
    ImageDraw.Draw(source).rectangle((1100,700,1600,1000),fill=(201,111,43,255))
    source.save(path)
    editor.openImage(str(path))
    wait_for(lambda:editor.hasImage and settled(editor) and ui.w.property("previewReady"))
    results=[]
    monkeypatch.setattr(object_grounding,"crop_ready",lambda owner,result,context,generation:results.append(result))
    crop=[.1,.1,.45,.45]
    editor._request("object_crop",crop=crop,source_sha=editor._sha,context={})
    wait_for(lambda:bool(results) and settled(editor))
    rectangle=crop_pixels(crop,source.size)
    expected=source.crop(rectangle)
    expected.thumbnail((1280,1280),Image.Resampling.LANCZOS)
    assert results[0]["crop_size"]==[1680,1120] and results[0]["image_size"]==[1280,853]
    with Image.open(results[0]["path"]) as actual:
        assert actual.size==(1280,853) and actual.tobytes()==expected.tobytes()
    before=deepcopy(editor._layers)
    editor._request("object_crop",crop=crop,source_sha="wrong-source",context={})
    wait_for(lambda:settled(editor))
    assert len(results)==1 and "过期" in editor.status and editor._layers==before
