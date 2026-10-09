"""Versioned layer documents with vector strokes and bounded grayscale masks."""

from copy import deepcopy
from collections import OrderedDict
from datetime import datetime
import json
import math
import re
from pathlib import Path
from uuid import uuid4

from PIL import Image, ImageDraw, ImageFilter, ImageChops

from .engine import Recipe, RECIPE_FIELDS, ENGINE_VERSION, render, render_masked, smoothing_step
from .storage import atomic_output
from .masks import validate_bitmap, decode_bitmap
from .layer_tree import validate_hierarchy, forest
from .pixel_patch import validate_patch, render_patch
from .rgba_content import replace_content

MAX_LAYERS = 32
MAX_PROJECT_BYTES = 128 * 1024 * 1024
# Stored radii are relative to the source short side. Keep enough precision
# for fine source-pixel strokes; the UI supplies a physical pixel minimum.
MIN_STROKE_RADIUS = .00001
SPATIAL_MASK_FIELDS = frozenset(('base', 'ops', 'inverted', 'feather', 'label', 'edge_shift', 'bitmap'))


def empty_mask(full=False):
    return {
        "base": "full" if full else "empty",
        "ops": [],
        "inverted": False,
        "feather": 0.0,
        "label": "全图" if full else "空选区",
    }


def new_layer(name="局部调整", full=False, parent_id="", kind="adjustment"):
    return {
        "id": uuid4().hex,
        "name": name,
        "visible": True,
        "opacity": 1.0,
        "recipe": Recipe().to_dict(),
        "locked": [],
        "mask": empty_mask(full),
        "kind": kind,
        "parent_id": parent_id,
        "collapsed": False,
    }


def number(value, lo, hi):
    if (
        isinstance(value, bool)
        or not isinstance(value, (float, int))
        or not math.isfinite(value)
        or not lo <= value <= hi
    ):
        raise ValueError(f"选区或图层数值超出允许范围：{value!r} 不在 {lo} ~ {hi}")
    return float(value)


def coord999(value):
    """AI boundary tolerance: models emit -1/1000 edge sentinels; clamp those,
    reject true scale mistakes (pixel values) instead of guessing."""
    if (
        isinstance(value, bool)
        or not isinstance(value, (float, int))
        or not math.isfinite(value)
    ):
        raise ValueError(f"坐标无效：{value!r}")
    if -16 <= value <= 1015:
        return min(999.0, max(0.0, float(value)))
    raise ValueError(f"坐标超出 0~999 归一化范围：{value!r}")


