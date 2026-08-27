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


def _assistant(request_id, message_id, model="claude-opus-5"):
    return {"type": "assistant", "requestId": request_id,
            "message": {"id": message_id, "model": model, "content": [], "usage": {}}}


def test_stats_reports_the_billing_lane():
    output = format_stats([
        _assistant("req_vrtx_a", "msg_1"),
        _assistant("req_b", "msg_2"),
    ])
    assert 'Billing lane' in output
    assert 'vertex' in output
    assert 'other' in output


def test_stats_counts_requests_not_transcript_lines():
    """CC streams one response across several entries sharing a requestId.
    Counting entries would report three requests where one was made."""
    streamed = [_assistant("req_vrtx_same", "msg_1") for _ in range(3)]
    output = format_stats(streamed)
    lane_line = next(l for l in output.splitlines() if l.strip().startswith('vertex'))
    assert lane_line.split()[-1] == '1'


def test_stats_omits_the_lane_block_when_nothing_can_be_attributed():
    """A transcript of purely synthetic responses has no requestIds to read;
    printing an empty section would imply the lane was checked and found absent."""
    output = format_stats([
        {"type": "assistant",
         "message": {"id": "msg_1", "model": "<synthetic>", "content": [], "usage": {}}},
    ])
    assert 'Billing lane' not in output


def _streamed(request_id, usage, model="claude-opus-5", tool_id=None):
    content = []
    if tool_id:
        content.append({"type": "tool_use", "id": tool_id, "name": "Read",
                        "input": {"file_path": "/tmp/x"}})
    return {"type": "assistant", "requestId": request_id,
            "message": {"id": "msg_1", "model": model,
                        "content": content, "usage": usage}}


def _usage(inp, cache_w, cache_r, out):
    return {"input_tokens": inp, "cache_creation_input_tokens": cache_w,
            "cache_read_input_tokens": cache_r, "output_tokens": out}


def test_stats_tokens_count_each_request_once():
    """One API response is written as one entry per content block, each
    repeating the same usage object. Summing entries overcounted by 128% on a
    real session (492 entries, 204 requests)."""
    output = format_stats([
        _streamed("req_a", _usage(2, 1000, 50_000, 5)),
        _streamed("req_a", _usage(2, 1000, 50_000, 5)),
        _streamed("req_a", _usage(2, 1000, 50_000, 499)),
    ])
    # one request: input 2 + 1000 + 50,000 = 51,002; output 499 (the final one)
    assert 'input=51,002' in output
    assert 'output=499' in output


def test_stats_takes_the_whole_usage_row_from_the_max_output_entry():
    """In the rare group where input and cache move too, they move together
    with output — so a max output spliced onto another entry's cache figures
    would undercount. Take the row, not the field."""
    output = format_stats([
        _streamed("req_a", _usage(2, 4406, 123_447, 3)),
        _streamed("req_a", _usage(4, 8907, 251_300, 3262)),
    ])
    assert 'input=260,211' in output      # 4 + 8,907 + 251,300
    assert 'output=3,262' in output


def test_stats_counts_models_per_request_not_per_entry():
    output = format_stats([_streamed("req_a", _usage(1, 0, 0, 1)) for _ in range(3)])
    model_line = next(l for l in output.splitlines()
                      if l.strip().startswith('claude-opus-5'))
    assert model_line.split()[-1] == '1'


def test_stats_counts_a_streamed_tool_call_once():
    output = format_stats([
        _streamed("req_a", _usage(1, 0, 0, 1), tool_id="toolu_1"),
        _streamed("req_a", _usage(1, 0, 0, 9), tool_id="toolu_1"),
    ])
    tool_line = next(l for l in output.splitlines() if l.strip().startswith('Read'))
    assert tool_line.split()[-1] == '1'


def test_stats_keeps_requests_without_a_request_id_distinct():
    """Synthetic and local responses carry no requestId. Collapsing them would
    report one request where several were recorded."""
    entries = [
        {"type": "assistant",
         "message": {"id": f"msg_{i}", "model": "<synthetic>",
                     "content": [], "usage": _usage(0, 0, 0, 7)}}
        for i in range(3)
    ]
    assert 'output=21' in format_stats(entries)


def test_stats_lane_and_model_are_independent():
    """The same model id appears on both lanes — that is why requestId is the handle."""
    output = format_stats([
        _assistant("req_vrtx_a", "msg_1", model="claude-opus-5"),
        _assistant("req_b", "msg_2", model="claude-opus-5"),
    ])
    assert 'claude-opus-5' in output
    assert 'vertex' in output and 'other' in output
