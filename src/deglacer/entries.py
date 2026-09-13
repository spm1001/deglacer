"""Forensic per-entry view of a transcript: ``--entries`` and ``--meta`` (dgc-mahula).

Built from what Claudes actually project when they hand-roll jq over a
transcript (605 programs from 87 sessions, tools/jq-programs-2026-09-13.txt):
which entries, which tool calls and what they returned, which model, which
hook, which request. The turn-shaped ``--json`` answers "what was said"; this
answers "what happened", one JSON object per line, in file order, streaming.

It is not a jq. It gives jq a clean input, carrying the two things hand-rolled
reads get wrong:

* **classification** — ``kind`` is deglacer's reading of the entry, not the
  raw ``type``: ``human`` / ``tool_result`` / ``meta`` for the triple-duty
  user entry, ``assistant``, ``system:<subtype>``, ``attachment:<type>``, and
  the raw type passed through by name for anything else. Nothing is dropped:
  every non-blank line of the file yields at least one row, and an entry
  type this module has never seen still comes out, under its own name.
* **pairing** — each ``tool_use`` block becomes its own row, ``kind`` =
  ``tool_use``, carrying ``name``, ``input`` and its ``result`` resolved by
  ``tool_use_id`` across lines: ``{isError, text (first 400 chars), chars,
  uuid}``, or null when no result ever arrived. The ``tool_result`` row in
  turn carries the tool's ``name``.

One deliberate departure from per-entry faithfulness: ``usage`` appears once
per API request, on the entry ``dedupe_by_request`` would keep (highest
output_tokens — output grows as the response streams), so summing ``usage``
over ``--entries`` is right. Summing it over the raw file is the 2.3× overcount
of dgc-tenoze, and 22 of the 605 programs did exactly that.

Every other top-level key of the raw entry is passed through flat and
unchanged (``permissionMode``, ``isMeta``, ``toolUseResult``, ``version``,
``gitBranch``, ``entrypoint``, ``slug``, ``attachment``, ``subtype``, …), so
a jq written against the raw file keeps working against this output, minus
the ``.message`` wrapper — which is unpacked into ``text``, ``thinking``,
``messageId``, ``stopReason`` and the ``tool_use`` rows.

Streaming: rows wait in an ordered queue only while something ahead of them
is unresolved — an assistant request group waiting to see which entry
carries the usage, a ``tool_use`` waiting for its result. A new human turn
releases every pending ``tool_use`` (its result is not coming), and a hard
cap releases the head regardless, so memory is bounded by the distance
between a call and its result, never by the file.
"""

import json
from collections import Counter, deque

from deglacer.parsing import iter_session, is_human_message
from deglacer.content import extract_human_text
from deglacer.discovery import session_title


RESULT_PREVIEW_CHARS = 400
_MAX_QUEUE = 5000

# Raw top-level keys consumed into fixed positions; everything else passes through.
_CONSUMED = frozenset({'type', 'timestamp', 'uuid', 'parentUuid', 'requestId', 'cwd', 'sessionId', 'message'})


def classify(entry: dict) -> str:
    """deglacer's reading of an entry — see the module docstring for the vocabulary."""
    t = entry.get('type')
    if t == 'user':
        content = (entry.get('message') or {}).get('content')
        if 'toolUseResult' in entry or _has_block(content, 'tool_result'):
            return 'tool_result'
        if entry.get('isMeta'):
            return 'meta'
        if is_human_message(entry):
            return 'human'
        return 'user'
    if t == 'assistant':
        return 'assistant'
    if t == 'system':
        return f"system:{entry.get('subtype') or '?'}"
    if t == 'attachment':
        return f"attachment:{(entry.get('attachment') or {}).get('type') or '?'}"
    return t if isinstance(t, str) and t else 'untyped'


def _has_block(content, btype: str) -> bool:
    return isinstance(content, list) and any(
        isinstance(b, dict) and b.get('type') == btype for b in content
    )


def _blocks(entry: dict, btype: str) -> list[dict]:
    content = (entry.get('message') or {}).get('content')
    if not isinstance(content, list):
        return []
    return [b for b in content if isinstance(b, dict) and b.get('type') == btype]


