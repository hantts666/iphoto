"""Export progress cannot publish results, complete jobs, or bypass cancellation."""
from copy import deepcopy
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from PIL import Image
from PySide6.QtCore import QProcess
from PySide6.QtTest import QTest

from iphoto.controllers import export_process
from test_ai import wait_for
from test_import_export import ui  # noqa: F401


class Output:
    def __init__(self):
        self.data = b''

    def readAllStandardOutput(self):
        data, self.data = self.data, b''
        return data

    def state(self):
        return QProcess.NotRunning


class Signal:
    def __init__(self):
        self.values = []

    def emit(self, *args):
        self.values.append(args)


@pytest.fixture
def owner(tmp_path):
    source = tmp_path / 'source.png'
    Image.new('RGB', (4, 3), (40, 60, 80)).save(source)
    folder = tmp_path / '.iphoto-export-fixture'
    folder.mkdir()
    request = dict(id=9, path=str(tmp_path/'export.png'), stage_dir=str(folder),
                   stage_path=str(folder/'export.png'), source_sha='sha')
    obj = SimpleNamespace(_export_request=request, _export_process=Output(), _export_buffer=b'',
                          _export_line_offset=0, _export_phase=0, _export_final_seen=False,
                          _export_aborting=False, _export_cancelled=None, _export_cleanup_pending=[],
                          _closing=False, _path=str(source), _sha='sha', _status=export_process.PHASES[0],
                          changed=Signal(), exportCompleted=Signal(), notifications=[])

    def notify(text, error=False):
        obj._status = text
        obj.notifications.append((text, error))

    obj._notify = notify
    return obj


def feed(owner, *values):
    owner._export_process.data = b''.join((json.dumps(value)+'\n').encode() for value in values)
    export_process.read(owner)


def result(owner):
    return {'id': 9, 'ok': True, 'result': {'stage_path': owner._export_request['stage_path'],
                                         'width': 4, 'height': 3}}


def test_partial_and_batched_frames_keep_job_and_files_untouched(owner):
    before = deepcopy(owner._export_request)
    owner._export_process.data = b'{"id":9,"type":"pro'
    export_process.read(owner)
    assert owner._export_phase == 0 and not owner.changed.values
    owner._export_process.data = b'gress","phase":1}\n{"id":9,"type":"progress","phase":2}\n'
    export_process.read(owner)
    assert owner._export_phase == 2 and owner._status == export_process.PHASES[2]
    assert len(owner.changed.values) == 2
    assert owner._export_request == before and not owner.exportCompleted.values
    assert not Path(before['path']).exists() and not Path(before['stage_path']).exists()


@pytest.mark.parametrize('frame', [
    {'id': 8, 'phase': 1}, {'id': '9', 'phase': 1}, {'id': True, 'phase': 1},
    {'id': 9, 'phase': True}, {'id': 9, 'phase': 1.0}, {'id': 9, 'phase': '1'},
    {'id': 9, 'phase': 0}, {'id': 9, 'phase': 4}, {'id': 9, 'phase': -1},
    {'id': 9, 'phase': None},
])
def test_invalid_or_foreign_progress_cannot_change_status(owner, frame):
    before = deepcopy(owner._export_request)
    feed(owner, {'type': 'progress', **frame})
    assert owner._status == export_process.PHASES[0] and owner._export_phase == 0
    assert not owner.changed.values and not owner.exportCompleted.values
    assert owner._export_request == before


def test_progress_does_not_regress_or_repeat_notifications(owner):
    feed(owner, {'id': 9, 'type': 'progress', 'phase': 3},
         {'id': 9, 'type': 'progress', 'phase': 1},
         {'id': 9, 'type': 'progress', 'phase': 3})
    assert owner._status == export_process.PHASES[3] and len(owner.changed.values) == 1
    assert not owner.exportCompleted.values and not owner.notifications


def test_progress_after_final_response_cannot_change_status(owner):
    feed(owner, result(owner), {'id': 9, 'type': 'progress', 'phase': 3})
    assert owner._export_request and owner._status == export_process.PHASES[0]
    assert not owner.changed.values and not owner.exportCompleted.values


