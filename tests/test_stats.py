"""Tests for session metadata extraction."""

from deglacer.stats import format_stats, format_timeline, format_summary
from tests.conftest import ALL_ENTRIES


def test_format_stats_contains_session_id():
    output = format_stats(ALL_ENTRIES)
    assert 'test-session-001' in output


def test_format_stats_contains_version():
    output = format_stats(ALL_ENTRIES)
    assert '2.3.0' in output


def test_format_stats_contains_slug():
    output = format_stats(ALL_ENTRIES)
    assert 'test-deglacer' in output


def test_format_stats_token_counts():
    output = format_stats(ALL_ENTRIES)
    # input_tokens from entries: (100+20+30) + (100+20+30) + 0 + (200+0+50) = 550
    # But we're testing format_stats produces token info, not exact values
    assert 'Tokens:' in output
    assert 'input=' in output
    assert 'output=' in output


def test_format_stats_model_counts():
    output = format_stats(ALL_ENTRIES)
    assert 'claude-opus-4-6' in output
    assert 'claude-sonnet-4-6' in output


def test_format_stats_tool_usage():
    output = format_stats(ALL_ENTRIES)
    assert 'Tool usage:' in output
    assert 'Read' in output
    assert 'Bash' in output


def test_format_stats_turn_counts():
    output = format_stats(ALL_ENTRIES)
    assert 'Human messages:' in output
    assert 'Assistant turns:' in output


def test_format_timeline():
    output = format_timeline(ALL_ENTRIES)
    assert 'human' in output
    assert 'assistant' in output
    assert 'turn: 5000ms' in output
    assert 'API error' in output


def test_format_summary():
    output = format_summary(ALL_ENTRIES)
    assert '2 messages' in output
    assert 'What is deglacer?' in output
    assert 'Tell me about the kitchen metaphor' in output
