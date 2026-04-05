"""Tests for conversation reconstruction."""

from deglacer.conversation import merge_assistant_entries, build_turns, format_text
from tests.conftest import ALL_ENTRIES


def test_merge_deduplicates_by_message_id():
    merged = merge_assistant_entries(ALL_ENTRIES)
    # msg-001 (3 raw entries) and msg-002 (1 entry) → 2 merged
    assert len(merged) == 2


def test_merge_preserves_order():
    merged = merge_assistant_entries(ALL_ENTRIES)
    assert merged[0]['message']['id'] == 'msg-001'
    assert merged[1]['message']['id'] == 'msg-002'


def test_merge_combines_content_blocks():
    merged = merge_assistant_entries(ALL_ENTRIES)
    msg001 = merged[0]
    content = msg001['message']['content']

    # Should have: 2 text blocks + 2 unique tool_use blocks (tool-001 deduped)
    text_blocks = [b for b in content if b.get('type') == 'text']
    tool_blocks = [b for b in content if b.get('type') == 'tool_use']

    assert len(text_blocks) == 2
    assert len(tool_blocks) == 2  # tool-001 + tool-002 (tool-001 duplicate removed)
    tool_ids = {b['id'] for b in tool_blocks}
    assert tool_ids == {'tool-001', 'tool-002'}


def test_build_turns_basic():
    turns = build_turns(ALL_ENTRIES)
    roles = [t['role'] for t in turns]
    assert 'human' in roles
    assert 'assistant' in roles
    assert 'system' in roles  # from summary entry


def test_build_turns_skips_noise():
    turns = build_turns(ALL_ENTRIES)
    # Should not include progress, tool_result, meta, or system entries as turns
    for turn in turns:
        assert turn['role'] in ('human', 'assistant', 'system')


def test_build_turns_human_count():
    turns = build_turns(ALL_ENTRIES)
    human_turns = [t for t in turns if t['role'] == 'human']
    assert len(human_turns) == 2


def test_build_turns_assistant_count():
    turns = build_turns(ALL_ENTRIES)
    assistant_turns = [t for t in turns if t['role'] == 'assistant']
    assert len(assistant_turns) == 2  # Two distinct message.ids


def test_build_turns_last_n():
    turns = build_turns(ALL_ENTRIES, last_n=2)
    assert len(turns) == 2


def test_build_turns_with_tools():
    turns = build_turns(ALL_ENTRIES, with_tools=True)
    assistant_turns = [t for t in turns if t['role'] == 'assistant']
    assistant_text = assistant_turns[0]['text']  # First assistant turn (msg-001)
    assert '[tool: Read]' in assistant_text


def test_build_turns_preserves_timestamps():
    turns = build_turns(ALL_ENTRIES)
    human_turn = next(t for t in turns if t['role'] == 'human')
    assert human_turn['timestamp'] is not None


def test_build_turns_preserves_model():
    turns = build_turns(ALL_ENTRIES)
    assistant_turns = [t for t in turns if t['role'] == 'assistant']
    assert assistant_turns[0]['model'] == 'claude-opus-4-6'
    assert assistant_turns[1]['model'] == 'claude-sonnet-4-6'


def test_format_text():
    turns = build_turns(ALL_ENTRIES)
    text = format_text(turns)
    assert '── HUMAN ──' in text
    assert '── ASSISTANT ──' in text
    assert 'What is deglacer?' in text
