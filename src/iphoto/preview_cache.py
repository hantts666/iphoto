"""Bounded prefix cache for sequential adjustment layers, scoped to one image."""

from collections import OrderedDict
from hashlib import sha256
import json

from PIL import Image
from .document import render_nodes, raster_mask
from .layer_tree import forest


def layer_key(layer):
    return {
        "recipe": layer["recipe"],
        "opacity": layer["opacity"],
        "mask": {key: value for key, value in layer["mask"].items() if key != "label"},
    }


def node_key(node):
    layer = node["layer"]
    if not layer["visible"] or not layer["opacity"]:
        return None
    if layer.get("kind") == "group":
        children = [
            key for child in node["children"] if (key := node_key(child)) is not None
        ]
        return {**layer_key(layer), "children": children} if children else None
    return layer_key(layer) if any(layer["recipe"].values()) else None


def composition_key(layers):
    return [key for node in forest(layers) if (key := node_key(node)) is not None]


class LayerPreviewCache:
    def __init__(self, max_bytes=64 * 1024 * 1024):
        self.max_bytes = max_bytes
        self.entries = OrderedDict()
        self.bytes = 0
        self.reused = 0

    def clear(self):
        self.entries.clear()
        self.bytes = 0

    def render(self, image, layers):
        self.reused = 0
        return self._sequence(image, forest(layers), "")

    def _sequence(self, image, nodes, prefix):
        current = image
        for node in nodes:
            key = node_key(node)
            if key is None:
                continue
            before = prefix
            prefix = sha256(
                (
                    prefix + json.dumps(key, sort_keys=True, separators=(",", ":"))
                ).encode()
            ).hexdigest()
            if prefix in self.entries:
                current = self.entries[prefix]
                self.entries.move_to_end(prefix)
                self.reused += 1
            else:
                layer = node["layer"]
                if layer.get("kind") == "group":
                    adjusted = self._sequence(
                        current, node["children"], before + "|group-input|"
                    )
                    mask = raster_mask(layer["mask"], image.size)
                    if layer["opacity"] < 1:
                        mask = mask.point(
                            [round(v * layer["opacity"]) for v in range(256)]
                        )
                    current = Image.composite(adjusted, current, mask)
                else:
                    current = render_nodes(current, [node])
                weight = current.width * current.height * len(current.getbands())
                if weight <= self.max_bytes:
                    while self.entries and self.bytes + weight > self.max_bytes:
                        _, old = self.entries.popitem(last=False)
                        self.bytes -= old.width * old.height * len(old.getbands())
                    self.entries[prefix] = current
                    self.bytes += weight
        return current
