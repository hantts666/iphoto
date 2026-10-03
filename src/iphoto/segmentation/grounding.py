"""Strict VLM localization protocol. Boxes/anchors are prompts, not selections."""

from ..document import coord999

COORDINATE_PROMPT = """坐标只相对于当前提供的这张图片，统一使用非负的0～999值：左上角[0,0]，右下角[999,999]，中心[499.5,499.5]。x从左向右增大，y从上向下增大；不得使用负数，也不得把图片中心当作零点。
若目标位于当前图片的像素(u,v)，则x=u/图宽*999，y=v/图高*999。即使图片是局部放大图，也按这张图的宽高计算，不要减去原图裁切位置。box=[左,上,右,下]必须左<右、上<下，point=[x,y]必须在box内且落在真正的目标像素上。
"""

BOX_SCHEMA = {
    "type": "array",
    "items": {"type": "number", "minimum": 0, "maximum": 999},
    "minItems": 4,
    "maxItems": 4,
}
ANCHOR_SCHEMA = {
    "type": "array",
    "items": {"type": "number", "minimum": 0, "maximum": 999},
    "minItems": 2,
    "maxItems": 2,
}


def box_hint(box, point):
    if (
        not isinstance(box, list)
        or len(box) != 4
        or not isinstance(point, list)
        or len(point) != 2
    ):
        raise ValueError("目标框或内部保留点格式无效")
    x0, y0, x1, y1 = [coord999(v) / 999 for v in box]
    x, y = [coord999(v) / 999 for v in point]
    if x1 <= x0 or y1 <= y0 or not (x0 <= x <= x1 and y0 <= y <= y1):
        raise ValueError("目标框必须有面积，保留点必须位于框内")
    polygon = [[x0, y0], [x1, y0], [x1, y1], [x0, y1]]
    return polygon, [x, y]
