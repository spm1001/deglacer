"""Tests for markdown export."""

from deglacer.markdown import format_markdown
from deglacer.content import extract_human_text
from tests.conftest import HUMAN_ENTRY, ASSISTANT_ENTRY_1, ASSISTANT_ENTRY_SECOND


def test_format_markdown_basic_shape(entries):
    md, filename = format_markdown(entries)
    assert md.startswith("---\n")
    assert "source: claude-code-export" in md
    assert "uuid: test-session-001" in md
    assert "# " in md
    assert "## You · " in md
    assert "## Claude · " in md


def test_format_markdown_uses_slug_for_title(entries):
    md, filename = format_markdown(entries)
    assert 'title: "Test Deglacer"' in md
    assert filename.endswith("Test Deglacer.md")


def test_format_markdown_falls_back_to_first_human_when_no_slug():
    entry = {**HUMAN_ENTRY, "slug": None}
    md, filename = format_markdown([entry, ASSISTANT_ENTRY_SECOND])
    assert 'title: "What is deglacer"' in md
    assert filename.endswith("What is deglacer.md")


def test_format_markdown_one_claude_bubble_per_human_turn(entries):
    """Two human turns separated by assistants → exactly two Claude bubbles."""
    md, _ = format_markdown(entries)
    assert md.count("## You · ") == 2
    assert md.count("## Claude · ") == 2


def test_format_markdown_coalesces_consecutive_assistants():
    """Multiple assistant message-ids between two human turns → one Claude bubble."""
    human1 = {
        "type": "user",
        "message": {"content": "go"},
        "timestamp": "2026-05-16T10:00:00Z",
        "sessionId": "s1",
    }
    asst_a = {
        "type": "assistant",
        "message": {"id": "m1", "content": [{"type": "text", "text": "first chunk"}]},
        "timestamp": "2026-05-16T10:00:05Z",
    }
    asst_b = {
        "type": "assistant",
        "message": {"id": "m2", "content": [{"type": "text", "text": "second chunk"}]},
        "timestamp": "2026-05-16T10:00:10Z",
    }
    asst_c = {
        "type": "assistant",
        "message": {"id": "m3", "content": [{"type": "text", "text": "third chunk"}]},
        "timestamp": "2026-05-16T10:00:15Z",
    }
    human2 = {
        "type": "user",
        "message": {"content": "thanks"},
        "timestamp": "2026-05-16T10:00:20Z",
        "sessionId": "s1",
    }
    md, _ = format_markdown([human1, asst_a, asst_b, asst_c, human2])
    assert md.count("## Claude · ") == 1
    assert md.count("## You · ") == 2
    for chunk in ("first chunk", "second chunk", "third chunk"):
        assert chunk in md


def test_format_markdown_renders_tool_calls_inline(entries):
    md, _ = format_markdown(entries)
    assert "📄 **Read:**" in md
    assert "/tmp/test.py" in md


def test_format_markdown_unwraps_command_args():
    """Regression: <command-args> content should appear unwrapped (no XML tags)."""
    entry = {
        "type": "user",
        "message": {
            "content": (
                "<command-message>foo</command-message>\n"
                "<command-name>/foo</command-name>\n"
                "<command-args>do the thing</command-args>"
            )
        },
        "timestamp": "2026-05-16T10:00:00Z",
        "sessionId": "s1",
    }
    md, _ = format_markdown([entry])
    assert "do the thing" in md
    assert "<command-args>" not in md
    assert "<command-message>" not in md


def test_format_markdown_skips_tool_results_and_meta(entries):
    md, _ = format_markdown(entries)
    # tool_result entries and isMeta entries shouldn't render as turns
    assert "file contents" not in md  # from TOOL_RESULT_ENTRY
    assert "Skill loaded: deglacer" not in md  # from META_ENTRY


def test_format_markdown_filename_format():
    entry = {
        "type": "user",
        "message": {"content": "test prompt"},
        "timestamp": "2026-05-16T13:41:07Z",
        "sessionId": "abc12345-def",
        "slug": "my-cool-session",
    }
    _, filename = format_markdown([entry])
    assert filename == "2026-05-16 1341 — My Cool Session.md"


def test_extract_human_unwraps_command_args():
    """Regression on content.py — direct test of the unwrap."""
    entry = {"message": {"content": "<command-args>hello world</command-args>"}}
    assert extract_human_text(entry) == "hello world"
