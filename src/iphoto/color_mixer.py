"""Selective HSL in encoded sRGB, with cyclic, overlapping colour bands.

Conversions follow CSS Color 4 section 7; the band weighting and adjustment
policy are photographic controls specific to iPhoto. They do not create masks.
"""
import numpy as np

COLORS = (("red", "红", 0), ("orange", "橙", 30), ("yellow", "黄", 60),
          ("green", "绿", 120), ("aqua", "青", 180), ("blue", "蓝", 240),
          ("purple", "紫", 270), ("magenta", "洋红", 300))
FIELDS = {f"hsl_{name}_{control}": (-180, 180) if control == "hue" else (-100, 100)
          for name, _, _ in COLORS for control in ("hue", "saturation", "lightness")}
LABELS = {f"hsl_{name}_{control}": f"{label}色{title}"
          for name, label, _ in COLORS
          for control, title in (("hue", "色相"), ("saturation", "饱和度"), ("lightness", "明度"))}


def rgb_to_hsl(rgb):
    red, green, blue = np.moveaxis(rgb, -1, 0)
    maximum, minimum = rgb.max(-1), rgb.min(-1)
    delta = maximum - minimum
    light = (maximum + minimum) * .5
    safe = np.maximum(delta, 1e-7)
    hue = np.where(maximum == red, ((green - blue) / safe) % 6,
                   np.where(maximum == green, (blue - red) / safe + 2,
                            (red - green) / safe + 4)) * 60
    sat = np.where(delta > 0, delta / np.maximum(1 - np.abs(2 * light - 1), 1e-7), 0)
    return hue, sat, light


def hsl_to_rgb(hue, sat, light):
    amplitude = sat * np.minimum(light, 1 - light)
    channels = []
    for offset in (0, 8, 4):
        k = (offset + hue / 30) % 12
        channels.append(light - amplitude * np.maximum(-1, np.minimum(np.minimum(k - 3, 9 - k), 1)))
    return np.stack(channels, -1).clip(0, 1)


def mix(rgb, recipe):
    hue, saturation, lightness = rgb_to_hsl(rgb)
    # Nearly neutral pixels must not turn into a coloured fringe. Colour band
    # adjustments fade smoothly to zero below 10% HSL saturation.
    neutral = np.clip(saturation / .1, 0, 1)
    neutral = neutral * neutral * (3 - 2 * neutral)
    shifts = [np.zeros_like(hue) for _ in range(3)]
    for index, (name, _, center) in enumerate(COLORS):
        values = [getattr(recipe, f"hsl_{name}_{field}")
                  for field in ("hue", "saturation", "lightness")]
        if not any(values):
            continue
        previous = COLORS[index - 1][2] if index else COLORS[-1][2] - 360
        following = COLORS[index + 1][2] if index < len(COLORS) - 1 else 360
        distance = (hue - center + 180) % 360 - 180
        width = np.where(distance < 0, center - previous, following - center)
        ratio = np.clip(np.abs(distance) / width, 0, 1)
        weight = (1 + np.cos(np.pi * ratio)) * .5 * neutral
        for shift, value in zip(shifts, values):
            if value:
                shift += weight * value
    hue = (hue + shifts[0]) % 360
    for value, shift in ((saturation, shifts[1]), (lightness, shifts[2])):
        amount = shift / 100
        value += np.where(amount >= 0, (1 - value) * amount, value * amount)
        np.clip(value, 0, 1, out=value)
    result = hsl_to_rgb(hue, saturation, lightness)
    # Unaffected colours, including exact greys, retain their original floats.
    unaffected = (shifts[0] == 0) & (shifts[1] == 0) & (shifts[2] == 0)
    result[unaffected] = rgb[unaffected]
    return result
