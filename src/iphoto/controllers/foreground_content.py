"""Import paired foreground content without a cloud request or mask-only conversion."""
from pathlib import Path

from PySide6.QtCore import QUrl

from ..document import MAX_LAYERS, new_layer
from .layers import addLocalLayers


def begin(editor, url):
    if not editor.hasImage or editor.busy:
        return False
    if editor.hasSelectionDraft or editor.hasRegionDraft:
        editor._notify('请先完成当前范围，再导入对齐整张画布的透明前景', True)
        return False
    if len(editor._layers) >= MAX_LAYERS:
        editor._notify('图层与组最多32项，透明前景未导入', True)
        return False
    file_url = QUrl(url)
    path = Path(file_url.toLocalFile() if file_url.isLocalFile() else url)
    if not path.is_absolute():
        editor._notify('请选择透明前景文件的完整路径', True)
        return False
    editor._status = '正在导入透明前景，保留颜色与透明度…可取消'
    context = {'binding': editor._document_signature(), 'source_sha256': editor._sha,
               'canvas_size': [editor._width, editor._height]}
    return editor._request('foreground_import', path=str(path), expected_sha256=editor._sha, context=context) is not False


def ready(editor, result, context, generation):
    if (generation != editor._generation or context['source_sha256'] != editor._sha
            or context['binding'] != editor._document_signature()
            or editor.hasSelectionDraft or editor.hasRegionDraft):
        editor._notify('照片或范围已变化，过期透明前景未应用', True)
        return
    if (result['patch']['canvas_size'] != context['canvas_size']
            or result['patch']['box'] != [0, 0, *context['canvas_size']]
            or result['patch'].get('compositing') != 'replace_rgba'):
        raise ValueError('透明前景的画布信息无效，照片未改变')
    layer = new_layer(result['name'], True)
    layer['mask']['label'] = '透明前景 · 整张画布'
    layer['pixel_patch'] = result['patch']
    addLocalLayers(editor, [layer], allow_pixel_patch=True)
    note = '已导入透明前景为独立图层；可对比原图、检查黑白底或一步撤销。'
    if not result['has_clear_background']:
        note += '文件没有完全透明区域，请检查背景。'
    editor._notify(note)


def cancel(editor):
    editor._queue = type(editor._queue)(r for r in editor._queue if r['op'] != 'foreground_import')
    if editor._active and editor._active['op'] == 'foreground_import':
        editor._active['cancelled'] = True
    editor._notify('已取消导入透明前景，照片未改变')