def test_cancelled_job_discards_late_progress_and_result(owner):
    terminal = result(owner)
    request = deepcopy(owner._export_request)
    export_process.cancel(owner)
    message = owner._status
    count = len(owner.changed.values)
    feed(owner, {'id': 9, 'type': 'progress', 'phase': 3}, terminal)
    assert owner._status == message and len(owner.changed.values) == count
    assert owner._export_request is None and owner._export_buffer == b''
    assert len(owner.exportCompleted.values) == 1 and not Path(request['path']).exists()
    assert not Path(request['stage_dir']).exists()


def test_cumulative_output_budget_includes_consumed_progress(owner):
    request = deepcopy(owner._export_request)
    feed(owner, {'id': 9, 'type': 'progress', 'phase': 1})
    owner._export_process.data = b' ' * (1024 * 1024)
    export_process.read(owner)
    assert owner._export_request is None and '数据过大' in owner._status
    assert not Path(request['stage_dir']).exists() and not Path(request['path']).exists()
    assert len(owner.exportCompleted.values) == 1


@pytest.mark.parametrize('reply', ['missing', 'duplicate', 'wrong_id'])
def test_progress_never_replaces_required_single_matching_final(owner, reply):
    request = deepcopy(owner._export_request)
    Path(request['stage_path']).write_bytes(b'protocol fixture, not image-quality evidence')
    values = [{'id': 9, 'type': 'progress', 'phase': 3}]
    if reply == 'duplicate':
        values += [result(owner), result(owner)]
    elif reply == 'wrong_id':
        values += [{**result(owner), 'id': 8}]
    feed(owner, *values)
    export_process.finished(owner, 0, QProcess.NormalExit)
    assert owner._export_request is None and not Path(request['path']).exists()
    assert not Path(request['stage_dir']).exists()
    assert len(owner.exportCompleted.values) == 1 and owner.exportCompleted.values[0][1]


@pytest.mark.parametrize('with_progress', [False, True])
def test_only_final_success_publishes_and_clears_job(owner, with_progress):
    request = deepcopy(owner._export_request)
    payload = Path(owner._path).read_bytes()
    Path(request['stage_path']).write_bytes(payload)
    values = [{'id': 9, 'type': 'progress', 'phase': 1}] if with_progress else []
    feed(owner, *values, result(owner))
    assert owner._export_request and not Path(request['path']).exists()
    export_process.finished(owner, 0, QProcess.NormalExit)
    assert Path(request['path']).read_bytes() == payload
    assert owner._export_request is None and owner.exportCompleted.values == [(request['path'], '')]
    assert not Path(request['stage_dir']).exists()


@pytest.mark.parametrize('size', [(1440, 930), (1080, 700)])
def test_real_export_stages_and_busy_indicator_survive_until_publication(request, size):
    from test_import_export import QPointF, Qt

    editor, window, find, warnings, tmp_path = request.getfixturevalue('ui')
    window.resize(*size)
    QTest.qWait(80)
    original = deepcopy((editor._layers, editor._history, editor._cursor))
    phases = []
    completed = []
    editor.changed.connect(lambda: phases.append(editor.exportProgress) if editor.exportProgress else None)
    editor.exportCompleted.connect(lambda path, error: completed.append((path, error)))
    dialog = find('exportDialog')
    dialog.open();QTest.qWait(80)
    target = tmp_path/'stage-export.png'
    dialog.setProperty('formatIndex', 1)
    dialog.setProperty('filePath', str(target))
    confirm = find('exportConfirmButton')
    point = confirm.mapToScene(QPointF(confirm.width()/2, confirm.height()/2)).toPoint()
    QTest.mouseClick(window, Qt.LeftButton, Qt.NoModifier, point)
    assert dialog.property('pending') and editor.busy
    assert find('exportBusyIndicator').property('running') and find('exportBusyIndicator').isVisible()
    assert find('exportProgressText').property('text') == export_process.PHASES[0]
    assert find('cancelExportButton').property('enabled')
    wait_for(lambda: target.exists() and not editor.busy and not dialog.property('pending'))
    ordered = list(dict.fromkeys(phases))
    assert ordered == list(export_process.PHASES)
    assert editor.exportProgress == '' and not dialog.property('opened')
    assert not find('exportBusyIndicator').property('running')
    assert completed == [(str(target), '')]
    assert (editor._layers, editor._history, editor._cursor) == original
    with Image.open(target) as exported:
        assert exported.size == (300, 200) and exported.getpixel((10, 10)) == (55, 90, 130)
    assert not warnings
