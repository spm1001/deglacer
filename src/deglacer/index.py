"""Whole-history session search: a SQLite FTS5 index over conversation text.

Native replacement for deja (dropped 2026-09-13, dgc-secise), built to the eight
settled decisions on dgc-puwupa:

1. sqlite3 + FTS5, both stdlib — zero external dependencies.
2. Human and assistant TEXT only, via the existing parse path. Never tool
   results, thinking, system, meta or attachment entries. This is the single
   decision that makes native better than deja: no echo hits from a session
   that merely quoted old content, and a far smaller secret surface.
3. One row per TURN (sane bm25 document lengths); ranking aggregated to
   SESSION, because "have we discussed X?" wants a session id.
4. ``~/.cache/deglacer/index.db``, directory 0700, file 0600. The index still
   holds conversation text, which can contain anything a human pasted.
5. Incremental: a ``files`` table keyed on path holds (size, mtime); a file
   whose size or mtime moved is re-indexed, the rest are skipped.
6. The prune guard: a scan that would drop >= PRUNE_GUARD_RATIO of the indexed
   files REFUSES without --force-prune. deja's reconciler once read an empty
   scan of a symlinked root as 6,092 deletions and wiped its index. The root
   is realpath-resolved before walking for the same reason.
7. ``--index [--rebuild] [--force-prune]`` / ``--search TERMS [--limit N]
   [--since DATE]``; search output columns match ``--recent``.
8. Both modes are named in ``cli._mode`` so per-mode telemetry stays whole.

Schema: ``turns`` is an ordinary table (id, file_id, session_id, role, ts,
text) and ``turns_fts`` is an external-content FTS5 table over it, kept in
step by triggers. The split is what makes incremental re-indexing cheap — a
changed file's old rows go with ``DELETE FROM turns WHERE file_id=?`` on an
index, where a bare FTS5 table would scan every row for an UNINDEXED path.

Query semantics: terms are OR-ed and bm25-ranked, so a whole question works
as well as a rare fragment — the rows that match more of the terms score
higher, and IDF keeps filler words near zero. A double-quoted phrase in the
query is a phrase match. Session score is its best turn; ties break on the
number of matching turns.

Two aggregation rules lost to best-turn on the 13-task bench (2026-09-13,
26 cases: whole question and short fragment per task, top 20 sessions):
best-turn found 25/26; sum of the top three turns also found 25/26 but swapped
which case missed and dropped one rank-1 hit to rank 12; sum of every turn
found 22/26, because it rewards long sessions over apt ones. Best-turn is also
the rule with nothing to tune.
"""

import os
import re
import sqlite3
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from deglacer.parsing import iter_session, is_human_message
from deglacer.content import extract_human_text, extract_assistant_content
from deglacer.discovery import session_title, _default_base


PRUNE_GUARD_RATIO = 0.5
DEFAULT_SEARCH_LIMIT = 20
_COMMIT_EVERY = 200   # files per transaction during a build

_SCHEMA = """
CREATE TABLE IF NOT EXISTS files (
    id          INTEGER PRIMARY KEY,
    path        TEXT NOT NULL UNIQUE,
    size        INTEGER NOT NULL,
    mtime       REAL NOT NULL,
    indexed_at  TEXT NOT NULL,
    session_id  TEXT NOT NULL,
    cwd         TEXT,
    title       TEXT,
    first_ts    TEXT,
    last_ts     TEXT,
    turns       INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS files_session ON files(session_id);
CREATE TABLE IF NOT EXISTS turns (
    id          INTEGER PRIMARY KEY,
    file_id     INTEGER NOT NULL REFERENCES files(id) ON DELETE CASCADE,
    session_id  TEXT NOT NULL,
    role        TEXT NOT NULL,
    ts          TEXT,
    text        TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS turns_file ON turns(file_id);
CREATE VIRTUAL TABLE IF NOT EXISTS turns_fts USING fts5(
    text,
    content='turns',
    content_rowid='id',
    tokenize='unicode61 remove_diacritics 2'
);
CREATE TRIGGER IF NOT EXISTS turns_ai AFTER INSERT ON turns BEGIN
    INSERT INTO turns_fts(rowid, text) VALUES (new.id, new.text);
END;
CREATE TRIGGER IF NOT EXISTS turns_ad AFTER DELETE ON turns BEGIN
    INSERT INTO turns_fts(turns_fts, rowid, text) VALUES ('delete', old.id, old.text);
END;
CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT
);
"""


