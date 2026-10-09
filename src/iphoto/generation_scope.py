"""A lossless edit-scope reference, separate from photo pixels and output alpha."""
import base64
import binascii
from io import BytesIO

from PIL import Image

SCOPE_PROMPT = ('\n输入图1是唯一待编辑照片。输入图2是与图1逐像素对齐的编辑范围参考，'
                '不是照片内容或风格参考，不能把它画进输出。'
                '图2白色与非零灰色允许编辑，黑色必须保留原图；'
                '灰色只标示边界过渡，输出该处仍给完整目标效果，最终强度由程序按蒙版合成一次；'
                '内部黑色孔洞也须保留。只编辑图1中图2允许的位置，保留范围外的颜色、'
                '纹理、几何位置和所有内容。输出单张与图1构图一致的完整照片，不输出蒙版、'
                '分屏、标注或参考图。')


def validate_scope_reference(photo_url, scope_url):
    """Decline missing/misaligned guides before credentials or network access.

    These are generated worker assets, sent as bounded metadata-free data URLs.
    A reference image informs the model; only local compositing enforces scope.
    """
    def read(url, scope=False):
        if not isinstance(url, str) or len(url) > 16_000_000:
            raise ValueError('生成精修的照片或范围参考无效，照片未改变')
        header, separator, payload = url.partition(',')
        if not separator or header not in (('data:image/png;base64',) if scope else
                                            ('data:image/png;base64', 'data:image/jpeg;base64')):
            raise ValueError('生成精修缺少有效的无损选区参考，照片未改变')
        data = base64.b64decode(payload, validate=True)
        if len(data) > 10_000_000:
            raise ValueError('生成精修的照片或范围参考资源过大，照片未改变')
        with Image.open(BytesIO(data)) as raw:
            if (max(raw.size) > 1280 or min(raw.size) < 64
                    or raw.format not in (('PNG',) if scope else ('PNG', 'JPEG'))
                    or getattr(raw, 'n_frames', 1) != 1):
                raise ValueError('生成精修的照片或范围参考尺寸无效，照片未改变')
            size = raw.size
            if scope:
                if raw.mode not in ('L', 'RGB'):
                    raise ValueError('生成精修范围参考必须是不透明黑白灰图，照片未改变')
                raw.load()
                if raw.mode == 'RGB':
                    bands = raw.split()
                    if bands[0].tobytes() != bands[1].tobytes() or bands[0].tobytes() != bands[2].tobytes():
                        raise ValueError('生成精修范围参考必须是不透明黑白灰图，照片未改变')
                    raw = bands[0]
                if not raw.getbbox():
                    raise ValueError('生成精修范围为空，照片未改变')
            else:
                raw.verify()
            return size
    try:
        photo_size, scope_size = read(photo_url), read(scope_url, True)
        if scope_size != photo_size:
            raise ValueError('生成精修照片与范围参考没有对齐，照片未改变')
    except (binascii.Error, OSError, TypeError, Image.DecompressionBombError) as exc:
        raise ValueError('生成精修的照片或范围参考读取失败，照片未改变') from exc
