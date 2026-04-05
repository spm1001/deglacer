"""Session metadata extraction — timing, tokens, tool usage, summaries."""

from collections import Counter

from deglacer.parsing import is_human_message
from deglacer.content import extract_human_text
from deglacer.conversation import merge_assistant_entries


def format_stats(entries: list[dict]) -> str:
    """Session statistics — tokens, models, tools, timing."""
    types = Counter(e.get('type') for e in entries)
    models = Counter()
    tools = Counter()
    total_input = 0
    total_output = 0

    for entry in entries:
        if entry.get('type') == 'assistant':
            msg = entry.get('message', {})
            m = msg.get('model')
            if m:
                models[m] += 1
            usage = msg.get('usage', {})
            # input_tokens is only the non-cached portion;
            # real input = input + cache_creation + cache_read
            total_input += (
                usage.get('input_tokens', 0)
                + usage.get('cache_creation_input_tokens', 0)
                + usage.get('cache_read_input_tokens', 0)
            )
            total_output += usage.get('output_tokens', 0)

            for block in msg.get('content', []):
                if isinstance(block, dict) and block.get('type') == 'tool_use':
                    tools[block.get('name', '?')] += 1

    human_count = sum(1 for e in entries if is_human_message(e))
    assistant_merged = merge_assistant_entries(entries)

    timestamps = [e.get('timestamp') for e in entries if e.get('timestamp')]
    first = timestamps[0] if timestamps else '?'
    last = timestamps[-1] if timestamps else '?'

    session_id = '?'
    version = '?'
    slug = ''
    for e in entries:
        if session_id == '?' and e.get('sessionId'):
            session_id = e['sessionId']
        if version == '?' and e.get('version'):
            version = e['version']
        if not slug and e.get('slug'):
            slug = e['slug']
        if session_id != '?' and version != '?':
            break

    lines = [
        f'Session: {session_id}',
        f'Slug:    {slug}' if slug else '',
        f'Version: {version}',
        f'Period:  {first} → {last}',
        f'',
        f'Entry types:',
    ]
    for t, c in types.most_common():
        lines.append(f'  {t:25s} {c:5d}')

    lines.extend([
        f'',
        f'Human messages:    {human_count}',
        f'Assistant turns:   {len(assistant_merged)}',
        f'',
        f'Models:',
    ])
    for m, c in models.most_common():
        lines.append(f'  {m:40s} {c:5d}')

    lines.extend([
        f'',
        f'Tokens:  input={total_input:,}  output={total_output:,}  total={total_input+total_output:,}',
        f'',
        f'Tool usage:',
    ])
    for t, c in tools.most_common():
        lines.append(f'  {t:20s} {c:5d}')

    return '\n'.join(lines)


def format_timeline(entries: list[dict]) -> str:
    """Timestamped turn log showing what happened when."""
    lines = []
    for entry in entries:
        ts = entry.get('timestamp', '')
        etype = entry.get('type', '?')

        if etype == 'assistant':
            msg = entry.get('message', {})
            blocks = msg.get('content', [])
            tool_names = [
                b.get('name', '?') for b in blocks
                if isinstance(b, dict) and b.get('type') == 'tool_use'
            ]
            texts = [
                b.get('text', '')[:80] for b in blocks
                if isinstance(b, dict) and b.get('type') == 'text'
            ]
            if tool_names:
                lines.append(f'{ts}  assistant  tools: {", ".join(tool_names)}')
            elif texts and any(t.strip() for t in texts):
                preview = next(t for t in texts if t.strip())[:80]
                lines.append(f'{ts}  assistant  "{preview}"')

        elif etype == 'user' and is_human_message(entry):
            text = extract_human_text(entry)[:80]
            lines.append(f'{ts}  human      "{text}"')

        elif etype == 'system':
            sub = entry.get('subtype', '')
            if sub == 'api_error':
                err = entry.get('error', {})
                lines.append(f'{ts}  system     API error: {err}')
            elif sub == 'turn_duration':
                ms = entry.get('durationMs', '?')
                lines.append(f'{ts}  system     turn: {ms}ms')

    return '\n'.join(lines)


def format_summary(entries: list[dict]) -> str:
    """Human-messages-only summary — what was discussed without the detail."""
    lines = []
    human_count = 0
    for entry in entries:
        if is_human_message(entry):
            human_count += 1
            text = extract_human_text(entry)
            ts = entry.get('timestamp', '')
            if ts:
                ts = ts[11:16]  # HH:MM
            preview = text.replace('\n', ' ')
            if len(preview) > 120:
                preview = preview[:117] + '...'
            lines.append(f'  {ts}  {preview}')

    assistant_merged = merge_assistant_entries(entries)
    timestamps = [e.get('timestamp') for e in entries if e.get('timestamp')]
    period = ''
    if timestamps:
        period = f'{timestamps[0][11:16]}–{timestamps[-1][11:16]}'

    header = f'{human_count} messages, {len(assistant_merged)} turns, {period}'
    return header + '\n' + '\n'.join(lines)
