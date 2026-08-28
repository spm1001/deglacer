"""Deglacer — Claude Code JSONL session parser.

Extracts structured conversation data from CC session files,
handling compaction, deduplication, and system tag stripping.
"""

from importlib.metadata import version as _version

__version__ = _version("deglacer")

from deglacer.parsing import (
    parse_session, is_human_message, is_tool_result, is_meta, billing_lane,
    dedupe_by_request,
)
from deglacer.health import new_health, assess, findings, format_doctor
from deglacer.content import extract_human_text, extract_assistant_content
from deglacer.conversation import merge_assistant_entries, build_turns, format_text, format_json
from deglacer.stats import format_stats, format_timeline, format_summary
from deglacer.discovery import find_sessions, search_sessions
from deglacer.markdown import format_markdown

__all__ = [
    "parse_session",
    "is_human_message",
    "is_tool_result",
    "is_meta",
    "billing_lane",
    "dedupe_by_request",
    "new_health",
    "assess",
    "findings",
    "format_doctor",
    "extract_human_text",
    "extract_assistant_content",
    "merge_assistant_entries",
    "build_turns",
    "format_stats",
    "format_timeline",
    "format_text",
    "format_json",
    "format_summary",
    "format_markdown",
    "find_sessions",
    "search_sessions",
]
