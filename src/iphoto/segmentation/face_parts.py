"""Facial part contracts shared by AI plans and the local pixel worker."""

PARTS = {
    "nose": {"target": "face_skin", "classes": (10,), "label": "鼻部皮肤"},
    "lips": {"target": "face", "classes": (12, 13), "label": "嘴唇"},
}


def validate_part(value, target, scope):
    if not isinstance(value, str) or value not in ("all", *PARTS):
        raise ValueError("面部部位类型无效")
    if value != "all" and (target != PARTS[value]["target"] or scope != "region"):
        raise ValueError("面部部位类型无效")
    return value
