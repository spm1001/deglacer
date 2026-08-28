"""Tests for parse-health assessment.

The point of these counters is to go RED when the CC format drifts, so most of
what is worth testing is the alarm firing — a health check that has only ever
been seen passing has not been tested.
"""

import json

from deglacer.health import (
    MIN_ASSISTANT_ENTRIES_FOR_DUPE_CHECK, assess, findings, format_doctor,
    new_health,
)
from deglacer.parsing import parse_session


def _assistant(request_id, message_id="msg_1", out=1):
    return {
        "type": "assistant",
        "requestId": request_id,
        "message": {
            "id": message_id,
            "model": "claude-opus-5",
            "content": [],
            "usage": {
                "input_tokens": 2,
                "cache_creation_input_tokens": 100,
                "cache_read_input_tokens": 500,
                "output_tokens": out,
            },
        },
    }


def _streamed_corpus(n_requests=12, entries_per_request=3):
    """A healthy transcript: every response written as several entries."""
    entries = []
    for i in range(n_requests):
        for k in range(entries_per_request):
            entries.append(_assistant(f"req_{i}", f"msg_{i}", out=k + 1))
    return entries


def _flags(h):
    return [f for f in findings(h) if f[0] == "flag"]


# -- the load-bearing alarm ---------------------------------------------------

def test_zero_duplicates_on_a_real_sized_corpus_raises_a_flag():
    """The dgc-tenoze tripwire. If collapsing by requestId removes nothing on a
    transcript this size, the key has stopped matching and every total is
    inflated — the failure that survived seven weeks unnoticed."""
    entries = [_assistant(f"req_{i}", f"msg_{i}") for i in range(20)]
    h = assess(entries)
    assert h["duplicates_collapsed"] == 0
    flags = _flags(h)
    assert any("dedupe key" in f[1] for f in flags)


def test_renaming_the_request_id_field_fires_the_alarm():
    """The brief's own acceptance case: simulate CC renaming requestId. Entries
    then fall back to message.id, which is per-response too, so a corpus that
    WAS collapsing stops — and the check must notice."""
    healthy = _streamed_corpus()
    assert assess(healthy)["duplicates_collapsed"] > 0, "control: healthy first"

    drifted = []
    for i, entry in enumerate(healthy):
        broken = dict(entry)
        broken["requestIdentifier"] = broken.pop("requestId")   # renamed upstream
        broken["message"] = {**broken["message"], "id": f"msg_unique_{i}"}
        drifted.append(broken)

    h = assess(drifted)
    assert h["duplicates_collapsed"] == 0
    assert any("dedupe key" in f[1] for f in _flags(h))


def test_a_healthy_streamed_corpus_raises_no_flags():
    h = assess(_streamed_corpus())
    assert h["api_requests"] == 12
    assert h["duplicates_collapsed"] == 24
    assert _flags(h) == []


def test_short_transcripts_do_not_trip_the_dupe_alarm():
    """A handful of entries can honestly contain no streamed response; firing
    there would train the reader to ignore the alarm."""
    entries = [_assistant(f"req_{i}", f"msg_{i}")
               for i in range(MIN_ASSISTANT_ENTRIES_FOR_DUPE_CHECK - 1)]
    h = assess(entries)
    assert h["duplicates_collapsed"] == 0
    assert _flags(h) == []
    assert any("too few" in f[1] for f in findings(h))


# -- line-level counters ------------------------------------------------------

def test_bad_json_lines_are_counted_and_flagged(tmp_path):
    path = tmp_path / "bad.jsonl"
    path.write_text('{"type":"user"}\nnot json\n{"type":"assistant"}\nalso not\n')
    health = new_health()
    entries = parse_session(str(path), health=health)
    assert len(entries) == 2
    h = assess(entries, health)
    assert h["lines"] == 4
    assert h["bad_json"] == 2
    assert any("not valid JSON" in f[1] for f in _flags(h))


