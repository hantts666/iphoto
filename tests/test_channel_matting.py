"""Continuous channel alpha, protected support and transactional conversation edits."""
from copy import deepcopy
import json

import numpy as np
from PIL import Image
import pytest

from iphoto.ai_protocol import parse_auto
from iphoto.document import empty_mask, raster_mask
from iphoto.engine import Recipe
from iphoto.masks import encode_bitmap
from iphoto.matting.channels import estimate, suggest, validate_options,whole_options,preview as channel_preview
from iphoto.workspace import Editor
from iphoto.controllers import channel_auto
from test_ai import configure, mock_api, wait_for
from test_editor import settled


def scene():
    truth = np.zeros((160,240), np.float32)
    truth[30:130,55:185] = .6
    truth[50:110,80:95] = 1
    truth[48:114:4,95:173] = .85
    truth[77:83,130:137] = 0
    pixels = np.rint(35+truth*175).astype(np.uint8)
    rgb = np.stack([pixels,pixels,pixels],axis=2)
    alpha = (truth>0).astype(np.uint8)*255
    mask = {**empty_mask(),'label':'透明织物','bitmap':encode_bitmap(Image.fromarray(alpha),sampling='alpha',preserve_resolution=True)}
    options = {'channel':'red','black':35,'white':210,'gamma':1.,'invert':False,'radius':8,'ai':True,'interior':True}
    return Image.fromarray(rgb),truth,mask,options


def test_channel_and_neural_preserve_real_gray_holes_and_distant_support():
    image,truth,mask,options = scene()
    original = deepcopy(mask)
    calls = []
    def ai(crop, guide, progress=None):
        calls.append((crop.size,guide.copy()))
        # ROI is bbox+24px: (31,6,209,154). Truth supplies an independent
        # oracle to isolate alpha processing from neural prediction accuracy.
        expected=np.rint(truth[6:154,31:209]*255).astype(np.uint8)
        expected[guide==0]=0;expected[guide==255]=255
        return expected,1
    result,quality = estimate(image,mask,options,neural=ai)
    alpha=np.asarray(raster_mask(result,image.size))/255
    assert np.abs(alpha-truth).max()<.004
    assert (alpha[:20]==0).all() and (alpha[77:83,130:137]==0).all()
    assert quality['partial_pixels']>9000 and quality['tiles']==1 and calls
    assert mask==original and result['bitmap']['sampling']=='alpha'
    assert result['bitmap']['width']==image.width
    manual,_=estimate(image,mask,{**options,'ai':False})
    assert np.abs(np.asarray(raster_mask(manual,image.size))/255-truth).max()<.004


def test_channel_contrast_does_not_replace_ai_coverage_with_varying_foreground_light():
    image,truth,mask,options=scene()
    # The same 60% coverage can contain different foreground colours. A
    # globally contrasting red plane is not an independent alpha measurement.
    foreground=np.full(truth.shape,210.,np.float32)
    foreground[35:125,110:170]=90
    foreground[35:125,145:170]=205
    rgb=np.rint(35*(1-truth)+foreground*truth).astype(np.uint8)
    image=Image.fromarray(np.repeat(rgb[...,None],3,axis=2))
    expected=np.rint(truth[6:154,31:209]*255).astype(np.uint8)
    def oracle(crop,guide,progress=None):
        predicted=expected.copy()
        predicted[guide==0]=0;predicted[guide==255]=255
        return predicted,1
    output,quality=estimate(image,mask,options,neural=oracle)
    assert quality['contrast_score']>4
    actual=np.asarray(raster_mask(output,image.size))/255
    assert np.abs(actual-truth).max()<.004
    # The explicit channel operation remains useful, but cannot be silently
    # treated as optical truth when its foreground colour assumptions fail.
    manual,_=estimate(image,mask,{**options,'ai':False})
    assert np.abs(np.asarray(raster_mask(manual,image.size))/255-truth).max()>.2


