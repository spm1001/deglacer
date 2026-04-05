"""Tests for content extraction."""

from deglacer.content import extract_human_text, extract_assistant_content
from tests.conftest import (
    HUMAN_ENTRY, HUMAN_WITH_SYSTEM_TAGS, ASSISTANT_ENTRY_SECOND,
)


def test_extract_human_text_plain():
    assert extract_human_text(HUMAN_ENTRY) == "What is deglacer?"


def test_extract_human_text_strips_system_tags():
    text = extract_human_text(HUMAN_WITH_SYSTEM_TAGS)
    assert "system-reminder" not in text
    assert "command-name" not in text
    assert "Tell me about the kitchen metaphor" in text


def test_extract_assistant_text_only():
    text = extract_assistant_content(ASSISTANT_ENTRY_SECOND)
    assert text == "Here are the details."
    assert "thinking" not in text


def test_extract_assistant_with_thinking():
    text = extract_assistant_content(ASSISTANT_ENTRY_SECOND, with_thinking=True)
    assert "<thinking>" in text
    assert "Let me think about this" in text
    assert "Here are the details." in text


def test_extract_assistant_with_tools():
    entry = {
        "message": {
            "content": [
                {"type": "text", "text": "Running a command."},
                {
                    "type": "tool_use",
                    "id": "t1",
                    "name": "Bash",
                    "input": {"command": "echo hello"},
                },
                {
                    "type": "tool_use",
                    "id": "t2",
                    "name": "Read",
                    "input": {"file_path": "/tmp/foo.py"},
                },
                {
                    "type": "tool_use",
                    "id": "t3",
                    "name": "Write",
                    "input": {"file_path": "/tmp/bar.py"},
                },
                {
                    "type": "tool_use",
                    "id": "t4",
                    "name": "Agent",
                    "input": {"description": "search code"},
                },
                {
                    "type": "tool_use",
                    "id": "t5",
                    "name": "TaskCreate",
                    "input": {},
                },
            ]
        }
    }
    text = extract_assistant_content(entry, with_tools=True)
    assert "[tool: Bash] echo hello" in text
    assert "[tool: Read] /tmp/foo.py" in text
    assert "[tool: Write] /tmp/bar.py" in text
    assert "[tool: Agent] search code" in text
    assert "[tool: TaskCreate]" in text


def test_extract_assistant_without_tools():
    entry = {
        "message": {
            "content": [
                {"type": "text", "text": "Some text."},
                {"type": "tool_use", "id": "t1", "name": "Bash", "input": {"command": "ls"}},
            ]
        }
    }
    text = extract_assistant_content(entry, with_tools=False)
    assert text == "Some text."
    assert "tool" not in text
