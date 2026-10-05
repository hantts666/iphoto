"""Dense neural prompt contracts; synthetic masks are not an accuracy score."""
from hashlib import sha256
from copy import deepcopy
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

import numpy as np
from PIL import Image, ImageDraw
import pytest

from iphoto.document import empty_mask, raster_mask, validate_mask, validate_layers, new_layer, render_layers
from iphoto.masks import encode_bitmap
from iphoto.segmentation import precise_sam, semantic_refine, service
from iphoto.controllers import worker_bridge
from iphoto.controllers.selections import _quality_text


def sessions():
    model = precise_sam.PreciseSAM.__new__(precise_sam.PreciseSAM)
    model._key = model._features = None
    encodes, decodes = [], []
    def encode(_, feed):
        encodes.append(feed)
        return [np.zeros(shape, np.float32) for shape in
                ((1, 32, 256, 256), (1, 64, 128, 128), (1, 256, 64, 64))]
    def decode(_, feed):
        decodes.append(feed)
        height, width = feed['original_image_size']
        return [np.ones((1, 1, height, width), np.float32), np.ones((1, 1), np.float32)*.91,
                np.ones((1, 1, 256, 256), np.float32)]
    model.encoder = SimpleNamespace(run=encode)
    model.decoder = SimpleNamespace(run=decode)
    return model, encodes, decodes


def test_float_preprocess_matches_native_rgb_without_byte_resize_rounding():
    image = Image.new('RGB', (2, 1)); image.putpixel((1, 0), (255, 130, 90))
    actual = precise_sam.preprocess(image)
    assert actual.shape == (1, 3, 1024, 1024) and actual.dtype == np.float32
    assert np.isfinite(actual).all()
    assert actual[0, :, 0, 0] == pytest.approx(-np.array([.485, .456, .406]) / [.229, .224, .225])
    # Bilinear interpolation of floats retains information below one byte.
    mid = (actual[0, 0, 0, 512]*.229+.485)*255
    assert abs(mid-round(float(mid))) > .01


def test_small_facial_part_is_not_reported_as_a_failed_empty_selection():
    alpha = Image.new('L', (600, 800)); ImageDraw.Draw(alpha).rectangle((280, 390, 290, 399), fill=255)
    mask = {**empty_mask(), 'semantic_target': 'face', 'bitmap': encode_bitmap(alpha, preserve_resolution=True)}
    text = _quality_text(mask)
    assert '局部分区' in text and '放大检查' in text and '几乎为空' not in text and '覆盖约 0%' not in text
    mask.pop('semantic_target')
    assert '几乎为空' in _quality_text(mask)


@pytest.mark.parametrize('part,target', [('nose', 'face_skin'), ('lips', 'face')])
def test_part_metadata_round_trips_without_affecting_source_pixels(part, target):
    mask = {**empty_mask(), 'semantic_target': target, 'face_part': part,
            'ops': [{'kind': 'rect', 'mode': 'add', 'points': [[.2, .2], [.5, .6]]}]}
    layer = new_layer('renamed'); layer['mask'] = mask; layer['recipe']['exposure'] = .2
    assert validate_mask(mask)['face_part'] == part and validate_layers([layer])[0]['mask']['face_part'] == part
    plain = deepcopy(layer); plain['mask'].pop('face_part')
    source = Image.new('RGB', (100, 80), (70, 60, 80))
    assert raster_mask(mask, source.size).tobytes() == raster_mask(plain['mask'], source.size).tobytes()
    assert render_layers(source, [layer]).tobytes() == render_layers(source, [plain]).tobytes()


@pytest.mark.parametrize('part,target', [('all', 'face'), (None, 'face'), ({}, 'face'),
                                       (True, 'face'), ('lips', 'face_skin'), ('nose', 'body_skin')])
def test_invalid_persistent_part_is_rejected(part, target):
    with pytest.raises(ValueError, match='部位'):
        validate_mask({**empty_mask(), 'semantic_target': target, 'face_part': part})


def test_partial_mask_cannot_claim_a_full_face_binding():
    with pytest.raises(ValueError, match='关联'):
        validate_mask({**empty_mask(), 'semantic_target': 'face_skin', 'face_part': 'nose',
                       'face_binding': {'face_id': 'face-1', 'source_sha256': 'a'*64}})