def validate_mask(mask, *, cache_bitmap=False):
    if not isinstance(mask, dict) or mask.get("base") not in ("full", "empty"):
        raise ValueError("选区结构无效")
    if not isinstance(mask.get("inverted"), bool):
        raise ValueError("选区反选标记无效")
    number(mask.get("feather"), 0, 0.05)
    if 'color_recovery' in mask and type(mask['color_recovery']) is not bool:
        raise ValueError('去背景串色选项无效')
    if "semantic_target" in mask and mask["semantic_target"] not in ("face", "face_skin", "body_skin"):
        raise ValueError("选区语义目标无效")
    part = mask.get("face_part")
    if "face_part" in mask and (not isinstance(part, str) or part not in ("nose", "lips")
            or mask.get("semantic_target") != {"nose": "face_skin", "lips": "face"}.get(part)):
        raise ValueError("选区面部部位信息无效")
    part_scope = mask.get('face_part_scope')
    if 'face_part_scope' in mask:
        if part is None or not isinstance(part_scope, dict) or not set(part_scope) <= SPATIAL_MASK_FIELDS:
            raise ValueError('五官原始选择范围无效')
        part_scope = validate_mask(part_scope, cache_bitmap=cache_bitmap)
    binding = mask.get('face_binding')
    if binding is not None:
        if (not isinstance(binding,dict) or set(binding)!={'face_id','source_sha256'}
                or not isinstance(binding['face_id'],str) or not 1<=len(binding['face_id'])<=64
                or not isinstance(binding['source_sha256'],str)
                or not re.fullmatch(r'[0-9a-fA-F]{64}',binding['source_sha256'])
                or mask.get('semantic_target') not in ('face','face_skin') or part is not None):
            raise ValueError('人脸关联信息无效')
        binding = {'face_id':binding['face_id'],'source_sha256':binding['source_sha256'].lower()}
    shift = mask.get("edge_shift", 0)
    if isinstance(shift, bool) or not isinstance(shift, int) or not -5 <= shift <= 5:
        raise ValueError("边缘位移应为 -5～5 的整数（短边百分比）")
    if not isinstance(mask.get("label"), str) or len(mask["label"]) > 200:
        raise ValueError("选区名称过长")
    ops = mask.get("ops")
    if not isinstance(ops, list) or len(ops) > 160:
        raise ValueError("选区最多支持 160 次笔画/形状，请新建图层")
    for op in ops:
        if (
            not isinstance(op, dict)
            or op.get("kind") not in ("rect", "ellipse", "polygon", "brush")
            or op.get("mode") not in ("add", "subtract")
        ):
            raise ValueError("选区操作无效")
        points = op.get("points")
        if not isinstance(points, list) or not 1 <= len(points) <= 512:
            raise ValueError("选区点数无效")
        for point in points:
            if not isinstance(point, list) or len(point) != 2:
                raise ValueError("选区坐标无效")
            for v in point:
                number(v, 0, 1)
        if op["kind"] in ("rect", "ellipse") and len(points) != 2:
            raise ValueError("矩形坐标无效")
        if op["kind"] == "polygon":
            if len(points) < 3:
                raise ValueError("轮廓至少需要三个点")
            area = (
                abs(
                    sum(
                        a[0] * b[1] - b[0] * a[1]
                        for a, b in zip(points, points[1:] + points[:1])
                    )
                )
                / 2
            )
            if area < 0.000001:
                raise ValueError("轮廓面积太小")
        if op["kind"] == "brush":
            number(op.get("radius"), MIN_STROKE_RADIUS, 0.2)
    # Persist only fields defined by the mask protocol, never arbitrary project metadata.
    return {
        "base": mask["base"],
        "inverted": mask["inverted"],
        "label": mask["label"],
        **({'color_recovery':mask['color_recovery']} if 'color_recovery' in mask else {}),
        **({"semantic_target":mask["semantic_target"]} if "semantic_target" in mask else {}),
        **({"face_part": part} if part is not None else {}),
        **({'face_part_scope': part_scope} if part_scope is not None else {}),
        **({'face_binding':binding} if binding is not None else {}),
        **({"bitmap": validate_bitmap(mask["bitmap"], cache_decoded=cache_bitmap)} if "bitmap" in mask else {}),
        "feather": float(mask["feather"]),
        **({"edge_shift": mask["edge_shift"]} if mask.get("edge_shift") else {}),
        "ops": [
            {
                "kind": op["kind"],
                "mode": op["mode"],
                "points": deepcopy(op["points"]),
                **({"radius": float(op["radius"])} if op["kind"] == "brush" else {}),
            }
            for op in ops
        ],
    }


def spatial_mask(mask):
    """Keep only the original spatial intent, with no nested semantic metadata."""
    return validate_mask({key: value for key, value in mask.items() if key in SPATIAL_MASK_FIELDS})


