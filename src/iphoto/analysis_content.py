"""Bounded immutable pixel-content snapshots, before adjustment recipes.

Keep applied patches, healing, fill, masks, opacity and group composition.
Tone/detail recipes are excluded so a mask's own effect cannot teach the
next extraction to follow its old boundary. The original photo stays intact.
"""
from copy import deepcopy
from hashlib import sha256
import json
from pathlib import Path

from .document import validate_layers,render_layers,MAX_PROJECT_BYTES
from .engine import Recipe
from .preview_cache import composition_key
from .storage import atomic_output


def project(layers):
    result=deepcopy(validate_layers(layers))
    for layer in result:
        layer['recipe']=Recipe().to_dict()
        layer['locked']=[]
    return result


def validate_reference(value,source):
    if (not isinstance(value,dict) or set(value)!={'path','sha256','source_sha','canvas_size'}
            or not isinstance(value['path'],str) or not Path(value['path']).is_absolute()
            or not isinstance(value['sha256'],str) or len(value['sha256'])!=64
            or any(c not in '0123456789abcdef' for c in value['sha256'])
            or value['source_sha']!=source.digest or not isinstance(value['canvas_size'],list)
            or len(value['canvas_size'])!=2 or any(type(v) is not int for v in value['canvas_size'])
            or value['canvas_size']!=list(source.image.size)):
        raise ValueError('图像内容参照已变化，原范围保留')
    return value


def _read_bytes(value,source):
    value=validate_reference(value,source)
    try:
        path=Path(value['path'])
        if path.stat().st_size>MAX_PROJECT_BYTES:
            raise ValueError('图像内容快照过大')
        data=path.read_bytes()
        if len(data)>MAX_PROJECT_BYTES or sha256(data).hexdigest()!=value['sha256']:
            raise ValueError('图像内容快照校验失败')
        return data
    except OSError as exc:
        raise ValueError('图像内容快照无法读取，原范围保留') from exc


def read_layers(value,source):
    try:
        data=_read_bytes(value,source)
        payload=json.loads(data)
        if (not isinstance(payload,dict) or set(payload)!={'schema','source_sha','canvas_size','layers'}
                or type(payload['schema']) is not int or payload['schema']!=1
                or payload['source_sha']!=source.digest or payload['canvas_size']!=list(source.image.size)):
            raise ValueError('图像内容快照与照片不一致')
        layers=validate_layers(payload['layers'])
        if any(any(layer['recipe'].values()) or layer['locked'] for layer in layers):
            raise ValueError('图像内容快照不能重复应用调色')
        return layers
    except (OSError,UnicodeError,json.JSONDecodeError) as exc:
        raise ValueError('图像内容快照无法读取，原范围保留') from exc


def resolve(source,reference):
    return source.image if reference is None else render_layers(source.image,read_layers(reference,source))


class ContentCache:
    """One native content frame and one portable snapshot per image worker."""
    def __init__(self,directory):
        self.directory=Path(directory)
        self.reference=None
        self.image=None
        self._owned=None

    def clear(self):
        if self._owned is not None:
            try:self._owned.unlink(missing_ok=True)
            except OSError:pass
        self.reference,self.image,self._owned=None,None,None

    def get(self,source,*,layers=None,reference=None):
        if layers is not None and reference is not None:
            raise ValueError('图像内容参照不能混用')
        if reference is not None:
            reference=validate_reference(reference,source)
            if self.reference==reference and self.image is not None:
                _read_bytes(reference,source)
                return self.image,deepcopy(self.reference)
            image=resolve(source,reference)
            self.clear()
            self.image,self.reference=image,deepcopy(reference)
            return image,deepcopy(self.reference)
        if layers is None:
            return source.image,None
        layers=project(layers)
        if not composition_key(layers):
            self.clear()
            return source.image,None
        data=json.dumps({'schema':1,'source_sha':source.digest,'canvas_size':list(source.image.size),
                         'layers':layers},ensure_ascii=False,separators=(',',':')).encode('utf8')
        if len(data)>MAX_PROJECT_BYTES:
            raise ValueError('图像内容快照过大')
        digest=sha256(data).hexdigest()
        reuse=bool(self.reference and self.reference['sha256']==digest and self.image is not None)
        if reuse:
            try:
                _read_bytes(self.reference,source)
                return self.image,deepcopy(self.reference)
            except ValueError:
                pass  # Recreate only from this freshly validated layer snapshot.
        image=self.image if reuse else render_layers(source.image,layers)
        path=self.directory/f'content-{digest}.json'
        with atomic_output(path,overwrite=True) as temporary:temporary.write(data)
        if self._owned==path:self._owned=None
        self.clear()
        self.image=image
        self._owned=path
        self.reference={'path':str(path.resolve()),'sha256':digest,'source_sha':source.digest,
                        'canvas_size':list(source.image.size)}
        return image,deepcopy(self.reference)
