"""Session file discovery and search."""

import json
from datetime import datetime
from pathlib import Path

from deglacer.parsing import is_human_message
from deglacer.content import extract_human_text, extract_assistant_content


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
    """
    if base is None:
        base = Path.home() / '.claude' / 'projects'

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

    return sessions[:limit] if limit else sessions


def search_sessions(
    term: str,
    base: Path | None = None,
    limit: int = 10,
) -> list[dict]:
    """Search for a term across recent session files.

    Searches human messages and assistant text (case-insensitive).
    Returns at most one match per session file.
    """
    if base is None:
        base = Path.home() / '.claude' / 'projects'

    results = []
    sessions = find_sessions(base, limit=200)

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
                            'match': text[:200],
                        })
                        break
        except OSError:
            continue

        if len(results) >= limit:
            break

    return results
