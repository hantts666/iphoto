"""Combine real object masks at source resolution, only inside the pixel worker."""

from copy import deepcopy

from ..document import validate_mask
from ..masks import MAX_MASK_PIXELS, MAX_MASK_SIDE
from ..scene import MAX_OBJECTS, combine_masks


def compose(definition, items):
    if not isinstance(definition, dict):
        raise ValueError("对象组合请求无效")
    size = definition.get("size")
    if (not isinstance(size, list) or len(size) != 2
            or any(type(v) is not int or not 1 <= v <= MAX_MASK_SIDE for v in size)
            or size[0]*size[1] > MAX_MASK_PIXELS):
        raise ValueError("对象组合原图尺寸超过处理限制")
    ids, exclude = definition.get("ids"), definition.get("exclude")
    if (not isinstance(ids, list) or not ids or not isinstance(exclude, list)
            or len(ids)+len(exclude) > MAX_OBJECTS
            or any(not isinstance(lid, str) or not 1 <= len(lid) <= 64 for lid in ids+exclude)
            or len(set(ids+exclude)) != len(ids+exclude)):
        raise ValueError("对象组合目标无效")
    mode = definition.get("mode")
    if mode not in ("replace", "add", "subtract", "intersect"):
        raise ValueError("选区组合方式无效")
    cached = definition.get("cached")
    if not isinstance(cached, dict) or set(cached)-set(ids+exclude):
        raise ValueError("对象蒙版缓存无效")
    masks = dict(cached)
    for item in items:
        lid = item["id"]
        if lid not in ids+exclude or lid in masks:
            raise ValueError("对象像素结果重复或不匹配")
        masks[lid] = item["mask"]
    if set(masks) != set(ids+exclude):
        raise ValueError("未取得全部目标的像素蒙版，已有图层与范围保留；请重试")
    masks = {lid: validate_mask(mask) for lid, mask in masks.items()}
    if any("bitmap" not in mask for mask in masks.values()):
        raise ValueError("对象像素蒙版不完整，未用定位框组合")
    base = validate_mask(definition["base"]) if definition.get("base") is not None else None
    if mode != "replace" and base is None:
        raise ValueError("请先新建选区，再添加、减去或相交")
    # Match the previous order exactly: union targets, subtract exclusions,
    # then combine with the existing draft. Preserve alpha and zero support.
    mask = deepcopy(masks[ids[0]]) if len(ids) == 1 else combine_masks([masks[lid] for lid in ids], tuple(size))
    if exclude:
        mask = combine_masks([masks[lid] for lid in exclude], tuple(size), mask, "subtract")
    if mode != "replace":
        mask = combine_masks([mask], tuple(size), base, mode)
    return mask