def default_index_path() -> Path:
    return Path.home() / '.cache' / 'deglacer' / 'index.db'


class IndexMissing(Exception):
    """No index at the given path — run --index first."""


class PruneRefused(Exception):
    """The scan would remove too much of the index; see build_index."""

    def __init__(self, would_remove: list[str], indexed: int, root: Path):
        self.would_remove = would_remove
        self.indexed = indexed
        self.root = root
        super().__init__(
            f"refusing to prune {len(would_remove)} of {indexed} indexed files "
            f"({len(would_remove) / indexed:.0%}) after a scan of {root}"
        )


@dataclass
class IndexPlan:
    root: Path
    to_index: list[tuple[str, int, float]]   # (path, size, mtime) — new or changed
    unchanged: int
    to_remove: list[str]                     # indexed paths the scan no longer sees
    indexed: int                             # rows in files before this run
    bytes_to_index: int = 0


@dataclass
class IndexSummary:
    root: Path
    db_path: Path
    indexed: int = 0        # files (re)indexed this run
    turns: int = 0          # turn rows written this run
    unchanged: int = 0
    removed: int = 0
    seconds: float = 0.0
    files_total: int = 0    # rows in files after the run
    turns_total: int = 0
    db_bytes: int = 0
    errors: list[tuple[str, str]] = field(default_factory=list)


# --- connection -------------------------------------------------------------

def open_index(db_path: Path | None = None, *, readonly: bool = False) -> sqlite3.Connection:
    """Open the index. Writable opens create the directory (0700), the file
    (0600) and the schema; read-only opens raise IndexMissing if absent."""
    db_path = Path(db_path or default_index_path())
    if readonly:
        if not db_path.exists():
            raise IndexMissing(str(db_path))
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    else:
        db_path.parent.mkdir(parents=True, exist_ok=True)
        os.chmod(db_path.parent, 0o700)
        # Create the file ourselves so its mode is 0600 from the first byte,
        # rather than umask-default until a chmod after sqlite's first write.
        fd = os.open(db_path, os.O_CREAT | os.O_RDWR, 0o600)
        os.close(fd)
        os.chmod(db_path, 0o600)
        conn = sqlite3.connect(db_path)
        conn.execute("PRAGMA foreign_keys = ON")
        conn.executescript(_SCHEMA)
    conn.row_factory = sqlite3.Row
    return conn


def index_status(conn: sqlite3.Connection) -> dict:
    """files, turns, indexed_as_of (ISO UTC of the last completed run), root."""
    files = conn.execute("SELECT COUNT(*) FROM files").fetchone()[0]
    turns = conn.execute("SELECT COUNT(*) FROM turns").fetchone()[0]
    meta = {r['key']: r['value'] for r in conn.execute("SELECT key, value FROM meta")}
    return {
        'files': files,
        'turns': turns,
        'indexed_as_of': meta.get('indexed_as_of'),
        'root': meta.get('root'),
    }


# --- walking and planning ---------------------------------------------------

def _walk_sessions(root: Path):
    """Yield (path, size, mtime) for every top-level session file under root.

    Subagent transcripts (``<session>/subagents/*.jsonl``) are skipped, as in
    discovery.find_sessions. One scandir per directory; no rglob.
    """
    stack = [str(root)]
    while stack:
        current = stack.pop()
        try:
            with os.scandir(current) as it:
                for entry in it:
                    if entry.is_dir(follow_symlinks=False):
                        if entry.name != 'subagents':
                            stack.append(entry.path)
                    elif entry.name.endswith('.jsonl') and entry.is_file(follow_symlinks=False):
                        st = entry.stat(follow_symlinks=False)
                        yield entry.path, st.st_size, st.st_mtime
        except OSError:
            continue