def _text_of(content) -> str:
    """Text of a content value: a string as-is, a block list's text blocks joined."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for b in content:
            if isinstance(b, dict) and b.get('type') == 'text' and b.get('text'):
                parts.append(b['text'])
            elif isinstance(b, str):
                parts.append(b)
        return '\n'.join(parts)
    return '' if content is None else str(content)


def _base_row(i: int, entry: dict, kind: str) -> dict:
    row = {'i': i, 'ts': entry.get('timestamp'), 'kind': kind, 'type': entry.get('type')}
    for key in ('uuid', 'parentUuid', 'requestId'):
        if key in entry:
            row[key] = entry[key]
    if kind == 'assistant':
        row['model'] = (entry.get('message') or {}).get('model')
    for key in ('cwd', 'sessionId'):
        if key in entry:
            row[key] = entry[key]
    return row


def _passthrough(row: dict, entry: dict) -> dict:
    for key, value in entry.items():
        if key not in _CONSUMED and key not in row:
            row[key] = value
    return row


def iter_entries(path: str, *, kinds=None, tool: str | None = None):
    """Yield one row per entry (plus one per tool_use block), in file order.

    ``kinds``: iterable of kind names to keep; a bare family name (``system``,
    ``attachment``) keeps every ``family:*``. ``tool``: keep only tool_use and
    tool_result rows for that tool name (implies those two kinds unless
    ``kinds`` says otherwise). Filtering happens after pairing, so a filtered
    view still pairs correctly.
    """
    kinds = set(kinds) if kinds else None
    if tool and not kinds:
        kinds = {'tool_use', 'tool_result'}

    def wanted(row: dict) -> bool:
        if kinds is not None:
            k = row['kind']
            family = k.split(':', 1)[0]
            if k not in kinds and family not in kinds:
                return False
        if tool and row['kind'] in ('tool_use', 'tool_result') and row.get('name') != tool:
            return False
        return True

    queue: deque[dict] = deque()
    pending_tools: dict[str, dict] = {}   # tool_use_id -> tool_use row awaiting a result
    tool_names: dict[str, str] = {}       # tool_use_id -> tool name, for tool_result rows
    group_key = None
    group_rows: list[tuple[dict, dict | None]] = []   # (assistant row, usage) awaiting release

    def close_group():
        nonlocal group_key, group_rows
        if group_rows:
            best_i, best_out = 0, -1
            for n, (_, usage) in enumerate(group_rows):
                out = (usage or {}).get('output_tokens') or 0
                if usage is not None and out >= best_out:
                    best_i, best_out = n, out
            for n, (row, usage) in enumerate(group_rows):
                if n == best_i and usage is not None:
                    row['usage'] = usage
                row.pop('_pending', None)
        group_key, group_rows = None, []

    def release_pending_tools():
        for row in pending_tools.values():
            row.pop('_pending', None)
        pending_tools.clear()

    def drain():
        while queue and not queue[0].get('_pending'):
            row = queue.popleft()
            if wanted(row):
                yield row

    for i, entry in iter_session(path, numbered=True):
        kind = classify(entry)

        if kind == 'assistant':
            msg = entry.get('message') or {}
            key = entry.get('requestId') or msg.get('id') or f'#{i}'
            if group_rows and key != group_key:
                close_group()
            group_key = key
            row = _base_row(i, entry, kind)
            row['messageId'] = msg.get('id')
            row['stopReason'] = msg.get('stop_reason')
            text = _text_of(msg.get('content'))
            if text:
                row['text'] = text
            thinking = '\n'.join(b.get('thinking', '') for b in _blocks(entry, 'thinking') if b.get('thinking'))
            if thinking:
                row['thinking'] = thinking
            row['_pending'] = True
            _passthrough(row, entry)
            queue.append(row)
            group_rows.append((row, msg.get('usage')))
            for block in _blocks(entry, 'tool_use'):
                tid = block.get('id')
                if tid and (tid in pending_tools or tid in tool_names):
                    continue  # the streaming dragon repeats a tool_use block; one call, one row
                trow = {
                    'i': i, 'ts': entry.get('timestamp'), 'kind': 'tool_use',
                    'uuid': entry.get('uuid'), 'requestId': entry.get('requestId'),
                    'model': msg.get('model'), 'cwd': entry.get('cwd'),
                    'sessionId': entry.get('sessionId'), 'messageId': msg.get('id'),
                    'toolUseId': tid, 'name': block.get('name'), 'input': block.get('input'),
                    'result': None, '_pending': True,
                }
                queue.append(trow)
                if tid:
                    pending_tools[tid] = trow
                    tool_names[tid] = block.get('name')
                else:
                    trow.pop('_pending')
            yield from drain()
            continue

        # Only a new HUMAN turn closes the open request group (besides a
        # different request or EOF). A tool_result or attachment does not:
        # with parallel tool calls CC writes block 0, runs it, writes its
        # result, then writes block 1's entry under the same requestId
        # (apiBlockIndex orders them; measured 2026-09-13, 11 such requests
        # in one 2.1.27x transcript). Closing on contiguity split those
        # requests and emitted their usage twice.
        if group_rows and kind == 'human':
            close_group()

        if kind == 'tool_result':
            blocks = _blocks(entry, 'tool_result') or [None]
            for block in blocks:
                row = _base_row(i, entry, kind)
                tid = (block or {}).get('tool_use_id')
                row['toolUseId'] = tid
                row['name'] = tool_names.get(tid)
                is_error = bool((block or {}).get('is_error'))
                text = _text_of((block or {}).get('content')) if block else _text_of(entry.get('toolUseResult'))
                row['isError'] = is_error
                row['text'] = text
                _passthrough(row, entry)
                queue.append(row)
                trow = pending_tools.pop(tid, None) if tid else None
                if trow is not None:
                    trow['result'] = {
                        'isError': is_error,
                        'text': text[:RESULT_PREVIEW_CHARS],
                        'chars': len(text),
                        'uuid': entry.get('uuid'),
                    }
                    trow.pop('_pending', None)
        else:
            row = _base_row(i, entry, kind)
            if kind == 'human':
                row['text'] = extract_human_text(entry)
                release_pending_tools()   # a new turn: earlier calls will not be answered
            elif kind in ('meta', 'user'):
                row['text'] = _text_of((entry.get('message') or {}).get('content'))
            _passthrough(row, entry)
            queue.append(row)

        if len(queue) > _MAX_QUEUE:
            queue[0].pop('_pending', None)
        yield from drain()

    close_group()
    release_pending_tools()
    yield from drain()


def format_entries(path: str, *, kinds=None, tool: str | None = None):
    """Yield JSON lines for iter_entries — the CLI writes these straight out."""
    for row in iter_entries(path, kinds=kinds, tool=tool):
        yield json.dumps(row, ensure_ascii=False, default=str)


def session_meta(path: str) -> dict:
    """One object describing the session as a whole (shape J of the corpus):
    identity, constants, span, and a census of kinds and tools. Streams."""
    first = {}
    versions, models, modes, entrypoints = [], [], [], []
    kinds: Counter = Counter()
    tools: Counter = Counter()
    requests = set()
    first_ts = last_ts = None
    rows = 0
    entries = 0
    last_i = None

    def note(bucket: list, value):
        if value and value not in bucket:
            bucket.append(value)

    for row in iter_entries(path):
        rows += 1
        if row['i'] != last_i:
            entries += 1
            last_i = row['i']
        kinds[row['kind']] += 1
        ts = row.get('ts')
        if ts:
            first_ts = first_ts or ts
            last_ts = ts
        for key in ('sessionId', 'cwd', 'gitBranch', 'slug'):
            if key not in first and row.get(key):
                first[key] = row[key]
        note(versions, row.get('version'))
        note(entrypoints, row.get('entrypoint'))
        note(modes, row.get('permissionMode'))
        if row['kind'] == 'assistant':
            note(models, row.get('model'))
            if row.get('requestId'):
                requests.add(row['requestId'])
        elif row['kind'] == 'tool_use':
            tools[row.get('name') or '?'] += 1

    return {
        'path': path,
        'sessionId': first.get('sessionId'),
        'title': session_title(path),
        'cwd': first.get('cwd'),
        'gitBranch': first.get('gitBranch'),
        'slug': first.get('slug'),
        'versions': versions,
        'entrypoints': entrypoints,
        'permissionModes': modes,
        'models': models,
        'firstTs': first_ts,
        'lastTs': last_ts,
        'entries': entries,
        'rows': rows,
        'requests': len(requests),
        'kinds': dict(kinds.most_common()),
        'tools': dict(tools.most_common()),
    }
