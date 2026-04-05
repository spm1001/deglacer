"""Conversation reconstruction from raw JSONL entries.

Handles the CC streaming dragon: multiple JSONL lines can share the same
message.id with incremental content updates. This module merges them into
coherent turns.
"""

from deglacer.parsing import is_human_message
from deglacer.content import extract_human_text, extract_assistant_content


def merge_assistant_entries(entries: list[dict]) -> list[dict]:
    """Merge assistant entries that share the same message.id.

    CC streams incremental updates — multiple JSONL lines can have the
    same message.id, each with only the new content blocks. We merge
    them into a single entry with all blocks combined, deduplicating
    tool_use blocks by their id.
    """
    merged = {}
    order = []

    for entry in entries:
        if entry.get('type') != 'assistant':
            continue
        msg = entry.get('message', {})
        msg_id = msg.get('id')
        if not msg_id:
            continue

        if msg_id not in merged:
            merged[msg_id] = {
                **entry,
                'message': {**msg, 'content': []},
            }
            order.append(msg_id)

        existing_content = merged[msg_id]['message']['content']
        seen_tool_ids = {
            b.get('id') for b in existing_content
            if isinstance(b, dict) and b.get('type') == 'tool_use'
        }

        for block in msg.get('content', []):
            if not isinstance(block, dict):
                continue
            if block.get('type') == 'tool_use' and block.get('id') in seen_tool_ids:
                continue
            existing_content.append(block)

    return [merged[mid] for mid in order]


def build_turns(
    entries: list[dict],
    with_tools: bool = False,
    with_thinking: bool = False,
    last_n: int | None = None,
) -> list[dict]:
    """Build a list of conversation turns from raw JSONL entries.

    A 'turn' is either a human message, an assistant response, or a
    context compaction summary.
    """
    assistant_merged = merge_assistant_entries(entries)
    assistant_by_id = {
        e['message']['id']: e for e in assistant_merged
    }

    turns = []
    seen_assistant_ids = set()

    for entry in entries:
        etype = entry.get('type')

        if etype == 'user' and is_human_message(entry):
            text = extract_human_text(entry)
            if text:
                turns.append({
                    'role': 'human',
                    'text': text,
                    'timestamp': entry.get('timestamp'),
                })

        elif etype == 'assistant':
            msg_id = entry.get('message', {}).get('id')
            if msg_id and msg_id not in seen_assistant_ids:
                seen_assistant_ids.add(msg_id)
                merged = assistant_by_id.get(msg_id, entry)
                text = extract_assistant_content(
                    merged,
                    with_tools=with_tools,
                    with_thinking=with_thinking,
                )
                if text:
                    turns.append({
                        'role': 'assistant',
                        'text': text,
                        'timestamp': entry.get('timestamp'),
                        'model': merged.get('message', {}).get('model'),
                    })

        elif etype == 'summary':
            turns.append({
                'role': 'system',
                'text': f"[context compacted: {entry.get('summary', '')}]",
                'timestamp': None,
            })

    if last_n is not None:
        turns = turns[-last_n:]

    return turns


def format_text(turns: list[dict]) -> str:
    """Plain text conversation format."""
    lines = []
    for turn in turns:
        role = turn['role'].upper()
        if role == 'SYSTEM':
            lines.append(turn['text'])
        else:
            lines.append(f'── {role} ──')
            lines.append(turn['text'])
        lines.append('')
    return '\n'.join(lines)


def format_json(turns: list[dict]) -> str:
    """Structured JSON output."""
    import json
    return json.dumps(turns, indent=2, default=str)
