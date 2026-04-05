"""Test fixtures for deglacer."""

import json
import tempfile
from pathlib import Path

import pytest


def _make_jsonl(entries: list[dict]) -> Path:
    """Write entries to a temp JSONL file and return its path."""
    f = tempfile.NamedTemporaryFile(mode='w', suffix='.jsonl', delete=False)
    for entry in entries:
        f.write(json.dumps(entry) + '\n')
    f.close()
    return Path(f.name)


# -- Synthetic session entries covering all parsing paths --

HUMAN_ENTRY = {
    "type": "user",
    "message": {"content": "What is deglacer?"},
    "timestamp": "2026-04-05T09:00:00Z",
    "sessionId": "test-session-001",
    "version": "2.3.0",
    "slug": "test-deglacer",
}

HUMAN_WITH_SYSTEM_TAGS = {
    "type": "user",
    "message": {
        "content": (
            '<system-reminder>You are helpful</system-reminder>'
            'Tell me about the kitchen metaphor'
            '<command-name>test</command-name>'
        )
    },
    "timestamp": "2026-04-05T09:05:00Z",
    "sessionId": "test-session-001",
}

# Two assistant entries sharing the same message.id (streaming dragon)
ASSISTANT_ENTRY_1 = {
    "type": "assistant",
    "message": {
        "id": "msg-001",
        "model": "claude-opus-4-6",
        "content": [
            {"type": "text", "text": "Deglacer is a library for parsing"},
        ],
        "usage": {
            "input_tokens": 100,
            "output_tokens": 50,
            "cache_creation_input_tokens": 20,
            "cache_read_input_tokens": 30,
        },
    },
    "timestamp": "2026-04-05T09:00:05Z",
    "sessionId": "test-session-001",
}

ASSISTANT_ENTRY_2 = {
    "type": "assistant",
    "message": {
        "id": "msg-001",  # Same message.id — streaming continuation
        "model": "claude-opus-4-6",
        "content": [
            {"type": "text", "text": " Claude Code session JSONL files."},
            {
                "type": "tool_use",
                "id": "tool-001",
                "name": "Read",
                "input": {"file_path": "/tmp/test.py"},
            },
        ],
        "usage": {
            "input_tokens": 100,
            "output_tokens": 80,
            "cache_creation_input_tokens": 20,
            "cache_read_input_tokens": 30,
        },
    },
    "timestamp": "2026-04-05T09:00:10Z",
    "sessionId": "test-session-001",
}

# Duplicate tool_use block (should be deduplicated)
ASSISTANT_ENTRY_3 = {
    "type": "assistant",
    "message": {
        "id": "msg-001",
        "model": "claude-opus-4-6",
        "content": [
            {
                "type": "tool_use",
                "id": "tool-001",  # Same tool id — duplicate
                "name": "Read",
                "input": {"file_path": "/tmp/test.py"},
            },
            {
                "type": "tool_use",
                "id": "tool-002",
                "name": "Bash",
                "input": {"command": "ls -la"},
            },
        ],
        "usage": {"input_tokens": 0, "output_tokens": 0},
    },
    "timestamp": "2026-04-05T09:00:15Z",
    "sessionId": "test-session-001",
}

TOOL_RESULT_ENTRY = {
    "type": "user",
    "toolUseResult": {"stdout": "file contents", "stderr": ""},
    "message": {
        "content": [{"type": "tool_result", "content": "file contents"}]
    },
    "timestamp": "2026-04-05T09:00:20Z",
}

META_ENTRY = {
    "type": "user",
    "isMeta": True,
    "message": {
        "content": [{"type": "text", "text": "Skill loaded: deglacer"}]
    },
    "timestamp": "2026-04-05T09:00:25Z",
}

SUMMARY_ENTRY = {
    "type": "summary",
    "summary": "The conversation covered deglacer library design.",
    "leafUuid": "uuid-123",
}

PROGRESS_ENTRY = {
    "type": "progress",
    "data": {"type": "bash_progress", "content": "running..."},
}

SYSTEM_TURN_DURATION = {
    "type": "system",
    "subtype": "turn_duration",
    "durationMs": 5000,
    "timestamp": "2026-04-05T09:01:00Z",
}

SYSTEM_API_ERROR = {
    "type": "system",
    "subtype": "api_error",
    "error": {"message": "rate limited"},
    "timestamp": "2026-04-05T09:01:05Z",
}

# A second distinct assistant turn
ASSISTANT_ENTRY_SECOND = {
    "type": "assistant",
    "message": {
        "id": "msg-002",
        "model": "claude-sonnet-4-6",
        "content": [
            {"type": "text", "text": "Here are the details."},
            {"type": "thinking", "thinking": "Let me think about this..."},
        ],
        "usage": {
            "input_tokens": 200,
            "output_tokens": 100,
            "cache_creation_input_tokens": 0,
            "cache_read_input_tokens": 50,
        },
    },
    "timestamp": "2026-04-05T09:05:10Z",
    "sessionId": "test-session-001",
}

ALL_ENTRIES = [
    HUMAN_ENTRY,
    ASSISTANT_ENTRY_1,
    ASSISTANT_ENTRY_2,
    ASSISTANT_ENTRY_3,
    TOOL_RESULT_ENTRY,
    META_ENTRY,
    HUMAN_WITH_SYSTEM_TAGS,
    ASSISTANT_ENTRY_SECOND,
    SUMMARY_ENTRY,
    PROGRESS_ENTRY,
    SYSTEM_TURN_DURATION,
    SYSTEM_API_ERROR,
]


@pytest.fixture
def session_file(tmp_path):
    """Write the full synthetic session to a temp JSONL file."""
    path = tmp_path / "session.jsonl"
    with open(path, 'w') as f:
        for entry in ALL_ENTRIES:
            f.write(json.dumps(entry) + '\n')
    return str(path)


@pytest.fixture
def entries():
    """Return the raw entry list."""
    return [dict(e) for e in ALL_ENTRIES]  # shallow copy
