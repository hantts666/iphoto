"""Policies that survive baking an effective mask into a new alpha bitmap."""
from copy import deepcopy


def copy_metadata(source, target):
    # Alpha is re-estimated; foreground colour recovery remains an export and
    # preview policy. Losing it here brings the old background back at edges.
    for key in ('edge_protection', 'color_recovery'):
        if key in source:
            target[key] = deepcopy(source[key])
    if not source['inverted']:
        for key in ('semantic_target', 'face_binding', 'face_part', 'face_part_scope'):
            if key in source:
                target[key] = deepcopy(source[key])
    return target