def test_faint_ai_strand_on_bright_background_is_not_zeroed_by_channel_curve():
    image,truth,mask,options=scene()
    pixels=np.array(image)
    # Coarse selection misses a low-coverage wisp on a locally bright backdrop.
    # This is inside the allowed 8px growth band, outside the old binary mask.
    truth[65:70,50:55]=3/255
    pixels[65:70,50:55]=230
    image=Image.fromarray(pixels)
    def oracle(crop,guide,progress=None):
        predicted=np.rint(truth[6:154,31:209]*255).astype(np.uint8)
        predicted[guide==0]=0;predicted[guide==255]=255
        assert (guide[59:64,19:24]==128).all()
        return predicted,1
    result,_=estimate(image,mask,options,neural=oracle)
    actual=np.asarray(raster_mask(result,image.size))
    assert (actual[65:70,50:55]==3).all()
    assert (actual[:20]==0).all()


def test_normal_edge_mode_keeps_opaque_core_and_semantic_exclusions():
    image,_,mask,options=scene()
    protected={**mask,'semantic_target':'face_skin'}
    result,_=estimate(image,protected,{**options,'interior':False,'ai':False})
    old=np.asarray(raster_mask(mask,image.size));new=np.asarray(raster_mask(result,image.size))
    assert np.all(new[old==0]==0) and new[60,110]==255
    assert result['semantic_target']=='face_skin'


def test_whole_photo_channels_create_native_gray_without_semantic_seed_or_model():
    image,truth,_,_=scene();before=image.tobytes()
    options={**whole_options(),'channel':'red','black':35,'white':210}
    def forbidden(*args,**kwargs):pytest.fail('Manual whole-photo channel loaded a model')
    output,quality=estimate(image,empty_mask(True),options,neural=forbidden)
    actual=np.asarray(raster_mask(output,image.size))/255
    assert np.abs(actual-truth).max()<.004 and image.tobytes()==before
    assert quality['whole'] and quality['tiles']==0 and not output.get('color_recovery')
    assert channel_preview(image,empty_mask(True),options,32)[0].tobytes()==raster_mask(output,image.size).tobytes()
    inverted,_=estimate(image,empty_mask(True),{**options,'invert':True},neural=forbidden)
    assert np.abs(np.asarray(raster_mask(inverted,image.size))/255-(1-truth)).max()<.004


@pytest.mark.parametrize('change',[{'ai':True},{'detail':True},{'color':True},{'channel':'auto'}])
def test_whole_photo_channel_cannot_request_unanchored_ai(change):
    with pytest.raises(ValueError,match='先用通道'):
        estimate(scene()[0],empty_mask(True),{**whole_options(),**change})


def test_whole_photo_channel_cannot_override_local_or_semantic_scope():
    image,_,mask,_=scene()
    for protected in (mask,{**empty_mask(True),'semantic_target':'face_skin'}):
        with pytest.raises(ValueError,match='整图通道只用于'):
            estimate(image,protected,whole_options())


@pytest.mark.parametrize('change',[{'black':210},{'gamma':float('nan')},{'radius':False},{'interior':'yes'},{'white':300}])
def test_invalid_channel_controls_are_rejected(change):
    with pytest.raises(ValueError):validate_options({**scene()[3],**change})


def test_missing_anchors_and_full_selection_do_not_invoke_model():
    image,_,mask,options=scene()
    def forbidden(*args,**kwargs):pytest.fail('No reliable anchors')
    with pytest.raises(ValueError,match='参照'):
        estimate(image,mask,{**options,'white':255},neural=forbidden)
    with pytest.raises(ValueError,match='背景'):
        suggest(image,empty_mask(True),8)
    proposed,_=suggest(image,mask,8)
    assert validate_options(proposed)==proposed


