"""Normalize Relay JSON frames without discarding a mutation result.

Malformed or unsupported patches fail closed. A terminal control frame with
data:null cannot erase an earlier result, and deferred data is applied at its
explicit path rather than merged into the operation root.
"""
from __future__ import annotations

import copy
import json


def decode_relay_response(raw_body):
    body = str(raw_body or '').strip()
    decoder = json.JSONDecoder()
    frames = []
    while body:
        if body.startswith('for (;;);'):
            body = body[len('for (;;);'):].lstrip()
        if not body:
            break
        frame, end = decoder.raw_decode(body)
        if not isinstance(frame, dict):
            raise ValueError('GraphQL response frame must be an object')
        frames.append(frame)
        body = body[end:].lstrip()
    if not frames:
        raise ValueError('Empty GraphQL response')
    last = frames[-1]
    extensions = last.get('extensions')
    if (last.get('hasNext') is True or (isinstance(extensions, dict) and extensions.get('is_final') is False)):
        raise ValueError('GraphQL stream explicitly ended before its final frame')
    result = {}

    def merge(target, source):
        for key, value in source.items():
            if isinstance(target.get(key), dict) and isinstance(value, dict):
                merge(target[key], value)
            elif key == 'errors' and isinstance(target.get(key), list) and isinstance(value, list):
                target[key].extend(copy.deepcopy(value))
            else:
                target[key] = copy.deepcopy(value)

    def patch(frame):
        path = frame.get('path')
        if not isinstance(path, list) or any(type(key) not in (str, int) for key in path):
            raise ValueError('Invalid GraphQL incremental path')
        parent = result.get('data')
        for key in path:
            if isinstance(parent, dict) and type(key) is str and key in parent:
                parent = parent[key]
            elif isinstance(parent, list) and type(key) is int and 0 <= key < len(parent):
                parent = parent[key]
            else:
                raise ValueError('GraphQL incremental path was not returned in initial data')
        data = frame.get('data')
        if not isinstance(parent, dict) or not isinstance(data, dict):
            raise ValueError('Unsupported GraphQL incremental data')
        merge(parent, data)
        if frame.get('errors'):
            merge(result, {'errors': frame['errors']})

    for frame in frames:
        incremental = frame.get('incremental')
        if incremental is not None and not isinstance(incremental, list):
            raise ValueError('Invalid GraphQL incremental envelope')
        if 'path' in frame:
            patch(frame)
            merge(result, {k: v for k, v in frame.items() if k not in {'data', 'path', 'label', 'errors'}})
        else:
            # Keep earlier data even if a later terminal/error envelope has no
            # data. Errors remain present, so this never proves a complete read.
            ext = frame.get('extensions')
            terminal = frame.get('hasNext') is False or (isinstance(ext, dict) and ext.get('is_final') is True)
            merge(result, {k: v for k, v in frame.items()
                if k != 'incremental' and not (k == 'data' and v is None and isinstance(result.get('data'), dict)
                    and (terminal or frame.get('errors')))})
        for deferred in incremental or []:
            if not isinstance(deferred, dict):
                raise ValueError('Invalid GraphQL incremental frame')
            patch(deferred)
    return result