def validate_layers(layers):
    if not isinstance(layers, list) or not 1 <= len(layers) <= MAX_LAYERS:
        raise ValueError("图层与组数量应为 1～32")
    result, ids = [], set()
    for layer in layers:
        if not isinstance(layer, dict):
            raise ValueError("图层结构无效")
        lid = layer.get("id")
        if not isinstance(lid, str) or not 1 <= len(lid) <= 64 or lid in ids:
            raise ValueError("图层标识无效")
        ids.add(lid)
        if not isinstance(layer.get("name"), str) or not 1 <= len(layer["name"]) <= 80:
            raise ValueError("图层名称无效")
        if not isinstance(layer.get("visible"), bool):
            raise ValueError("图层可见性无效")
        locked = layer.get("locked", [])
        if layer.get("kind") == "group" and (
            any(Recipe.from_dict(layer["recipe"]).to_dict().values()) or locked
        ):
            raise ValueError("图层组不直接保存调色参数，请在组内新建调整层")
        if not isinstance(locked, list) or any(
            not isinstance(k, str) or k not in RECIPE_FIELDS for k in locked
        ):
            raise ValueError("图层锁定参数无效")
        inpaint = layer.get("inpaint")
        if inpaint is not None:
            if (
                layer.get("kind") == "group"
                or not isinstance(inpaint, dict)
                or inpaint.get("method") not in ("telea", "ns")
                or isinstance(inpaint.get("radius"), bool)
                or not isinstance(inpaint.get("radius"), (int, float))
                or not 1 <= inpaint["radius"] <= 25
            ):
                raise ValueError("内容感知填充参数无效")
            inpaint = {
                "method": inpaint["method"],
                "radius": float(inpaint["radius"]),
            }
        patch = validate_patch(layer['pixel_patch']) if 'pixel_patch' in layer else None
        if patch is not None and (layer.get('kind') == 'group' or layer.get('heal') or inpaint):
            raise ValueError('生成像素不能与图层组或内容填充混用')
        heal = layer.get("heal")
        if heal is not None:
            if (
                layer.get("kind") == "group"
                or not isinstance(heal, dict)
                or not isinstance(heal.get("ops"), list)
                or not 1 <= len(heal["ops"]) <= 60
            ):
                raise ValueError("修复笔画无效")
            clean_ops = []
            for op in heal["ops"]:
                if not isinstance(op, dict) or op.get("kind") != "heal":
                    raise ValueError("修复笔画无效")
                points = op.get("points")
                if not isinstance(points, list) or not 1 <= len(points) <= 512:
                    raise ValueError("修复点数无效")
                for point in points:
                    if not isinstance(point, list) or len(point) != 2:
                        raise ValueError("修复坐标无效")
                    for v in point:
                        number(v, 0, 1)
                clean_ops.append(
                    {
                        "kind": "heal",
                        "points": deepcopy(points),
                        "radius": number(op.get("radius"), MIN_STROKE_RADIUS, 0.2),
                    }
                )
            heal = {"ops": clean_ops}
        result.append(
            {
                "id": lid,
                "name": layer["name"],
                "visible": layer["visible"],
                "opacity": number(layer.get("opacity"), 0, 1),
                "recipe": Recipe.from_dict(layer["recipe"]).to_dict(),
                "locked": sorted(set(locked)),
                "mask": validate_mask(layer["mask"]),
                "kind": layer.get("kind", "adjustment"),
                "parent_id": layer.get("parent_id", ""),
                "collapsed": layer.get("collapsed", False),
                **({"inpaint": inpaint} if inpaint is not None else {}),
                **({"heal": heal} if heal is not None else {}),
                **({"pixel_patch": patch} if patch is not None else {}),
            }
        )
    return validate_hierarchy(result)


def raster_mask(mask, size):
    """Feather inward: even after blur, hard-mask zero pixels remain exactly zero."""
    image = (
        decode_bitmap(mask["bitmap"], size)
        if "bitmap" in mask
        else Image.new("L", size, 255 if mask["base"] == "full" else 0)
    )
    draw = ImageDraw.Draw(image)
    w, h = size
    for op in mask["ops"]:
        points = [
            (min(w - 1, round(x * w)), min(h - 1, round(y * h)))
            for x, y in op["points"]
        ]
        fill = 255 if op["mode"] == "add" else 0
        if op["kind"] in ("rect", "ellipse"):
            a, b = points
            bounds = (
                min(a[0], b[0]),
                min(a[1], b[1]),
                max(a[0], b[0]),
                max(a[1], b[1]),
            )
            getattr(draw, "rectangle" if op["kind"] == "rect" else "ellipse")(
                bounds, fill=fill
            )
        elif op["kind"] == "polygon":
            draw.polygon(points, fill=fill)
        else:
            radius = max(1, round(op["radius"] * min(size)))
            if len(points) > 1:
                draw.line(points, fill=fill, width=radius * 2, joint="curve")
            for x, y in points:
                draw.ellipse(
                    (x - radius, y - radius, x + radius, y + radius), fill=fill
                )
    if mask["inverted"]:
        image = ImageChops.invert(image)
    shift = mask.get("edge_shift", 0)
    if shift:
        # Photoshop-like contract/expand, percent of the short side.
        px = max(1, round(abs(shift) / 100 * min(size)))
        image = image.filter(
            ImageFilter.MaxFilter(2 * px + 1)
            if shift > 0
            else ImageFilter.MinFilter(2 * px + 1)
        )
    if mask["feather"]:
        # Inward feather may lower coverage, but must not square partial alpha.
        # For a binary mask this is identical to the previous multiply rule.
        image = ImageChops.darker(
            image, image.filter(ImageFilter.GaussianBlur(mask["feather"] * min(size)))
        )
    return image