def test_typed_nose_is_not_reused_as_whole_skin_even_with_a_legacy_full_label():
    from test_face_binding import layer, skin, owner, FACE
    from iphoto.controllers import face_inventory
    item = layer(skin(None))
    assert face_inventory.retouch_layer(owner([item]), FACE) is item
    item['mask']['face_part'] = 'nose'
    assert face_inventory.retouch_layer(owner([item]), FACE) is None


@pytest.mark.parametrize('route', ['classic', 'closed_form', 'neural'])
def test_refined_edges_keep_explicit_part_and_protected_zeros(monkeypatch, route):
    image = Image.new('RGB', (96, 80), (70, 50, 60))
    alpha = Image.new('L', image.size); ImageDraw.Draw(alpha).rectangle((20, 20, 75, 60), fill=255)
    ImageDraw.Draw(alpha).rectangle((42, 35, 49, 42), fill=0)
    mask = {**empty_mask(), 'semantic_target': 'face', 'face_part': 'lips',
            'bitmap': encode_bitmap(alpha, sampling='alpha', preserve_resolution=True)}
    if route == 'classic':
        from iphoto.segmentation import classical
        monkeypatch.setattr('cv2.grabCut', lambda *args, **kwargs: None)
        result, _ = classical.refine(image, mask)
    elif route == 'closed_form':
        from iphoto.matting import service as matting
        monkeypatch.setattr(matting, 'solve_alpha', lambda rgb, trimap, **kwargs:
                            (np.where(trimap == 0, 0, 255).astype(np.uint8), 1))
        result, _ = matting.refine_alpha(image, mask, radius=2)
    else:
        from iphoto.matting import neural
        monkeypatch.setattr(neural, 'solve', lambda rgb, trimap, **kwargs:
                            (np.where(trimap == 0, 0, 255).astype(np.uint8), 1))
        monkeypatch.setattr(neural, 'backend', lambda: SimpleNamespace(provider='fixture', fallback=''))
        result, _ = neural.refine(image, mask, radius=2)
    actual = raster_mask(validate_mask(result), image.size)
    assert result['face_part'] == 'lips' and result['semantic_target'] == 'face'
    assert actual.getpixel((45, 38)) == 0


def test_dense_prior_coordinates_and_one_image_cache_do_not_cache_prompt_results():
    model, encodes, decodes = sessions()
    image = Image.new('RGB', (24, 20), 'gray'); guide = Image.new('L', image.size)
    ImageDraw.Draw(guide).rectangle((3, 3, 20, 16), fill=255)
    coords = np.array([[10, 10], [12, 12], [2, 2], [21, 17]], np.float32)
    labels = np.array([1, 0, 2, 3], np.float32); phases = []
    _, _, first = model.predict_with_prior(image, coords, labels, guide, progress=phases.append)
    guide.putpixel((8, 8), 0)
    _, _, second = model.predict_with_prior(image.copy(), coords+1, labels, guide)
    assert len(encodes) == 1 and len(decodes) == 2 and not first['embedding_cached'] and second['embedding_cached']
    assert phases == ['semantic_encode', 'semantic_points']
    assert np.allclose(decodes[0]['point_coords'][0], coords/[24, 20]*1024)
    assert decodes[0]['point_labels'].dtype == np.int32
    assert decodes[0]['input_masks'].shape == (1, 1, 256, 256)
    assert decodes[0]['input_masks'].dtype == np.float32 and np.isfinite(decodes[0]['input_masks']).all()
    assert not np.array_equal(decodes[0]['input_masks'], decodes[1]['input_masks'])
    image.putpixel((0, 0), (1, 2, 3)); model.predict_with_prior(image, coords, labels, guide)
    assert len(encodes) == 2


@pytest.mark.parametrize('failure', ['nan', 'shape', 'dtype', 'score'])
def test_corrupt_neural_output_is_rejected(failure):
    model, _, _ = sessions()
    output = np.zeros((1, 1, 10, 12), np.float32)
    if failure == 'nan': output[0, 0, 0, 0] = np.nan
    if failure == 'shape': output = output[:, :, :5]
    if failure == 'dtype': output = output.astype(np.float64)
    model.decoder.run = lambda *args: [output, np.ones((1, 1), np.float32)*(2 if failure == 'score' else 1),
                                     np.zeros((1, 1, 256, 256), np.float32)]
    with pytest.raises(ValueError, match='预测无效'):
        model.predict_with_prior(Image.new('RGB', (12, 10)), np.array([[3, 3]]), np.array([1]), Image.new('L', (12, 10)))


