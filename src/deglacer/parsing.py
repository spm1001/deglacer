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