def plan_index(conn: sqlite3.Connection, root: Path | None = None) -> IndexPlan:
    """Compare the filesystem against the files table. Reads only stat()s.

    The root is realpath-resolved BEFORE the walk: a symlinked projects/ (the
    commis seat's layout) must index the target, not read as empty.
    """
    root = Path(os.path.realpath(root or _default_base()))
    known = {
        r['path']: (r['size'], r['mtime'])
        for r in conn.execute("SELECT path, size, mtime FROM files")
    }
    seen = set()
    to_index = []
    unchanged = 0
    total_bytes = 0
    for path, size, mtime in _walk_sessions(root):
        seen.add(path)
        prior = known.get(path)
        if prior and prior[0] == size and prior[1] == mtime:
            unchanged += 1
            continue
        to_index.append((path, size, mtime))
        total_bytes += size
    to_remove = sorted(p for p in known if p not in seen)
    return IndexPlan(
        root=root,
        to_index=sorted(to_index),
        unchanged=unchanged,
        to_remove=to_remove,
        indexed=len(known),
        bytes_to_index=total_bytes,
    )


# --- indexing one file ------------------------------------------------------

def _iter_turns(path: str):
    """Yield (role, ts, text) per conversational turn, streaming.

    Human turns come from is_human_message/extract_human_text; assistant turns
    group consecutive entries sharing a message.id (the streaming dragon) and
    take text blocks only — extract_assistant_content with tools and thinking
    off. Nothing else in the file is read into the index. Also yields the
    session's identity as a leading ('_meta', ...) tuple once it is known.
    """
    cur_id = None
    cur_ts = None
    cur_parts: list[str] = []
    flushed: set[str] = set()
    meta_sent = False
    session_id = cwd = first_ts = last_ts = None

    def flush():
        nonlocal cur_id, cur_ts, cur_parts
        if cur_parts:
            text = '\n'.join(cur_parts).strip()
            if text:
                yield 'assistant', cur_ts, text
        if cur_id:
            flushed.add(cur_id)
        cur_id, cur_ts, cur_parts = None, None, []

    for entry in iter_session(path):
        ts = entry.get('timestamp')
        if ts:
            first_ts = first_ts or ts
            last_ts = ts
        if not meta_sent:
            session_id = session_id or entry.get('sessionId')
            cwd = cwd or entry.get('cwd')
            if session_id and cwd:
                meta_sent = True

        if entry.get('type') == 'assistant':
            mid = (entry.get('message') or {}).get('id')
            if mid is None:
                # An id-less entry (synthetic/local) is a turn on its own.
                yield from flush()
                text = extract_assistant_content(entry)
                if text:
                    yield 'assistant', ts, text
                continue
            if mid != cur_id:
                yield from flush()
                if mid in flushed:
                    continue  # a resumed session replaying a turn already indexed
                cur_id, cur_ts = mid, ts
            text = extract_assistant_content(entry)
            if text:
                cur_parts.append(text)
            continue

        # Any non-assistant entry ends the current assistant turn.
        yield from flush()
        if is_human_message(entry):
            text = extract_human_text(entry)
            if text:
                yield 'human', ts, text

    yield from flush()
    yield '_meta', None, {
        'session_id': session_id,
        'cwd': cwd,
        'first_ts': first_ts,
        'last_ts': last_ts,
    }


