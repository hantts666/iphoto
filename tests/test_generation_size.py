"""Actual crops must not round below the image endpoint's minimum area."""
from copy import deepcopy
import json
import subprocess
import sys
from types import SimpleNamespace

from PIL import Image
import pytest

from iphoto.document import empty_mask, new_layer
from iphoto.engine import load_source
from iphoto.generation_size import output_size, validate_size
from iphoto.paths import ROOT


@pytest.mark.parametrize('size', [(600, 400), (400, 600), (1536, 193),
                                  (193, 1536), (64, 64), (4016, 6016)])
def test_source_aspect_and_service_area_are_kept(size):
    result = output_size(size)
    assert validate_size(result) == result
    assert 512 * 512 <= result[0] * result[1] <= 1536 * 1536
    assert min(result) >= 192 and max(result) <= 1536
    assert all(side % 8 == 0 for side in result)
    assert abs((result[0] / result[1]) / (size[0] / size[1]) - 1) <= .02


@pytest.mark.parametrize('size', [(600, 400), (400, 600)])
def test_real_worker_prepares_a_legal_crop_without_changing_its_pixels(tmp_path, size):
    image = Image.new('RGB', size, (49, 88, 130))
    path = tmp_path / 'photo.png'
    image.save(path)
    digest = load_source(path).digest
    before = [new_layer('原照片', True)]
    proposed = [new_layer('AI 选区精修')]
    proposed[0]['mask'] = empty_mask(True)
    snapshot = deepcopy(proposed)
    requests = [{'op': 'open', 'id': 1, 'generation': 7, 'path': str(path)},
                {'op': 'generative_crop', 'id': 2, 'generation': 7,
                 'expected_sha256': digest, 'before': before, 'proposed': proposed}]
    child = subprocess.run([sys.executable, str(ROOT/'run.py'), '--worker', str(tmp_path/'cache')],
        input=''.join(json.dumps(item)+'\n' for item in requests), capture_output=True,
        text=True, encoding='utf8', timeout=30)
    replies = [json.loads(line) for line in child.stdout.splitlines()]
    assert child.returncode == 0 and len(replies) == 2 and all(r['ok'] for r in replies), replies
    result = replies[-1]['result']
    assert 512*512 <= result['output_size'][0]*result['output_size'][1] <= 1536*1536
    assert result['box'] == [0, 0, *size] and result['proposed'] == snapshot
    with Image.open(result['path']) as crop:
        assert crop.size == size and crop.tobytes() == image.tobytes()
    assert load_source(path).digest == digest


@pytest.mark.parametrize('size', [[600, 420], [64, 64], [4096, 2048], [True, 512], [2048, 64]])
def test_invalid_size_declines_before_credential_access_or_network(qt_app, size):
    from iphoto.image_edit import ImageEditController
    class Store:
        def resolve_key(self, *_args):
            raise AssertionError('Invalid dimensions must not access credentials')
    controller = ImageEditController(SimpleNamespace(store=Store()))
    failures = []
    controller.failure.connect(failures.append)
    assert controller.start('unused', 'unused', size, 'token', 7) is False
    assert failures == ['AI 图像编辑分辨率无效，照片未改变']
    assert controller.reply is None and not controller.busy
