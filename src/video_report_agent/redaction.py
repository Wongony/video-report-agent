"""Remove credentials and resource URLs before diagnostic persistence."""

import re

_SENSITIVE = re.compile(
    r"key|secret|token|authorization|signature|policy|credential|url|upload", re.I
)
_URL = re.compile(r"(?:https?://|oss://)[^\s\"'<>]+")


def redact(value):
    if isinstance(value, dict):
        return {k: redact(v) for k, v in value.items() if not _SENSITIVE.search(k)}
    if isinstance(value, (list, tuple)):
        return [redact(v) for v in value]
    if isinstance(value, str):
        value = _URL.sub("[resource URL removed]", value)
        return re.sub(r"\b(?:sk-[\w-]+|Bearer\s+\S+)", "[credential removed]", value)
    return value