def test_native_color_lines_polish_fuzzy_ai_alpha_and_preserve_constraints(monkeypatch):
    from iphoto.matting import channels
    image,truth,mask,options=scene()
    fields=channels._fields
    def weak_contrast(*args):
        alpha,inside,outside,planes,metrics=fields(*args)
        return alpha,inside,outside,planes,[{**item,'score':1.5} for item in metrics]
    monkeypatch.setattr(channels,'_fields',weak_contrast)
    def fuzzy_ai(crop,guide,progress=None):
        expected=np.rint(truth[6:154,31:209]*255*.7).astype(np.uint8)
        expected[guide==0]=0;expected[guide==255]=255
        return expected,1
    before,_=estimate(image,mask,options,neural=fuzzy_ai)
    phases=[]
    after,quality=estimate(image,mask,{**options,'detail':True,'color':True},neural=fuzzy_ai,
                           progress=lambda **value:phases.append(value))
    raw=np.asarray(raster_mask(before,image.size))/255
    fixed=np.asarray(raster_mask(after,image.size))/255
    assert np.abs(fixed-truth).mean()<np.abs(raw-truth).mean()/5
    assert quality['native_detail'] and quality['color_recovery'] and after['color_recovery']
    assert phases==[{'phase':'polish'}]
    assert (fixed[:20]==0).all() and (fixed[77:83,130:137]==0).all()


@pytest.mark.parametrize('error',[ValueError('缺少参照'),ImportError('组件缺失')])
def test_unstable_polish_keeps_ai_result_and_reports_failure(monkeypatch,error):
    from iphoto.matting import channels,service
    image,_,mask,options=scene()
    fields=channels._fields
    def weak_contrast(*args):
        alpha,inside,outside,planes,metrics=fields(*args)
        return alpha,inside,outside,planes,[{**item,'score':1.5} for item in metrics]
    monkeypatch.setattr(channels,'_fields',weak_contrast)
    def ai(crop,guide,progress=None):return np.where(guide==128,100,guide).astype(np.uint8),1
    before,_=estimate(image,mask,options,neural=ai)
    def fail(*args,**kwargs):raise error
    monkeypatch.setattr(service,'refine_alpha',fail)
    after,quality=estimate(image,mask,{**options,'detail':True},neural=ai)
    assert after==before and not quality['native_detail'] and quality['warnings']


def test_missing_color_component_keeps_alpha_and_reports_no_recovery(monkeypatch):
    monkeypatch.setattr('iphoto.cutout.available',lambda:False)
    image,_,mask,options=scene()
    before,_=estimate(image,mask,{**options,'ai':False})
    after,quality=estimate(image,mask,{**options,'ai':False,'color':True})
    assert after==before and not quality['color_recovery'] and quality['warnings']


def plan(scope='current_layer'):
    return {'action':'channel_mask','scope':scope,'summary':'结合通道与 AI 修透明边缘',
            'recipe':Recipe().to_dict(),'regions':[],'layer_edits':[], 'group':None,
            'repairs':[],'mask_refinement':None,'strategy':None,'edit_prompt':None}


def completion(value):
    return {'choices':[{'finish_reason':'stop','message':{'content':json.dumps(value,ensure_ascii=False)}}]}


def test_conversation_scope_and_capability_cannot_bypass_selection():
    value=plan('current_selection')
    assert parse_auto(completion(value),Recipe().to_dict(),[],current_scope='selection',
                      workspace={'channel_mask_available':True})['action']=='channel_mask'
    with pytest.raises(ValueError):
        parse_auto(completion(plan()),Recipe().to_dict(),[],current_scope='selection',workspace={'channel_mask_available':True})
    with pytest.raises(ValueError,match='能力不可用'):
        parse_auto(completion(value),Recipe().to_dict(),[],current_scope='selection',workspace={'channel_mask_available':False})