class _RasterCache:
    """Bounded LRU of rasterized masks; mask dicts are immutable per version."""

    def __init__(self, max_bytes=64 * 1024 * 1024):
        self.max_bytes = max_bytes
        self.entries = OrderedDict()
        self.bytes = 0

    def get(self, key):
        item = self.entries.get(key)
        if item is not None:
            self.entries.move_to_end(key)
        return item

    def put(self, key, image):
        weight = image.width * image.height
        if weight > self.max_bytes:
            return
        while self.entries and self.bytes + weight > self.max_bytes:
            _, old = self.entries.popitem(last=False)
            self.bytes -= old.width * old.height
        self.entries[key] = image
        self.bytes += weight

    def clear(self):
        self.entries.clear()
        self.bytes = 0


RASTER_CACHE = _RasterCache()


def raster_mask_cached(mask, size):
    key = (
        json.dumps(
            {k: v for k, v in mask.items() if k != "label"},
            sort_keys=True,
            separators=(",", ":"),
        ),
        tuple(size),
    )
    hit = RASTER_CACHE.get(key)
    if hit is not None:
        return hit
    image = raster_mask(mask, size)
    RASTER_CACHE.put(key, image)
    return image


def render_layers(image, layers):
    return render_nodes(image, forest(layers))


def heal_region_mask(heal, size):
    """Raster of healing strokes; reuses brush rendering of the mask protocol."""
    proxy = {
        "base": "empty",
        "inverted": False,
        "feather": 0.0,
        "label": "修复",
        "ops": [
            {
                "kind": "brush",
                "mode": "add",
                "points": op["points"],
                "radius": op["radius"],
            }
            for op in heal["ops"]
        ],
    }
    return raster_mask(proxy, size)


def _render_healed_layer(image, layer, full_size, canvas_box):
    """Combine the layer's tool effects before applying its opacity once."""
    from .inpainting import inpaint_image

    heal = layer["heal"]
    strokes = heal_region_mask(heal, full_size)
    if canvas_box is not None:
        strokes = strokes.crop(canvas_box)
    combined = image
    if strokes.getbbox():
        radius = max(3, min(25, round(max(op["radius"] for op in heal["ops"]) * min(full_size) / 2)))
        filled = inpaint_image(image, strokes, {"method": "telea", "radius": radius})
        combined = Image.composite(filled, image, strokes)
    # Brush strokes are their own range. The adjustment mask still limits
    # tone/fill effects, including on old empty-mask healing layers.
    inpaint = layer.get("inpaint")
    if inpaint or any(layer["recipe"].values()):
        source_mask = layer["mask"]
        full = (source_mask["base"] == "full" and not source_mask["ops"]
                and not source_mask["inverted"] and not source_mask["feather"]
                and not source_mask.get("edge_shift", 0) and not source_mask.get("bitmap"))
        region = None if full and not inpaint else raster_mask_cached(source_mask, full_size)
        if region is not None and canvas_box is not None:
            region = region.crop(canvas_box)
        if region is None or region.getbbox():
            base = inpaint_image(combined, region, inpaint) if inpaint else combined
            adjusted = render(base, Recipe.from_dict(layer["recipe"]), detail_size=full_size)
            combined = adjusted if region is None else Image.composite(adjusted, combined, region)
    if layer["opacity"] == 1:
        return combined
    opacity = Image.new("L", image.size, round(255 * layer["opacity"]))
    return Image.composite(combined, image, opacity)


