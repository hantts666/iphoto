"""Read a bounded, portable transparent foreground aligned to a full canvas."""
from io import BytesIO
from pathlib import Path

from PIL import Image, ImageCms, ImageOps

from .pixel_patch import MAX_BYTES, MAX_PIXELS, encode_patch


def read_foreground(path, canvas_size):
    path = Path(path)
    if not path.is_absolute():
        raise ValueError('请选择透明前景文件的完整路径')
    with path.open('rb') as file:
        data = file.read(MAX_BYTES + 1)
    if len(data) > MAX_BYTES:
        raise ValueError('透明前景超过16 MB，请缩小后再导入')
    try:
        with Image.open(BytesIO(data)) as raw:
            if (raw.format != 'PNG' or getattr(raw, 'n_frames', 1) != 1
                    or max(raw.size) > 32768 or raw.width * raw.height > MAX_PIXELS):
                raise ValueError('请选择不超过419万像素的单张透明PNG')
            if 'A' not in raw.getbands() and 'transparency' not in raw.info:
                raise ValueError('文件没有透明通道，请选择透明PNG')
            ImageOps.exif_transpose(raw, in_place=True)
            raw.load()
            if abs((raw.width / raw.height) / (canvas_size[0] / canvas_size[1]) - 1) > .02:
                raise ValueError('透明前景与照片比例不一致，请使用与整张画布对齐的文件')
            rgba = raw.convert('RGBA')
            alpha = rgba.getchannel('A')
            low, high = alpha.getextrema()
            if high == 0:
                raise ValueError('透明前景为空，照片未改变')
            if low == 255:
                raise ValueError('文件全部不透明，请选择带透明背景的PNG')
            if raw.info.get('icc_profile'):
                profile = ImageCms.ImageCmsProfile(BytesIO(raw.info['icc_profile']))
                rgb = ImageCms.profileToProfile(rgba.convert('RGB'), profile,
                    ImageCms.createProfile('sRGB'), outputMode='RGB')
                rgb.putalpha(alpha)
                rgba = rgb
    except (OSError, Image.DecompressionBombError, ImageCms.PyCMSError) as exc:
        raise ValueError('透明前景读取失败，请检查文件及ICC色彩配置') from exc
    return {'patch': encode_patch(rgba, canvas_size, (0, 0, *canvas_size), preserve_alpha=True),
            'name': path.stem[:64] or '透明前景', 'size': list(rgba.size),
            'has_clear_background': low == 0}
