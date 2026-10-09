"""Keep the edit crop's aspect ratio inside the image service's pixel budget."""
from math import log

MIN_AREA = 512 * 512
MAX_AREA = 2048 * 2048


def validate_size(size):
    if (not isinstance(size, (list, tuple)) or len(size) != 2
            or any(type(side) is not int or side <= 0 for side in size)
            or not MIN_AREA <= size[0] * size[1] <= MAX_AREA
            or max(size) > min(size) * 8):
        raise ValueError('AI 图像编辑分辨率无效，照片未改变')
    return list(size)


def output_size(size):
    width, height = size
    if width <= 0 or height <= 0 or max(size) > min(size) * 8:
        raise ValueError('选区过窄，无法稳定生成精修；请扩大上下文范围')
    scale = min(1536 / max(size), max(1., (MIN_AREA / (width * height)) ** .5))
    desired = [side * scale for side in size]
    # Rounding both sides down can cross the minimum area. Check complete
    # pairs, keeping the aspect ratio within the response/alignment tolerance.
    choices = [[(round(side / 8) + step) * 8 for step in (-1, 0, 1, 2)]
               for side in desired]
    candidates = [(w, h) for w in choices[0] for h in choices[1]
                  if min(w, h) >= 192 and max(w, h) <= 1536
                  and MIN_AREA <= w * h <= MAX_AREA and max(w, h) <= min(w, h) * 8
                  and abs((w / h) / (width / height) - 1) <= .02]
    if not candidates:
        raise ValueError('选区过窄，无法稳定生成精修；请扩大上下文范围')
    return list(min(candidates, key=lambda pair: (
        abs(log((pair[0] / pair[1]) / (width / height))),
        sum(abs(side - goal) / goal for side, goal in zip(pair, desired)))))
