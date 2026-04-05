"""Tests for JSONL parsing and entry classification."""

from deglacer.parsing import parse_session, is_human_message, is_tool_result, is_meta
from tests.conftest import (
    HUMAN_ENTRY, HUMAN_WITH_SYSTEM_TAGS, ASSISTANT_ENTRY_1,
    TOOL_RESULT_ENTRY, META_ENTRY, PROGRESS_ENTRY, SUMMARY_ENTRY,
)


def test_parse_session_loads_all_entries(session_file):
    entries = parse_session(session_file)
    assert len(entries) == 12


def test_parse_session_skips_blank_lines(tmp_path):
    path = tmp_path / "sparse.jsonl"
    path.write_text('{"type": "user"}\n\n\n{"type": "assistant"}\n')
    entries = parse_session(str(path))
    assert len(entries) == 2


def test_parse_session_skips_malformed_json(tmp_path):
    path = tmp_path / "bad.jsonl"
    path.write_text('{"valid": true}\nnot json\n{"also": "valid"}\n')
    entries = parse_session(str(path))
    assert len(entries) == 2


def test_is_human_message():
    assert is_human_message(HUMAN_ENTRY) is True
    assert is_human_message(HUMAN_WITH_SYSTEM_TAGS) is True


def test_is_human_rejects_tool_result():
    assert is_human_message(TOOL_RESULT_ENTRY) is False


def test_is_human_rejects_meta():
    assert is_human_message(META_ENTRY) is False


def test_is_human_rejects_assistant():
    assert is_human_message(ASSISTANT_ENTRY_1) is False


def test_is_tool_result():
    assert is_tool_result(TOOL_RESULT_ENTRY) is True
    assert is_tool_result(HUMAN_ENTRY) is False
    assert is_tool_result(ASSISTANT_ENTRY_1) is False


def test_is_meta():
    assert is_meta(META_ENTRY) is True
    assert is_meta(HUMAN_ENTRY) is False
    assert is_meta(TOOL_RESULT_ENTRY) is False
