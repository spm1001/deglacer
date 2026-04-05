"""Content extraction from human and assistant messages."""

import re


# System XML tags injected by CC that should be stripped from human messages
_SYSTEM_TAG_PATTERNS = [
    re.compile(r'<command-message>.*?</command-message>\s*', re.DOTALL),
    re.compile(r'<command-name>.*?</command-name>\s*', re.DOTALL),
    re.compile(r'<task-notification>.*?</task-notification>\s*', re.DOTALL),
    re.compile(r'<system-reminder>.*?</system-reminder>\s*', re.DOTALL),
]


def extract_human_text(entry: dict) -> str:
    """Extract text from a human message, stripping system tags."""
    content = entry.get('message', {}).get('content', '')
    if not isinstance(content, str):
        return ''
    for pattern in _SYSTEM_TAG_PATTERNS:
        content = pattern.sub('', content)
    return content.strip()


def extract_assistant_content(
    entry: dict,
    with_tools: bool = False,
    with_thinking: bool = False,
) -> str:
    """Extract text from an assistant message's content blocks.

    Handles the multi-entry-same-message-id dragon by accepting
    pre-merged content blocks.
    """
    msg = entry.get('message', {})
    content = msg.get('content', [])
    if not isinstance(content, list):
        return str(content) if content else ''

    parts = []
    for block in content:
        if not isinstance(block, dict):
            continue
        btype = block.get('type')

        if btype == 'text':
            text = block.get('text', '')
            if text:
                parts.append(text)

        elif btype == 'tool_use' and with_tools:
            parts.append(_format_tool_use(block))

        elif btype == 'thinking' and with_thinking:
            thinking = block.get('thinking', '')
            if thinking:
                parts.append(f'<thinking>\n{thinking}\n</thinking>')

    return '\n'.join(parts)


def _format_tool_use(block: dict) -> str:
    """Format a tool_use content block as a summary string."""
    name = block.get('name', '?')
    inp = block.get('input', {})

    if name == 'Bash':
        cmd = inp.get('command', '')
        return f'[tool: {name}] {cmd[:200]}'
    elif name in ('Read', 'Glob', 'Grep'):
        summary = inp.get('file_path') or inp.get('pattern') or inp.get('path', '')
        return f'[tool: {name}] {summary[:200]}'
    elif name in ('Write', 'Edit'):
        fp = inp.get('file_path', '')
        return f'[tool: {name}] {fp}'
    elif name == 'Agent':
        desc = inp.get('description', '')
        return f'[tool: {name}] {desc}'
    else:
        return f'[tool: {name}]'
