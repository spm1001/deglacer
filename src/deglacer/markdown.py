"""Markdown export — claude.ai-export-format files for CC sessions.

Mirrors the shape Claude.ai's "Export chats" produces, so CC sessions and
web-Claude conversations can sit side-by-side in the same corpus (e.g. for
persona / register study) without smelling different.

Filename convention: "YYYY-MM-DD HHMM — {slug}.md" — caller is responsible
for redirecting stdout to that name. The CLI prints the suggested filename
to stderr.
"""

from datetime import datetime
import re

from deglacer.conversation import merge_assistant_entries
from deglacer.parsing import is_human_message
from deglacer.content import extract_human_text


# CC tool name → (emoji, label, function rendering the input as a one-line arg).
# Modelled on claude.ai's inline conventions (🌐 Fetched, 🔍 Searched, …).
_TOOL_MARKERS: dict[str, tuple[str, str, callable]] = {
    "Bash": ("🔧", "Bash", lambda i: i.get("command", "")),
    "Read": ("📄", "Read", lambda i: i.get("file_path", "")),
    "Edit": ("✏️", "Edit", lambda i: i.get("file_path", "")),
    "Write": ("📝", "Write", lambda i: i.get("file_path", "")),
    "Glob": ("🔍", "Glob", lambda i: i.get("pattern", "")),
    "Grep": ("🔍", "Grep", lambda i: i.get("pattern", "")),
    "WebFetch": ("🌐", "Fetched", lambda i: i.get("url", "")),
    "WebSearch": ("🔎", "Searched", lambda i: i.get("query", "")),
    "Agent": ("🤖", "Agent", lambda i: i.get("description", "")),
    "Skill": ("🎯", "Skill", lambda i: i.get("skill", "")),
    "TaskCreate": ("✅", "Task", lambda i: i.get("subject", "")),
    "TaskUpdate": ("✅", "Task update", lambda i: i.get("taskId", "")),
    "TaskGet": ("✅", "Task get", lambda i: i.get("taskId", "")),
    "TaskList": ("✅", "Task list", lambda i: ""),
    "AskUserQuestion": ("❓", "Ask user", lambda i: ""),
    "ExitPlanMode": ("📋", "Exit plan mode", lambda i: ""),
    "ExitWorktree": ("🌿", "Exit worktree", lambda i: i.get("action", "")),
    "EnterWorktree": ("🌿", "Enter worktree", lambda i: i.get("name", "") or i.get("path", "")),
    "EnterPlanMode": ("📋", "Enter plan mode", lambda i: ""),
    "NotebookEdit": ("📓", "Notebook edit", lambda i: i.get("notebook_path", "")),
}


def _slugify(text: str) -> str:
    """Filename-safe slug — preserve casing, keep most printable chars."""
    if not text:
        return "untitled"
    cleaned = re.sub(r"[^\w\s.-]", " ", text, flags=re.UNICODE)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return cleaned[:80] or "untitled"


def _format_tool_inline(name: str, inp: dict) -> str:
    """One-line markdown marker for a tool_use block."""
    marker = _TOOL_MARKERS.get(name)
    if marker is None:
        emoji, label, arg_fn = "🔨", name, lambda i: ""
    else:
        emoji, label, arg_fn = marker
    arg = (arg_fn(inp) or "").strip()
    if arg:
        if "\n" in arg:
            arg = arg.replace("\n", " ⏎ ")
        if len(arg) > 150:
            arg = arg[:147] + "..."
        return f"{emoji} **{label}:** `{arg}`"
    return f"{emoji} **{label}**"


def _render_assistant(entry: dict, with_thinking: bool) -> str:
    """Render an assistant message — text blocks verbatim, tool_use as inline markers."""
    content = entry.get("message", {}).get("content", [])
    if not isinstance(content, list):
        return str(content) if content else ""
    parts = []
    for block in content:
        if not isinstance(block, dict):
            continue
        btype = block.get("type")
        if btype == "text":
            text = (block.get("text", "") or "").strip()
            if text:
                parts.append(text)
        elif btype == "tool_use":
            parts.append(_format_tool_inline(block.get("name", "?"), block.get("input") or {}))
        elif btype == "thinking" and with_thinking:
            t = (block.get("thinking", "") or "").strip()
            if t:
                parts.append(f"<details><summary>💭 thinking</summary>\n\n{t}\n\n</details>")
    return "\n\n".join(parts)