def test_local_dense_prior_accepts_eight_points_but_rejects_nine_before_encoding():
    model,encodes,decodes=sessions()
    image=Image.new('RGB',(24,20),'gray');guide=Image.new('L',image.size,128)
    coords=np.array([[2+i*2,3+i] for i in range(8)],np.float32)
    labels=np.array([1,0,0,1,1,0,0,1],np.float32)
    model.predict_with_prior(image,coords,labels,guide)
    assert len(encodes)==len(decodes)==1
    assert decodes[0]['point_coords'].shape==(1,8,2) and decodes[0]['point_labels'].shape==(1,8)
    with pytest.raises(ValueError,match='输入无效'):
        model.predict_with_prior(image,np.vstack((coords,[20,15])),np.append(labels,0),guide)
    assert len(encodes)==len(decodes)==1


def test_unbounded_input_is_rejected_before_encoder():
    model, encodes, _ = sessions()
    with pytest.raises(ValueError, match='输入无效'):
        model.predict_with_prior(Image.new('RGB', (12, 10)), [[12, 3]], [1], Image.new('L', (12, 10)))
    assert not encodes


@pytest.mark.parametrize('mode', ['negative', 'positive', 'unconfigured', 'broken', 'whole'])
def test_semantic_dispatch_keeps_exclusions_identity_and_bounds(monkeypatch, mode):
    from test_semantic_refine import Predictor, fixture
    image, prior, hint = fixture()
    if mode != 'whole':
        hint['face_part'] = 'nose'
        hint.pop('face_binding')
    basic, precise = Predictor(), []
    monkeypatch.setattr(precise_sam, 'available', lambda: mode != 'unconfigured')
    def dense(patch, coords, labels, guide, **kwargs):
        precise.append((patch.size, guide.copy()))
        if mode == 'broken': raise RuntimeError('ORT failure')
        logits, scores, timing = Predictor().predict(patch, coords, labels)
        return logits, scores, {**timing, 'model': 'SAM2.1 Small'}
    monkeypatch.setattr(precise_sam, 'backend', lambda **kwargs: SimpleNamespace(predict_with_prior=dense))
    monkeypatch.setattr('iphoto.segmentation.efficient_sam.backend', lambda: basic)
    points = [[320/679, 200/519, int(mode == 'positive')]]
    result, quality = service.segment(image, hint, points)
    assert bool(precise) == (mode in ('negative', 'broken'))
    assert bool(basic.calls) == (mode != 'negative')
    assert quality['model'].startswith('SAM2.1' if mode == 'negative' else 'EfficientSAM')
    assert any('基础神经' in s for s in quality['warnings']) == (mode == 'broken')
    actual, old = np.asarray(raster_mask(result, image.size)), np.asarray(prior)
    assert np.all(actual[old == 0] == 0) and result.get('face_binding') == hint.get('face_binding')
    assert result.get('face_part') == hint.get('face_part')
    assert (actual[200, 320] > 127) == (mode == 'positive')
    left, top, right, bottom = quality['crop_box']; outside = np.ones(old.shape, bool)
    outside[top:bottom, left:right] = False
    assert np.array_equal(actual[outside], old[outside])


def test_small_partition_uses_native_detail_crop_and_keeps_all_prompt_points():
    image = Image.new('RGB', (2400, 3200), 'gray'); alpha = Image.new('L', image.size)
    ImageDraw.Draw(alpha).rectangle((1100, 1500, 1200, 1520), fill=255)
    hint = {**empty_mask(), 'semantic_target': 'face', 'bitmap': encode_bitmap(alpha, preserve_resolution=True)}
    class BoxPredictor:
        def predict(self, patch, coords, labels):
            hard = np.zeros((patch.height, patch.width), bool)
            left, top = np.rint(coords[labels == 2][0]).astype(int)
            right, bottom = np.rint(coords[labels == 3][0]).astype(int)
            hard[top:bottom+1, left:right+1] = True
            return np.where(hard[None], 5., -5.), np.array([.96]), {}
    _, quality = semantic_refine.refine(image, hint, [[1150/2399, 1510/3199, 1]], engine=BoxPredictor())
    assert quality['crop_box'] == [1036, 1436, 1265, 1585]
    _, quality = semantic_refine.refine(image, hint, [[1150/2399, 1510/3199, 1], [900/2399, 1510/3199, 0]], engine=BoxPredictor())
    assert quality['crop_box'][0] <= 900 < quality['crop_box'][2]


