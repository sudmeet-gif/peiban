"""Backward-compatible storage of one or more character voice samples."""
import json
from pathlib import PurePosixPath


def decode_reference_items(value):
    if not value:
        return []
    if value.startswith('['):
        try:
            parsed = json.loads(value)
        except (TypeError, ValueError):
            parsed = None
        if isinstance(parsed, list):
            result = []
            seen = set()
            for item in parsed:
                path = item if isinstance(item, str) else item.get('path') if isinstance(item, dict) else None
                if not isinstance(path, str) or not path or path in seen:
                    continue
                label = item.get('label') if isinstance(item, dict) else None
                result.append({'path':path, 'label': label if isinstance(label,str) and label else PurePosixPath(path).name})
                seen.add(path)
            return result
    return [{'path':value, 'label':PurePosixPath(value).name}]


def decode_references(value):
    return [item['path'] for item in decode_reference_items(value)]


def encode_reference_items(items):
    return json.dumps([{'path':item['path'], 'label':item['label']} for item in items], ensure_ascii=False, separators=(',', ':')) if items else ''


def encode_references(paths):
    paths = list(dict.fromkeys(p for p in paths if p))
    if not paths:
        return ''
    if len(paths) == 1:
        return paths[0]
    return json.dumps(paths, ensure_ascii=False, separators=(',', ':'))