def render_nodes(image, nodes, *, canvas_size=None, canvas_box=None):
    from .inpainting import inpaint_image

    if (canvas_size is None) != (canvas_box is None):
        raise ValueError("细节画布尺寸和裁剪范围必须同时提供")
    full_size = canvas_size or image.size
    result = image
    for node in nodes:
        layer = node["layer"]
        if not layer["visible"] or not layer["opacity"]:
            continue
        group = layer.get("kind") == "group"
        inpaint = layer.get("inpaint")
        heal = layer.get("heal")
        if layer.get("pixel_patch"):
            result = render_patch(result, layer, full_size, canvas_box)
            continue
        if not group and not inpaint and not heal and not any(layer["recipe"].values()):
            continue
        if heal:
            result = _render_healed_layer(result, layer, full_size, canvas_box)
            continue
        source_mask = layer["mask"]
        opaque_full = (
            not inpaint and layer["opacity"] == 1
            and source_mask["base"] == "full"
            and not source_mask["ops"] and not source_mask["inverted"]
            and not source_mask["feather"] and not source_mask.get("edge_shift", 0)
            and not source_mask.get("bitmap")
        )
        if opaque_full:
            region = None
        else:
            region = raster_mask_cached(source_mask, full_size)
            if canvas_box is not None:
                region = region.crop(canvas_box)
            if not region.getbbox():
                continue
        base = inpaint_image(result, region, inpaint) if inpaint else result
        mask = region
        if mask is not None and layer["opacity"] < 1:
            mask = mask.point([round(v * layer["opacity"]) for v in range(256)])
        if not group and not inpaint and mask is not None:
            result = render_masked(
                base, Recipe.from_dict(layer["recipe"]), mask,
                detail_size=full_size, origin=canvas_box[:2] if canvas_box else (0, 0),
            )
            continue
        adjusted = (
            render_nodes(base, node["children"], canvas_size=canvas_size, canvas_box=canvas_box)
            if group
            else render(base, Recipe.from_dict(layer["recipe"]), detail_size=full_size)
        )
        result = adjusted if opaque_full else replace_content(adjusted, result, mask)
    return result if result is not image else image.copy()


