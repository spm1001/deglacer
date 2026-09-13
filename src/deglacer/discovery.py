"""Session file discovery and search."""

import json
from datetime import datetime
from pathlib import Path

from deglacer.parsing import is_human_message
from deglacer.content import extract_human_text, extract_assistant_content


DEFAULT_SEARCH_WINDOW = 200   # most-recent sessions --find scans
DEFAULT_SEARCH_LIMIT = 10     # matches --find stops at

# How far back from the end of a transcript the title reader looks. The
# ai-title / last-prompt entries land near the tail; 64 KB covers the tail
# of every transcript measured (2026-09-13) without reading the whole file.
_TAIL_BYTES = 64 * 1024


def _default_base() -> Path:
    return Path.home() / '.claude' / 'projects'


def count_sessions(base: Path | None = None) -> int:
    """How many top-level session files exist under base (subagents excluded).

    This is the denominator every scoped listing or search owes its reader:
    "10 shown" means nothing until it sits beside "of 14,157".
    """
    base = base or _default_base()
    return sum(1 for p in base.rglob('*.jsonl') if '/subagents/' not in str(p))


def session_title(path: str | Path) -> str | None:
    """A human-recognisable title for a session, or None.

    Precedence, matching what /resume shows: the LAST ``ai-title`` entry
    (``aiTitle``), else the last ``last-prompt`` entry (``lastPrompt``), read
    from the tail of the file. Sessions with neither (very short, or
    headless before a title was minted) return None — the caller prints an
    honest placeholder rather than a blank column.
    """
    try:
        with open(path, 'rb') as f:
            f.seek(0, 2)
            size = f.tell()
            f.seek(max(0, size - _TAIL_BYTES))
            tail = f.read().decode('utf-8', errors='replace')
    except OSError:
        return None

    last_prompt = None
    for line in reversed(tail.splitlines()):
        if '"ai-title"' not in line and '"last-prompt"' not in line:
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue  # the first line of a tail read is usually a fragment
        if obj.get('type') == 'ai-title' and obj.get('aiTitle'):
            return obj['aiTitle']
        if obj.get('type') == 'last-prompt' and obj.get('lastPrompt') and last_prompt is None:
            last_prompt = obj['lastPrompt']
    if last_prompt:
        return ' '.join(last_prompt.split())
    return None


def find_sessions(
    base: Path | None = None,
    limit: int = 20,
    since: str | None = None,
) -> list[dict]:
    """Find recent CC session JSONL files.

    Args:
        base: Root directory to search (default: ~/.claude/projects/).
        limit: Maximum number of sessions to return. Pass 0 for unlimited.
        since: ISO date string (YYYY-MM-DD). Only return sessions modified
               on or after this date.

    Each returned session carries ``title`` (see ``session_title``) alongside
    the first-line fields; ``title`` is None when the transcript has none.
    """
    base = base or _default_base()

    since_ts = None
    if since:
        since_ts = datetime.strptime(since, '%Y-%m-%d').timestamp()

    sessions = []
    for jsonl in base.rglob('*.jsonl'):
        if '/subagents/' in str(jsonl):
            continue
        stat = jsonl.stat()
        if since_ts and stat.st_mtime < since_ts:
            continue
        sessions.append({
            'path': str(jsonl),
            'size': stat.st_size,
            'mtime': stat.st_mtime,
        })

    sessions.sort(key=lambda s: s['mtime'], reverse=True)

    enrich_limit = limit if limit else len(sessions)
    for s in sessions[:enrich_limit]:
        try:
            with open(s['path'], 'r', errors='replace') as f:
                first_line = f.readline().strip()
                if first_line:
                    obj = json.loads(first_line)
                    s['sessionId'] = obj.get('sessionId', '')
                    s['slug'] = obj.get('slug', '')
                    s['version'] = obj.get('version', '')
        except (json.JSONDecodeError, OSError):
            pass
        s['title'] = session_title(s['path'])

    return sessions[:limit] if limit else sessions


def search_sessions(
    term: str,
    base: Path | None = None,
    limit: int = DEFAULT_SEARCH_LIMIT,
    window: int = DEFAULT_SEARCH_WINDOW,
    since: str | None = None,
) -> list[dict]:
    """Search for a term across recent session files.

    Searches human messages and assistant text (case-insensitive).
    Returns at most one match per session file, and at most ``limit`` in all.

    Scope: only the ``window`` most-recently-modified sessions are read
    (0 = every session), narrowed further by ``since`` when given. This is a
    recent-window convenience, not a memory search — the CLI prints the
    scope beside the results so an empty answer cannot pass as "never
    discussed" (dgc-nidobe).
    """
    base = base or _default_base()

    results = []
    sessions = find_sessions(base, limit=window, since=since)

    for s in sessions:
        try:
            with open(s['path'], 'r', errors='replace') as f:
                for i, line in enumerate(f):
                    try:
                        obj = json.loads(line)
                    except json.JSONDecodeError:
                        continue

                    if is_human_message(obj):
                        text = extract_human_text(obj)
                    elif obj.get('type') == 'assistant':
                        text = extract_assistant_content(obj)
                    else:
                        continue

                    if term.lower() in text.lower():
                        results.append({
                            'file': s['path'],
                            'line': i + 1,
                            'sessionId': s.get('sessionId', ''),
                            'slug': s.get('slug', ''),
                            'title': s.get('title'),
                            'match': text[:200],
                        })
                        break
        except OSError:
            continue

        if len(results) >= limit:
            break

    return results