def _build_markdown_turns(entries: list[dict], with_thinking: bool) -> list[dict]:
    """Build a sequence of {role, text, timestamp} turns for markdown rendering.

    Consecutive same-role turns are coalesced into one bubble — CC sessions
    split a single logical response across many assistant message.ids when
    tool cycles run; claude.ai exports merge these. Coalesce mirrors that
    visual shape and matches what register-study readers expect.
    """
    merged = {e["message"]["id"]: e for e in merge_assistant_entries(entries)}
    raw_turns = []
    seen_assistant_ids: set[str] = set()
    for entry in entries:
        etype = entry.get("type")
        if etype == "user" and is_human_message(entry):
            text = extract_human_text(entry)
            if text:
                raw_turns.append(
                    {"role": "human", "text": text, "timestamp": entry.get("timestamp")}
                )
        elif etype == "assistant":
            msg_id = entry.get("message", {}).get("id")
            if msg_id and msg_id not in seen_assistant_ids:
                seen_assistant_ids.add(msg_id)
                rendered = _render_assistant(merged.get(msg_id, entry), with_thinking=with_thinking)
                if rendered:
                    raw_turns.append(
                        {"role": "assistant", "text": rendered, "timestamp": entry.get("timestamp")}
                    )
        # `summary` entries (compaction markers) deliberately skipped — they're noise
        # in a register-study sample and the surrounding turns read fine without them.

    # Coalesce consecutive same-role turns. Timestamp of the bubble = first turn's.
    coalesced = []
    for turn in raw_turns:
        if coalesced and coalesced[-1]["role"] == turn["role"]:
            coalesced[-1]["text"] += "\n\n" + turn["text"]
        else:
            coalesced.append(dict(turn))
    return coalesced


def _parse_ts(ts: str | None) -> datetime | None:
    if not ts:
        return None
    try:
        return datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except (ValueError, AttributeError):
        return None


def _collect_session_metadata(entries: list[dict], turns: list[dict]) -> dict:
    """Session-level metadata for the frontmatter block."""
    session_id = ""
    slug = ""
    tools_used: set[str] = set()
    for entry in entries:
        if not session_id and entry.get("sessionId"):
            session_id = entry["sessionId"]
        if not slug and entry.get("slug"):
            slug = entry["slug"]
        if entry.get("type") == "assistant":
            for block in entry.get("message", {}).get("content", []) or []:
                if isinstance(block, dict) and block.get("type") == "tool_use":
                    tools_used.add(block.get("name", "?"))
    first_ts = _parse_ts(turns[0]["timestamp"]) if turns else None
    last_ts = _parse_ts(turns[-1]["timestamp"]) if turns else None
    if first_ts is None:
        for e in entries:
            t = _parse_ts(e.get("timestamp"))
            if t:
                first_ts = t
                break
    if last_ts is None:
        last_ts = first_ts
    return {
        "session_id": session_id,
        "slug": slug or "untitled",
        "tools_used": sorted(tools_used),
        "first_ts": first_ts,
        "last_ts": last_ts,
    }


def _title_from_slug(slug: str) -> str:
    """CC slugs are usually 'kebab-case-words' — turn into Title Case."""
    if not slug or slug == "untitled":
        return ""
    return slug.replace("-", " ").replace("_", " ").strip().title()


def _title_from_first_human(turns: list[dict]) -> str:
    """Fallback title: first ~8 words of the first human message, capitalised."""
    for t in turns:
        if t.get("role") == "human" and t.get("text"):
            first_line = t["text"].splitlines()[0].strip()
            words = re.findall(r"\S+", first_line)[:8]
            if words:
                return " ".join(words).strip(".,!?:;\"'")
    return ""


def format_markdown(
    entries: list[dict],
    with_thinking: bool = False,
    last_n: int | None = None,
) -> tuple[str, str]:
    """Render a CC session as claude.ai-export-format markdown.

    Returns (markdown_text, suggested_filename). The filename matches the
    "YYYY-MM-DD HHMM — Title.md" convention; caller decides what to do with it.
    """
    turns = _build_markdown_turns(entries, with_thinking=with_thinking)
    if last_n is not None:
        turns = turns[-last_n:]
    meta = _collect_session_metadata(entries, turns)

    first_ts = meta["first_ts"]
    last_ts = meta["last_ts"]
    date_str = first_ts.strftime("%Y-%m-%d") if first_ts else ""
    time_str = first_ts.strftime("%H%M") if first_ts else ""
    title = (
        _title_from_slug(meta["slug"])
        or _title_from_first_human(turns)
        or (f"Session {meta['session_id'][:8]}" if meta["session_id"] else "Untitled")
    )

    name_prefix = f"{date_str} {time_str}".strip()
    suggested_filename = (
        f"{name_prefix} — {_slugify(title)}.md" if name_prefix else f"{_slugify(title)}.md"
    )

    fm = ["---", f'title: "{title}"']
    if date_str:
        fm.append(f"date: {date_str}")
    fm.append("tags: [claude-code-chat]")
    if first_ts:
        fm.append(f"created: {first_ts.isoformat()}")
    if last_ts:
        fm.append(f"updated: {last_ts.isoformat()}")
    fm.append("source: claude-code-export")
    if meta["session_id"]:
        fm.append(f"uuid: {meta['session_id']}")
    fm.append(f"message_count: {len(turns)}")
    if meta["tools_used"]:
        fm.append(f"tools_used: [{', '.join(meta['tools_used'])}]")
    fm.append("---")
    fm.append("")
    fm.append(f"# {title}")
    fm.append("")

    body = []
    for t in turns:
        ts = _parse_ts(t["timestamp"])
        ts_str = ts.strftime("%Y-%m-%d %H:%M") if ts else ""
        speaker = "You" if t["role"] == "human" else "Claude"
        body.append(f"## {speaker} · {ts_str}" if ts_str else f"## {speaker}")
        body.append("")
        body.append(t["text"])
        body.append("")

    return "\n".join(fm + body), suggested_filename
