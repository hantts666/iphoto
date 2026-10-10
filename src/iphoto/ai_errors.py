"""Recognize documented error codes without displaying upstream response text."""

import json


def content_inspection_failed(body):
    if not isinstance(body, (bytes, bytearray)) or len(body) > 2_000_000:
        return False
    try:
        data = json.loads(body)
    except (ValueError, UnicodeError, RecursionError):
        return False
    if not isinstance(data, dict):
        return False
    error = data.get('error', data)
    if not isinstance(error, dict):
        return False
    return any(isinstance(value, str) and len(value) <= 64
               and value.casefold() in ('data_inspection_failed', 'datainspectionfailed')
               for value in (error.get('code'), error.get('type')))
