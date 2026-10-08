"""Show source feature geometry so a vision review can reject false strand dots.

Components are bounded native channel features, not semantic masks or alpha.
Their geometry is never sent to the pixel correction as a foreground constraint.
"""
import cv2
import numpy as np
from PIL import Image, ImageDraw

from ..matte_review import point_window
from .strand_candidates import line_fields


def render_structures(source, core, frame, candidates):
    if not candidates:
        return [], []
    rgb = np.asarray(source.crop(core).convert('RGB'), np.float32) / 255
    fields = {name: line_fields(rgb[..., index])
              for name, index in (('red', 0), ('blue', 2))}
    # Eight small label maps are shared by all sixteen points in this core.
    # Computing a fresh neighborhood for each dot can truncate a curl into
    # unrelated fragments and can change the shape at every point.
    components = {}
    for channel, polarities in fields.items():
        for polarity, response in polarities.items():
            for threshold in (.025, .05):
                binary = cv2.morphologyEx(
                    (response >= threshold).astype(np.uint8), cv2.MORPH_CLOSE,
                    np.ones((3, 3), np.uint8))
                _, labels, stats, _ = cv2.connectedComponentsWithStats(binary)
                components[channel, polarity, threshold] = labels, stats
    sheets = [Image.new('RGB', (1024, ((min(8, len(candidates)-start) + 1) // 2) * 276), '#24292f')
              for start in range(0, len(candidates), 8)]
    centers = Image.new('RGB', (1024, ((len(candidates)+3)//4)*276), '#24292f')
    center_header = ImageDraw.Draw(centers)
    records = []
    for number, coordinate in enumerate(candidates, 1):
        window, (gx, gy) = point_window(source.size, frame, coordinate)
        original = source.crop(window).convert('RGB')
        picture = np.array(original)
        row, column = gy - core[1], gx - core[0]
        features = []
        symbols = []
        for channel, color in (('red', (30, 220, 255)), ('blue', (255, 200, 70))):
            polarities = fields[channel]
            polarity = max(polarities, key=lambda key: float(polarities[key][row, column]))
            for threshold in (.025, .05):
                labels, stats = components[channel, polarity, threshold]
                identity = int(labels[row, column])
                if not identity:
                    continue
                x, y, width, height, area = (int(v) for v in stats[identity])
                if area < 48 or area > 12000 or max(width, height) < 30:
                    continue
                footprint = labels == identity
                native_box = [core[0] + x, core[1] + y,
                              core[0] + x + width, core[1] + y + height]
                clipped = (x == 0 or y == 0 or x + width == rgb.shape[1]
                           or y + height == rgb.shape[0]
                           or native_box[0] < window[0] or native_box[1] < window[1]
                           or native_box[2] > window[2] or native_box[3] > window[3])
                local = np.zeros((original.height, original.width), np.uint8)
                left, top = max(core[0], window[0]), max(core[1], window[1])
                right, bottom = min(core[2], window[2]), min(core[3], window[3])
                local[top-window[1]:bottom-window[1], left-window[0]:right-window[0]] = (
                    footprint[top-core[1]:bottom-core[1], left-core[0]:right-core[0]])
                chosen = local > 0
                picture[chosen] = np.rint(picture[chosen] * .85 + np.array(color) * .15).astype(np.uint8)
                contours, _ = cv2.findContours(local, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
                cv2.drawContours(picture, contours, -1, color, 1)
                features.append({'channel': channel, 'polarity': polarity,
                                 'threshold': threshold, 'source_box': native_box,
                                 'pixels': area, 'clipped': clipped})
                symbols.append(('R' if channel == 'red' else 'B')
                               + ('+' if polarity == 'bright' else '-') + (' clip' if clipped else ''))
                break
        # All marks leave the actual 3x3 source center unobscured. A nearby
        # component cannot establish identity from a painted center pixel.
        cx, cy = gx-window[0], gy-window[1]
        picture[max(0, cy-1):cy+2, max(0, cx-1):cx+2] = np.asarray(original)[max(0, cy-1):cy+2, max(0, cx-1):cx+2]
        marked = Image.fromarray(picture)
        drawing = ImageDraw.Draw(marked)
        drawing.ellipse((cx-8, cy-8, cx+8, cy+8), outline='#ec9dff', width=1)
        sheet = sheets[(number-1)//8]
        header = ImageDraw.Draw(sheet)
        x, y = (number-1) % 2 * 512, ((number-1) % 8) // 2 * 276
        header.text((x+5, y+3), f'C{number} Source', fill='white')
        # An untouched source tile is paired with each feature tile. The
        # feature outline itself can look like a strand on a clothing seam.
        sheet.paste(original, (x, y+20))
        header.text((x+261, y+3), f'C{number} ' + (' / '.join(symbols) or 'no shape'), fill='white')
        sheet.paste(marked, (x+256, y+20))
        center_box = [max(0,gx-32),max(0,gy-32),min(source.width,gx+32),min(source.height,gy+32)]
        center = source.crop(center_box).convert('RGB')
        center = center.resize((center.width*4,center.height*4),Image.Resampling.NEAREST)
        cx,cy = (number-1)%4*256,(number-1)//4*276
        center_header.text((cx+5,cy+3),f'C{number} Source center 4x',fill='white')
        centers.paste(center,(cx,cy+20))
        records.append({'candidate': number, 'coordinate': list(coordinate),
                        'source_box': window, 'center_box': center_box, 'features': features})
    sheets.append(centers)
    return records, sheets