def test_pair_install_checks_both_before_publishing_and_resumes_verified_partial(tmp_path, monkeypatch):
    script = Path(__file__).resolve().parents[1]/'scripts/setup_precise_points.py'
    spec = importlib.util.spec_from_file_location('precise_setup_test', script)
    setup = importlib.util.module_from_spec(spec); spec.loader.exec_module(setup)
    files = {'encoder': ('encoder.onnx', 3, sha256(b'one').hexdigest()),
             'decoder': ('decoder.onnx', 3, sha256(b'two').hexdigest())}
    monkeypatch.setattr(precise_sam, 'FILES', files); monkeypatch.setattr(precise_sam, 'MODEL_DIR', tmp_path/'models')
    source = tmp_path/'source'; source.mkdir()
    (source/'encoder.onnx').write_bytes(b'one'); (source/'decoder.onnx').write_bytes(b'bad')
    with pytest.raises(ValueError, match='SHA256'): setup.install(source)
    assert not precise_sam.MODEL_DIR.exists()
    (source/'decoder.onnx').write_bytes(b'two'); precise_sam.MODEL_DIR.mkdir()
    (precise_sam.MODEL_DIR/'encoder.onnx').write_bytes(b'one')
    setup.install(source)
    assert precise_sam.available() and precise_sam.verified_path('decoder').read_bytes() == b'two'
    (precise_sam.MODEL_DIR/'decoder.onnx').write_bytes(b'bad')
    with pytest.raises(ValueError, match='校验失败'): setup.install(source)
    assert (precise_sam.MODEL_DIR/'decoder.onnx').read_bytes() == b'bad'


@pytest.mark.parametrize('case', ['model', 'encode', 'points', 'wrong_id', 'wrong_op', 'old_op',
    'stale', 'old_generation', 'cancelled', 'background', 'closing', 'wrong_target',
    'missing_hint', 'bad_part', 'bad_total', 'boolean', 'tile'])
def test_semantic_progress_keeps_tasks_masks_and_identity_until_complete(case):
    progress = {'kind': 'object', 'phase': 'semantic_'+(case if case in ('model', 'encode', 'points') else 'model'),
                'part': 1, 'total': 1}
    if case == 'bad_part': progress['part'] = 0
    if case == 'bad_total': progress['total'] = 2
    if case == 'boolean': progress['part'] = True
    if case == 'tile': progress.update(tile=1, tiles=1)
    hint = {'semantic_target': 'object' if case == 'wrong_target' else 'face_skin'}
    active = {'id': 7, 'op': 'open' if case == 'old_op' else 'segment',
              'generation': 9 if case == 'old_generation' else 10,
              'jobs': [{'mask_target': 'object', 'hint': None if case == 'missing_hint' else hint}],
              'cancelled': case == 'cancelled', 'priority': 'low' if case == 'background' else 'normal'}
    message = {'id': 8 if case == 'wrong_id' else 7, 'op': 'open' if case == 'wrong_op' else 'segment',
               'generation': 9 if case == 'stale' else 10, 'progress': progress}
    owner = SimpleNamespace(_pixel_buffer=b'', _pixel_active=active, _generation=10, _closing=case == 'closing',
        _status='previous', _layers=[{'id': 'kept'}], _candidate=empty_mask(), _warm_ready_sha='unprepared',
        _pixel_process=SimpleNamespace(readAllStandardOutput=lambda: (json.dumps(message)+'\n').encode()),
        changed=SimpleNamespace(emit=lambda: None), _pump_pixel=lambda: pytest.fail('Progress cannot finish task'))
    before = deepcopy((owner._layers, owner._candidate, owner._generation))
    worker_bridge._pixel_read(owner)
    assert owner._pixel_active is active and owner._warm_ready_sha == 'unprepared'
    assert before == (owner._layers, owner._candidate, owner._generation)
    assert (owner._status != 'previous') == (case in ('model', 'encode', 'points'))