@pytest.mark.parametrize('outcome',['complete','cancel','stale','stale_matte','failure','reject_review','cancel_review','stale_review','failure_review'])
def test_channel_auto_real_workers_are_atomic(qt_app,ai_store,tmp_path,outcome):
    image,_,mask,_=scene();path=tmp_path/'cloth.png';image.save(path)
    editor=Editor(ai_store=ai_store)
    try:
        editor.openImage(str(path));wait_for(lambda:editor.hasImage and settled(editor))
        editor._layer()['mask']=deepcopy(mask);editor._load_layer();editor._commit()
        original=deepcopy(editor._layers);cursor=editor._cursor
        def server(payload):
            if '前景颜色检查员' in payload['messages'][0]['content']:
                return completion({'status':'accept','summary':'受控颜色通过','corrections':[]})
            if '通道参数操作员' in payload['messages'][0]['content']:
                return completion({'status':'keep','options':None,'summary':'尚未确认实际抠图质量，继续计算并检查'})
            if '独立抠图质量检查员' in payload['messages'][0]['content']:
                if outcome=='failure_review':return completion({'status':'accept'})
                return completion({'status':'reject' if outcome=='reject_review' else 'accept','summary':'核对实际黑白底边缘'})
            return completion(plan())
        with mock_api(server) as (endpoint,requests):
            configure(editor.ai,endpoint)
            assert editor.sendMessage('用通道和AI修这层薄纱透明内部','auto')
            wait_for(lambda:editor.aiChannelPreparing)
            if outcome=='cancel':editor.selection.cancelTask()
            elif outcome=='stale':editor._generation+=1
            elif outcome=='stale_matte':
                wait_for(lambda:editor.matteBusy)
                editor._generation+=1
            elif outcome=='failure':path.unlink()
            elif outcome in ('cancel_review','stale_review'):
                wait_for(lambda:(editor._pending_request or {}).get('channel_auto',{}).get('reviewing'))
                if outcome=='cancel_review':editor.selection.cancelTask()
                else:editor._generation+=1
            wait_for(lambda:not editor.busy and settled(editor),seconds=45)
        if outcome=='complete':
            assert editor._layers!=original and len(editor._layers)==len(original)
            assert editor._layer()['recipe']==original[-1]['recipe'] and editor._cursor==cursor+1
            assert editor._candidate is None
            result=deepcopy(editor._layers);editor.undo();assert editor._layers==original
            editor.redo();assert editor._layers==result
        else:
            assert editor._layers==original and editor._candidate is None and editor._cursor==cursor
        assert not editor.aiChannelPreparing
        expected=5 if outcome=='failure_review' else 4 if outcome in ('complete','reject_review') else 3 if outcome=='stale_review' else 2 if outcome in ('stale_matte','failure') else 1
        assert len(requests)==expected or outcome=='cancel_review' and len(requests)<=3
        if outcome in ('complete','reject_review','stale_review','failure_review'):
            assert '通道参数操作员' in requests[1][2]['messages'][0]['content']
            assert '前景颜色检查员' in requests[2][2]['messages'][0]['content']
            if outcome!='stale_review':assert '独立抠图质量检查员' in requests[-1][2]['messages'][0]['content']
    finally:editor.close()


def test_stale_channel_callback_does_not_cancel_new_transaction(qt_app,ai_store,tmp_path):
    image,_,mask,_=scene();path=tmp_path/'cloth.png';image.save(path)
    editor=Editor(ai_store=ai_store)
    try:
        editor.openImage(str(path));wait_for(lambda:editor.hasImage and settled(editor))
        assert channel_auto._current(editor,'old') is None
        editor.drawDraft('rect','replace',[[.2,.2],[.8,.8]],.025)
        wait_for(lambda:settled(editor));before=deepcopy(editor._candidate)
        channel_auto.complete(editor,{'mask':mask},'old')
        assert editor._candidate==before
    finally:editor.close()