def index_file(conn: sqlite3.Connection, path: str, size: int, mtime: float) -> int:
    """(Re)index one session file inside the caller's transaction. Returns turns written."""
    row = conn.execute("SELECT id FROM files WHERE path = ?", (path,)).fetchone()
    if row:
        conn.execute("DELETE FROM turns WHERE file_id = ?", (row['id'],))
        conn.execute("DELETE FROM files WHERE id = ?", (row['id'],))

    turns = []
    meta = {}
    for role, ts, text in _iter_turns(path):
        if role == '_meta':
            meta = text
        else:
            turns.append((role, ts, text))

    session_id = meta.get('session_id') or Path(path).stem
    conn.execute(
        "INSERT INTO files(path, size, mtime, indexed_at, session_id, cwd, title, first_ts, last_ts, turns)"
        " VALUES (?,?,?,?,?,?,?,?,?,?)",
        (path, size, mtime, _now(), session_id, meta.get('cwd'), session_title(path),
         meta.get('first_ts'), meta.get('last_ts'), len(turns)),
    )
    file_id = conn.execute("SELECT id FROM files WHERE path = ?", (path,)).fetchone()['id']
    conn.executemany(
        "INSERT INTO turns(file_id, session_id, role, ts, text) VALUES (?,?,?,?,?)",
        ((file_id, session_id, role, ts, text) for role, ts, text in turns),
    )
    return len(turns)


# --- the build --------------------------------------------------------------

def build_index(
    root: Path | None = None,
    db_path: Path | None = None,
    *,
    rebuild: bool = False,
    force_prune: bool = False,
    progress=None,
) -> IndexSummary:
    """Bring the index up to date with the session files under root.

    ``progress(done, total, bytes_done, bytes_total)`` is called after every
    file when given. Raises PruneRefused — before touching anything — when
    the scan would remove >= PRUNE_GUARD_RATIO of the indexed files and
    force_prune is False.
    """
    db_path = Path(db_path or default_index_path())
    if rebuild and db_path.exists():
        os.remove(db_path)
        for suffix in ('-journal', '-wal', '-shm'):
            side = Path(str(db_path) + suffix)
            if side.exists():
                os.remove(side)

    started = time.monotonic()
    conn = open_index(db_path)
    try:
        plan = plan_index(conn, root)
        summary = IndexSummary(root=plan.root, db_path=db_path, unchanged=plan.unchanged)

        # The prune guard, evaluated before a single write. deja read an empty
        # scan of a symlinked root as "every file was deleted" and dropped
        # 6,092 sessions; a scan that would remove half or more of what is
        # indexed is far likelier to be a wrong root than a real purge.
        if (plan.indexed and plan.to_remove and not force_prune
                and len(plan.to_remove) / plan.indexed >= PRUNE_GUARD_RATIO):
            raise PruneRefused(plan.to_remove, plan.indexed, plan.root)

        conn.execute("PRAGMA synchronous = OFF")
        total = len(plan.to_index)
        bytes_done = 0
        conn.execute("BEGIN")
        for n, (path, size, mtime) in enumerate(plan.to_index, 1):
            try:
                summary.turns += index_file(conn, path, size, mtime)
                summary.indexed += 1
            except (OSError, sqlite3.Error) as exc:
                summary.errors.append((path, f"{type(exc).__name__}: {exc}"))
            bytes_done += size
            if n % _COMMIT_EVERY == 0:
                conn.execute("COMMIT")
                conn.execute("BEGIN")
            if progress:
                progress(n, total, bytes_done, plan.bytes_to_index)

        for path in plan.to_remove:
            row = conn.execute("SELECT id FROM files WHERE path = ?", (path,)).fetchone()
            if row:
                conn.execute("DELETE FROM turns WHERE file_id = ?", (row['id'],))
                conn.execute("DELETE FROM files WHERE id = ?", (row['id'],))
                summary.removed += 1

        conn.execute("INSERT OR REPLACE INTO meta(key, value) VALUES ('indexed_as_of', ?)", (_now(),))
        conn.execute("INSERT OR REPLACE INTO meta(key, value) VALUES ('root', ?)", (str(plan.root),))
        conn.execute("COMMIT")

        if summary.indexed >= 1000 or rebuild:
            # Merge the FTS b-trees a large build leaves behind; queries get
            # faster and the file smaller. Seconds on a full corpus.
            conn.execute("INSERT INTO turns_fts(turns_fts) VALUES ('optimize')")
            conn.commit()

        status = index_status(conn)
        summary.files_total = status['files']
        summary.turns_total = status['turns']
    finally:
        conn.close()

    os.chmod(db_path, 0o600)
    summary.db_bytes = db_path.stat().st_size
    summary.seconds = time.monotonic() - started
    return summary


