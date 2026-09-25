"""Strict VLM localization protocol. Boxes/anchors are prompts, not selections."""

from ..document import number

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
    x0, y0, x1, y1 = [number(v, 0, 999) / 999 for v in box]
    x, y = [number(v, 0, 999) / 999 for v in point]
    if x1 <= x0 or y1 <= y0 or not (x0 <= x <= x1 and y0 <= y <= y1):
        raise ValueError("目标框必须有面积，保留点必须位于框内")
    polygon = [[x0, y0], [x1, y0], [x1, y1], [x0, y1]]
    return polygon, [x, y]
