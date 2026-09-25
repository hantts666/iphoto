"""Versioned layer documents with vector strokes and bounded grayscale masks."""

from copy import deepcopy
from datetime import datetime
import json
import math
import re
from pathlib import Path
from uuid import uuid4

from PIL import Image, ImageDraw, ImageFilter, ImageChops

from .engine import Recipe, RANGES, ENGINE_VERSION, render
from .storage import atomic_output
from .masks import validate_bitmap, decode_bitmap
from .layer_tree import validate_hierarchy, forest

MAX_LAYERS = 32
MAX_PROJECT_BYTES = 128 * 1024 * 1024


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
        raise ValueError("选区或图层数值超出允许范围")
    return float(value)


def validate_mask(mask):
    if not isinstance(mask, dict) or mask.get("base") not in ("full", "empty"):
        raise ValueError("选区结构无效")
    if not isinstance(mask.get("inverted"), bool):
        raise ValueError("选区反选标记无效")
    number(mask.get("feather"), 0, 0.05)
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
            number(op.get("radius"), 0.001, 0.2)
    # Persist only fields defined by the mask protocol, never arbitrary project metadata.
    return {
        "base": mask["base"],
        "inverted": mask["inverted"],
        "label": mask["label"],
        **({"bitmap": validate_bitmap(mask["bitmap"])} if "bitmap" in mask else {}),
        "feather": float(mask["feather"]),
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
            not isinstance(k, str) or k not in RANGES for k in locked
        ):
            raise ValueError("图层锁定参数无效")
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
    if mask["feather"]:
        # Inward feather may lower coverage, but must not square partial alpha.
        # For a binary mask this is identical to the previous multiply rule.
        image = ImageChops.darker(
            image, image.filter(ImageFilter.GaussianBlur(mask["feather"] * min(size)))
        )
    return image


def render_layers(image, layers):
    return render_nodes(image, forest(layers))


def render_nodes(image, nodes):
    result = image.copy()
    for node in nodes:
        layer = node["layer"]
        if not layer["visible"] or not layer["opacity"]:
            continue
        group = layer.get("kind") == "group"
        if not group and not any(layer["recipe"].values()):
            continue
        mask = raster_mask(layer["mask"], image.size)
        if not mask.getbbox():
            continue
        if layer["opacity"] < 1:
            mask = mask.point([round(v * layer["opacity"]) for v in range(256)])
        adjusted = (
            render_nodes(result, node["children"])
            if group
            else render(result, Recipe.from_dict(layer["recipe"]))
        )
        result = Image.composite(adjusted, result, mask)
    return result


def overlay_mask(mask, size, mode="overlay"):
    if mode == "grayscale":
        return raster_mask(mask, size).convert("RGB")
    alpha = raster_mask(mask, size).point([round(v * 0.38) for v in range(256)])
    overlay = Image.new("RGBA", size, (52, 215, 166, 0))
    overlay.putalpha(alpha)
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
    if payload.get("schema_version") not in ("1.2", "1.3", "1.4", "1.5", "1.6"):
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
        "schema_version": "1.6",
        "engine_version": ENGINE_VERSION,
        "source": payload["source"],
        "source_sha256": payload["source_sha256"].lower(),
        "layers": layers,
        "active_layer": active,
        "conversation": validate_conversation(payload.get("conversation", [])),
        "selection_draft": validate_mask(payload["selection_draft"])
        if payload.get("selection_draft") is not None
        else None,
        "region_draft": region_draft,
        "scene_catalog": validate_catalog(payload.get("scene_catalog")),
    }


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
