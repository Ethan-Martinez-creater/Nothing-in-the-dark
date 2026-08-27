"""M10: lenient parsing of structured output from free-form model text.

Expert agents are instructed to return JSON, but real models wrap it in
markdown fences or prose. ``repair_json_content`` tries progressively
harder strategies and only gives up when no JSON object can be found —
mirroring the "no fake data" contract: an unparseable response is
returned verbatim with ``parsed: false`` instead of fabricating fields.
"""

from __future__ import annotations

import json
import re
from typing import Any

_FENCE_RE = re.compile(r"```(?:json)?\s*(.*?)```", re.DOTALL | re.IGNORECASE)


def repair_json_content(content: str) -> dict[str, Any] | None:
    """Extract a JSON object from model output, or ``None`` if impossible.

    Strategy ladder:
    1. strict ``json.loads`` of the whole text;
    2. the first ````` ```json ... ``` ```` fenced block;
    3. ``raw_decode`` starting at each ``{`` — handles surrounding prose
       and nested braces/strings correctly without regex slicing.
    """
    text = (content or "").strip()
    if not text:
        return None

    try:
        value = json.loads(text)
        if isinstance(value, dict):
            return value
    except (ValueError, TypeError):
        pass

    match = _FENCE_RE.search(text)
    if match is not None:
        try:
            value = json.loads(match.group(1).strip())
            if isinstance(value, dict):
                return value
        except (ValueError, TypeError):
            pass

    decoder = json.JSONDecoder()
    for index, char in enumerate(text):
        if char != "{":
            continue
        try:
            value, _ = decoder.raw_decode(text[index:])
        except (ValueError, TypeError):
            continue
        if isinstance(value, dict):
            return value
    return None
