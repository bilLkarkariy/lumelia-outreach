"""Keep external text compatible with PostgreSQL JSON operators."""

import json


def clean(value):
    if isinstance(value, str):
        return value.replace("\x00", "\ufffd")
    if isinstance(value, list):
        return [clean(item) for item in value]
    if isinstance(value, dict):
        return {clean(key): clean(item) for key, item in value.items()}
    return value


def dumps(value):
    return json.dumps(clean(value))