# --- search -----------------------------------------------------------------

_QUERY_TOKEN = re.compile(r'"([^"]*)"|(\S+)')

# Dropped from a query when anything else remains. bm25's IDF already scores
# these near zero; dropping them shortens the OR over a whole-sentence query
# (the slow shape: 400-550 ms against ~100 ms for a fragment) and moved one
# bench case from rank 17 to 13 with nothing else changing (2026-09-13, all
# 26 cases). Inside a quoted phrase they are kept — "the pass" is a phrase.
_STOPWORDS = frozenset(
    "a an and are as at be been but by can could did do does for from had has "
    "have how i if in into is it its just of on or our so that the their them "
    "then there these they this to us was we were what when where which who "
    "why will with would you your".split()
)


def build_match(query: str) -> str:
    """Turn a user query into an FTS5 MATCH expression.

    Every whitespace-separated word, and every double-quoted phrase, becomes
    one FTS5 string — the tokenizer then splits it, so ``mit-pca`` and
    ``25.12.5`` become adjacent-token phrases rather than syntax errors —
    and the strings are OR-ed. Bare stopwords are dropped unless the query is
    nothing but stopwords. FTS5 operator words (AND, OR, NOT, NEAR) are quoted
    like everything else and so match literally.
    """
    kept, dropped = [], []
    for phrase, word in _QUERY_TOKEN.findall(query):
        token = (phrase or word).strip()
        if not token:
            continue
        quoted = '"' + token.replace('"', '""') + '"'
        if not phrase and token.lower() in _STOPWORDS:
            dropped.append(quoted)
        else:
            kept.append(quoted)
    return ' OR '.join(kept or dropped)


def search_index(
    query: str,
    db_path: Path | None = None,
    *,
    limit: int = DEFAULT_SEARCH_LIMIT,
    since: str | None = None,
) -> tuple[list[dict], dict]:
    """Rank sessions by their best-matching turn. Returns (results, scope).

    Each result carries what ``--recent`` prints — path, size, mtime,
    sessionId, title — plus ``score`` (bm25 of the best turn, lower is
    better), ``hits`` (matching turns) and ``cwd``. A session present under
    two paths (a Mac and a tube copy) is one result, at its newest path.
    ``scope`` carries files, turns and indexed_as_of for the caller's scope
    line; a stale index must never read as an absence.
    """
    conn = open_index(db_path, readonly=True)
    try:
        scope = index_status(conn)
        match = build_match(query)
        if not match:
            return [], scope
        since_ts = datetime.strptime(since, '%Y-%m-%d').timestamp() if since else None
        sql = """
            WITH m AS (
                SELECT rowid AS id, rank AS score FROM turns_fts WHERE turns_fts MATCH ?
            ),
            per_session AS (
                SELECT t.session_id AS session_id, MIN(m.score) AS best, COUNT(*) AS hits
                FROM m JOIN turns t ON t.id = m.id
                GROUP BY t.session_id
            )
            SELECT p.session_id, p.best, p.hits,
                   f.path, f.size, f.mtime, f.title, f.cwd
            FROM per_session p
            JOIN files f ON f.id = (
                SELECT id FROM files WHERE session_id = p.session_id
                ORDER BY mtime DESC, id DESC LIMIT 1
            )
            WHERE 1 = 1
        """
        params: list = [match]
        if since_ts is not None:
            sql += " AND f.mtime >= ?"
            params.append(since_ts)
        sql += " ORDER BY p.best ASC, p.hits DESC LIMIT ?"
        params.append(limit)
        rows = conn.execute(sql, params).fetchall()
    finally:
        conn.close()

    results = []
    for r in rows:
        results.append({
            'path': r['path'],
            'size': r['size'],
            'mtime': r['mtime'],
            'sessionId': r['session_id'],
            'title': r['title'],
            'cwd': r['cwd'],
            'score': r['best'],
            'hits': r['hits'],
        })
    return results, scope


def _now() -> str:
    return datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