def test_late_preview_during_debounce_cannot_overwrite_new_control(qt_app,ai_store,tmp_path):
    image,_,mask,_=scene();path=tmp_path/'cloth.png';image.save(path)
    editor=Editor(ai_store=ai_store)
    try:
        editor.openImage(str(path));wait_for(lambda:editor.hasImage and settled(editor))
        editor._set_candidate(mask);wait_for(lambda:settled(editor));channel=editor.channelMask
        channel.open();wait_for(lambda:not channel.loading and channel.previewUrl)
        context={'token':channel.state['token'],'revision':channel.state['revision']}
        old=dict(channel.options);channel.setOption('white',210)
        channel.ready({'options':old,'path':str(path),'channel':'red','score':5},context,editor._generation)
        assert channel.options['white']==210 and channel.loading
        wait_for(lambda:not channel.loading)
        assert channel.options['white']==210
    finally:editor.close()


@pytest.mark.parametrize('rgba',[False,True])
def test_native_cutout_and_gray_export_leave_draft_and_layers_exact(qt_app,ai_store,tmp_path,rgba):
    image,_,mask,_=scene()
    if rgba:
        image=image.convert('RGBA');image.putalpha(180)
    path=tmp_path/'source.png';image.save(path)
    editor=Editor(ai_store=ai_store)
    try:
        editor.openImage(str(path));wait_for(lambda:editor.hasImage and settled(editor))
        editor._set_candidate(mask);wait_for(lambda:settled(editor))
        before=deepcopy((editor._candidate,editor._layers,editor._cursor,editor._draft_history,editor._generation))
        alpha=np.asarray(raster_mask(mask,image.size))
        for output in ('cutout','mask'):
            target=tmp_path/f'{output}.png'
            assert editor.exportRange(str(target),output)
            wait_for(lambda:target.exists() and not editor.busy,seconds=20)
            with Image.open(target) as result:
                assert result.size==image.size
                if output=='mask':
                    assert result.mode=='L' and np.array_equal(result,alpha)
                else:
                    assert result.mode=='RGBA'
                    expected=((alpha.astype(np.uint16)*180)//255).astype(np.uint8) if rgba else alpha
                    assert np.array_equal(result.getchannel('A'),expected)
                    assert np.array_equal(np.array(result)[:,:,:3],np.array(image.convert('RGB')))
                    assert result.info.get('icc_profile')
            assert before==(editor._candidate,editor._layers,editor._cursor,editor._draft_history,editor._generation)
        assert not editor.exportRange(str(path),'mask')
        assert image.tobytes()==Image.open(path).tobytes()
    finally:editor.close()


def test_cutout_uses_pending_bound_layer_mask_in_its_temporary_composite(qt_app,ai_store,tmp_path):
    from iphoto.document import render_layers
    image=Image.new('RGB',(240,160),(85,110,140));path=tmp_path/'source.png';image.save(path)
    editor=Editor(ai_store=ai_store)
    try:
        editor.openImage(str(path));wait_for(lambda:editor.hasImage and settled(editor))
        mask=empty_mask();alpha=Image.new('L',image.size);alpha.paste(255,(30,20,100,140))
        mask['bitmap']=encode_bitmap(alpha,sampling='alpha',preserve_resolution=True)
        editor._layer()['mask']=mask;editor._load_layer();editor.setParameter('exposure',.8);editor.finishGesture()
        wait_for(lambda:settled(editor));original=deepcopy(editor._layers)
        editor.beginSelection('current');wait_for(lambda:settled(editor))
        alpha.paste(255,(100,20,150,140));mask={**mask,'bitmap':encode_bitmap(alpha,sampling='alpha',preserve_resolution=True)}
        editor._set_candidate(mask);wait_for(lambda:settled(editor))
        expected=deepcopy(original);expected[-1]['mask']=mask
        target=tmp_path/'expanded.png';assert editor.exportRange(str(target),'cutout')
        wait_for(lambda:target.exists() and not editor.busy)
        with Image.open(target) as result:
            assert np.array_equal(np.array(result)[:,:,:3],render_layers(image,expected))
            assert result.getchannel('A').tobytes()==alpha.tobytes()
        assert editor._layers==original and editor._candidate==mask
    finally:editor.close()