def test_non_object_lines_are_counted_separately(tmp_path):
    """A bare array parses fine as JSON but is not a CC entry — a different
    drift signal from a truncated write, so it gets its own counter."""
    path = tmp_path / "arr.jsonl"
    path.write_text('{"type":"user"}\n[1,2,3]\n"a string"\n')
    health = new_health()
    entries = parse_session(str(path), health=health)
    assert len(entries) == 1
    h = assess(entries, health)
    assert h["not_object"] == 2
    assert any("not JSON objects" in f[1] for f in _flags(h))


def test_blank_lines_are_counted_but_never_flagged(tmp_path):
    path = tmp_path / "sparse.jsonl"
    path.write_text('{"type":"user"}\n\n\n{"type":"assistant"}\n')
    health = new_health()
    parse_session(str(path), health=health)
    h = assess([], health)
    assert h["blank"] == 2
    assert not any("blank" in f[1].lower() for f in _flags(h))


def test_parse_session_still_works_without_a_health_counter(tmp_path):
    """health is optional — the return type must not change."""
    path = tmp_path / "s.jsonl"
    path.write_text('{"type":"user"}\nnot json\n')
    assert parse_session(str(path)) == [{"type": "user"}]


# -- empty and degenerate inputs ---------------------------------------------

def test_an_empty_file_is_diagnosed_rather_than_silently_zero():
    h = assess([], new_health())
    assert any("No entries parsed" in f[1] for f in _flags(h))


def test_a_transcript_with_no_assistant_entries_is_flagged():
    h = assess([{"type": "user", "message": {"content": "hi"}}] * 3)
    assert any("No assistant entries" in f[1] for f in _flags(h))


def test_the_no_assistant_flag_names_what_was_there_instead():
    """Swept across 120 real sessions, this flag fired 8 times and every hit
    was a true positive — but two were workflow journal.jsonl files, which are
    a different format, not drift. The detail has to let the reader tell those
    apart without opening the file."""
    journal = [{"type": "started"}] * 7 + [{"type": "result"}] * 7
    row = next(f for f in _flags(assess(journal)) if "No assistant" in f[1])
    assert "started" in row[2] and "result" in row[2]
    assert "workflow" in row[2]


def test_missing_request_ids_are_reported_but_not_flagged():
    """Synthetic responses carry no requestId. Expected, not drift."""
    entries = _streamed_corpus()
    entries += [{"type": "assistant",
                 "message": {"id": f"syn_{i}", "model": "<synthetic>",
                             "content": [], "usage": {}}}
                for i in range(3)]
    h = assess(entries)
    assert h["no_request_id"] == 3
    assert h["zero_usage_requests"] == 3
    assert _flags(h) == []


def test_mostly_missing_request_ids_is_a_flag_not_reassurance():
    """Caught on a real drift run: renaming requestId made every entry
    ID-less, and the row read 'Expected: synthetic responses' directly beneath
    the flag saying the dedupe key had broken. A reassuring row beside an alarm
    about the same cause is worse than no row."""
    entries = [{"type": "assistant",
                "message": {"id": f"msg_{i}", "model": "claude-opus-5",
                            "content": [], "usage": {"output_tokens": 1}}}
               for i in range(20)]
    h = assess(entries)
    assert h["no_request_id"] == 20
    flags = _flags(h)
    assert any("carry no requestId" in f[1] for f in flags)
    assert not any(
        f[0] == "ok" and "carry no requestId" in f[1] for f in findings(h)
    )


# -- rendering ----------------------------------------------------------------

def test_format_doctor_shows_counts_types_and_findings():
    out = format_doctor(assess(_streamed_corpus()))
    assert "Parse health" in out
    assert "duplicates collapsed" in out
    assert "Entry types:" in out
    assert "assistant" in out
    assert "[ok]" in out


def test_format_doctor_puts_flags_before_ok_rows():
    entries = [_assistant(f"req_{i}", f"msg_{i}") for i in range(20)]
    out = format_doctor(assess(entries))
    assert "[!]" in out
    assert out.index("[!]") < out.index("[ok]") if "[ok]" in out else True


def test_the_dupe_ok_row_quantifies_the_overcount_avoided():
    """The healthy row is evidence, not filler: it names what summing raw
    entries would have cost."""
    row = next(f for f in findings(assess(_streamed_corpus()))
               if "collapsed" in f[1])
    assert "200%" in row[2]        # 36 entries against 12 requests