def render_detail_tile(image, layers, box, halo=128):
    """Render a viewport crop at source resolution with nearby filter context."""
    if not (isinstance(box, (list, tuple)) and len(box) == 4):
        raise ValueError("细节裁剪范围无效")
    left, top, right, bottom = (int(value) for value in box)
    if not (0 <= left < right <= image.width and 0 <= top < bottom <= image.height):
        raise ValueError("细节裁剪范围超出照片")
    if (right - left) * (bottom - top) > 8_000_000:
        raise ValueError("细节裁剪范围超过 800 万像素")
    margin = max(0, min(256, int(halo)))
    step = smoothing_step(image.size) if any(layer.get("recipe", {}).get("skin_smoothing") for layer in layers) else 1
    # Give each crop the same smoothing cell origin as the whole photograph.
    # Filter context remains outside the requested box and is trimmed below.
    outer = (
        max(0, ((left - margin) // step) * step),
        max(0, ((top - margin) // step) * step),
        min(image.width, ((right + margin + step - 1) // step) * step),
        min(image.height, ((bottom + margin + step - 1) // step) * step),
    )
    cropped = image.crop(outer)
    rendered = render_nodes(cropped, forest(layers), canvas_size=image.size, canvas_box=outer)
    return rendered.crop((left - outer[0], top - outer[1], right - outer[0], bottom - outer[1]))


def overlay_mask(mask, size, mode="overlay"):
    if mode == "grayscale":
        return raster_mask(mask, size).convert("RGB")
    alpha = raster_mask(mask, size)
    if mode in ('white','black'):
        overlay = Image.new('RGBA',size,'white' if mode=='white' else 'black')
        overlay.putalpha(alpha.point([255-v for v in range(256)]))
        return overlay
    alpha = alpha.point([round(v * 0.38) for v in range(256)])
    overlay = Image.new("RGBA", size, (52, 215, 166, 0))
    overlay.putalpha(alpha)
    return overlay


def overlay_mask_tile(mask, size, box, mode="overlay"):
    """Match the source-resolution selection edge in a viewport crop."""
    left, top, right, bottom = box
    if not (0 <= left < right <= size[0] and 0 <= top < bottom <= size[1]):
        raise ValueError("蒙版细节裁剪范围无效")
    alpha = raster_mask_cached(mask, size).crop(box)
    if mode == "grayscale":
        return alpha.convert("RGB")
    if mode in ('white','black'):
        overlay = Image.new('RGBA',alpha.size,'white' if mode=='white' else 'black')
        overlay.putalpha(alpha.point([255-v for v in range(256)]))
        return overlay
    overlay = Image.new("RGBA", alpha.size, (52, 215, 166, 0))
    overlay.putalpha(alpha.point([round(v * 0.38) for v in range(256)]))
    return overlay


def validate_conversation(messages):
    if not isinstance(messages, list) or len(messages) > 2000:
        raise ValueError("对话记录过大")
    clean, ids = [], set()
    for msg in messages:
        if not isinstance(msg, dict) or msg.get("role") not in (
            "user",
            "assistant",
            "error",
            "event",
        ):
            raise ValueError("对话结构无效")
        item = {}
        for key in (
            "id",
            "role",
            "text",
            "time",
            "model",
            "mode",
            "layer_id",
            "layer_name",
            "state",
        ):
            v = msg.get(key, "")
            if not isinstance(v, str) or len(v) > (8000 if key == "text" else 200):
                raise ValueError("对话字段无效")
            item[key] = v
        if not item["id"] or item["id"] in ids:
            raise ValueError("对话标识缺失或重复")
        ids.add(item["id"])
        if "recipe" in msg:
            item["recipe"] = Recipe.from_dict(msg["recipe"]).to_dict()
        if "binding" in msg:
            if not isinstance(msg["binding"], str) or (
                msg["binding"] and not re.fullmatch(r"[0-9a-f]{64}", msg["binding"])
            ):
                raise ValueError("建议版本信息无效")
            item["binding"] = msg["binding"]
        if 'repair_layer_ids' in msg:
            repair_ids = msg['repair_layer_ids']
            if (not isinstance(repair_ids, list) or not 1 <= len(repair_ids) <= 3
                    or any(not isinstance(lid, str) or not 1 <= len(lid) <= 64 for lid in repair_ids)
                    or len(set(repair_ids)) != len(repair_ids)):
                raise ValueError('修复结果的图层引用无效')
            item['repair_layer_ids'] = list(repair_ids)
        if 'adjustment_layer_ids' in msg:
            adjustment_ids = msg['adjustment_layer_ids']
            if (not isinstance(adjustment_ids, list) or not 1 <= len(adjustment_ids) <= 4
                    or any(not isinstance(lid, str) or not 1 <= len(lid) <= 64 for lid in adjustment_ids)
                    or len(set(adjustment_ids)) != len(adjustment_ids)):
                raise ValueError('局部调整结果的图层引用无效')
            item['adjustment_layer_ids'] = list(adjustment_ids)
        if 'request_binding' in msg:
            if not isinstance(msg['request_binding'], str) or not re.fullmatch(r'[0-9a-f]{64}', msg['request_binding']):
                raise ValueError('原请求的作用范围信息无效')
            item['request_binding'] = msg['request_binding']
        if 'request_user_id' in msg:
            if not isinstance(msg['request_user_id'], str) or not 1 <= len(msg['request_user_id']) <= 200:
                raise ValueError('原请求的对话引用无效')
            item['request_user_id'] = msg['request_user_id']
        clean.append(item)
    return clean


def validate_project(payload):
    try:
        return _validate_project(payload)
    except (KeyError, TypeError, OverflowError, RecursionError):
        raise ValueError("项目字段缺失或类型不正确") from None


def _validate_project(payload):
    if not isinstance(payload, dict):
        raise ValueError("项目结构无效")
    if payload.get("schema_version") == "0.1":
        layer = new_layer("全图调整", True)
        layer["recipe"] = Recipe.from_dict(payload["recipe"]).to_dict()
        layer["locked"] = payload.get("locked", [])
        payload = {
            **payload,
            "schema_version": "1.2",
            "layers": [layer],
            "active_layer": layer["id"],
            "conversation": [],
        }
    if payload.get("schema_version") not in ("1.2", "1.3", "1.4", "1.5", "1.6", "1.7", "1.8", "1.9", "1.10", "1.11", "1.12", "1.13"):
        raise ValueError("不支持此项目版本")
    if (
        not isinstance(payload.get("source"), str)
        or not isinstance(payload.get("source_sha256"), str)
        or not re.fullmatch(r"[0-9a-fA-F]{64}", payload["source_sha256"])
    ):
        raise ValueError("缺少源图片校验信息")
    layers = validate_layers(payload["layers"])
    active = payload.get("active_layer")
    if active not in [l["id"] for l in layers]:
        raise ValueError("当前图层不存在")
    selection_target = payload.get("selection_target_id", "")
    if not isinstance(selection_target, str) or (
        selection_target
        and (selection_target != active or payload.get("selection_draft") is None)
    ):
        raise ValueError("范围修正的目标图层无效")
    conversation_draft = payload.get("conversation_draft", "")
    if not isinstance(conversation_draft, str) or len(conversation_draft) > 4000:
        raise ValueError("对话草稿无效或超过 4000 字")
    conversation_draft_mode = payload.get("conversation_draft_mode", "edit")
    if conversation_draft_mode not in ("edit", "advice", "regions"):
        raise ValueError("对话草稿模式无效")
    region_draft = validate_region_draft(payload.get("region_draft"))
    if region_draft and (
        payload.get("selection_draft") is not None
        or len(layers) + len(region_draft["layers"]) > MAX_LAYERS
    ):
        raise ValueError("分区方案与当前文档不兼容")
    if region_draft and {l["id"] for l in layers} & {
        l["id"] for l in region_draft["layers"]
    }:
        raise ValueError("分区图层标识重复")
    from .scene import validate_catalog

    return {
        "schema_version": project_version(layers, payload.get('selection_draft'), region_draft),
        "engine_version": ENGINE_VERSION,
        "source": payload["source"],
        "source_sha256": payload["source_sha256"].lower(),
        "layers": layers,
        "active_layer": active,
        "conversation": validate_conversation(payload.get("conversation", [])),
        "conversation_draft": conversation_draft,
        "conversation_draft_mode": conversation_draft_mode,
        "selection_draft": validate_mask(payload["selection_draft"])
        if payload.get("selection_draft") is not None
        else None,
        "selection_target_id": selection_target,
        "region_draft": region_draft,
        "scene_catalog": validate_catalog(payload.get("scene_catalog")),
    }


def project_version(layers, draft=None, regions=None):
    content_layers = list(layers) + (regions['layers'] if regions else [])
    if any(layer.get('pixel_patch', {}).get('compositing') == 'replace_rgba' for layer in content_layers):
        return '1.13'
    masks = [layer['mask'] for layer in layers]
    if draft is not None:
        masks.append(draft)
    if regions:
        masks.extend(layer['mask'] for layer in regions['layers'])
    if any('color_recovery' in mask for mask in masks):
        return '1.12'
    return '1.11' if any(layer.get('pixel_patch') for layer in layers) else '1.10'


def validate_region_draft(value):
    if value is None:
        return None
    if (
        not isinstance(value, dict)
        or not isinstance(value.get("summary"), str)
        or len(value["summary"]) > 4000
    ):
        raise ValueError("分区方案无效")
    layers = validate_layers(value.get("layers"))
    if len(layers) > 4:
        raise ValueError("分区方案最多四个区域")
    return {"summary": value["summary"], "layers": layers}


def read_project(path):
    path = Path(path)
    if path.stat().st_size > MAX_PROJECT_BYTES:
        raise ValueError("项目超过 16 MB 限制")
    try:
        return validate_project(json.loads(path.read_text(encoding="utf-8")))
    except (RecursionError, UnicodeError):
        raise ValueError("项目文件编码或嵌套结构无效") from None


def write_project(path, payload, overwrite=False):
    path = Path(path)
    encoded = json.dumps(
        validate_project(payload), ensure_ascii=False, indent=2
    ).encode("utf-8")
    if len(encoded) > MAX_PROJECT_BYTES:
        raise ValueError("项目超过 16 MB 限制")
    with atomic_output(path, overwrite) as file:
        file.write(encoded)


def now():
    return datetime.now().astimezone().isoformat(timespec="seconds")
