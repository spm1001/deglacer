"""JSONL session loading and entry type classification."""

import json


def parse_session(path: str) -> list[dict]:
    """Parse a CC JSONL file into a list of entries.

    Handles encoding errors gracefully and skips malformed lines.
    """
    entries = []
    with open(path, 'r', errors='replace') as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                entries.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return entries


def is_human_message(entry: dict) -> bool:
    """True if this is a human-typed user message (not tool result, not meta).

    CC overloads the 'user' type for three purposes:
    - Human input: content is a string
    - Tool results: has toolUseResult key
    - Skill/system injections: has isMeta flag
    """
    if entry.get('type') != 'user':
        return False
    if entry.get('isMeta'):
        return False
    if 'toolUseResult' in entry:
        return False
    content = entry.get('message', {}).get('content')
    return isinstance(content, str)


def is_tool_result(entry: dict) -> bool:
    """True if this is a tool result entry."""
    return entry.get('type') == 'user' and 'toolUseResult' in entry


def is_meta(entry: dict) -> bool:
    """True if this is a skill/system injection."""
    return entry.get('type') == 'user' and entry.get('isMeta', False)


VERTEX_REQUEST_PREFIX = 'req_vrtx_'


def billing_lane(entry: dict) -> str | None:
    """Which provider served this assistant response — 'vertex' or 'other'.

    Session JSONL records no provider field, and model ids are identical across
    providers, so `requestId` is the only handle: Vertex stamps `req_vrtx_…`,
    everything else uses a bare `req_…`. Measured across 6,347 sessions and
    ~499k assistant entries (2026-08-26): exactly those two shapes, nothing else.

    Returns None when there is no requestId to read — which is not a lane. Local
    and synthetic responses (`model: "<synthetic>"`) carry none, so absence means
    "no API request was made", not "some other provider".

    'other' rather than 'anthropic-api' deliberately: this corpus contains no
    Bedrock or Foundry traffic, so what those stamp is unmeasured. On an estate
    that uses them, a bare `req_` may not mean the Anthropic API.
    """
    if entry.get('type') != 'assistant':
        return None
    request_id = entry.get('requestId')
    if not request_id:
        return None
    return 'vertex' if request_id.startswith(VERTEX_REQUEST_PREFIX) else 'other'
