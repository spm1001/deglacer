"""--entries / --meta: the forensic per-entry view (dgc-mahula).

Two layers. The synthetic layer proves the mechanics on the shared fixture and
on small purpose-built files: classification, pairing across lines, usage once
per request, ordering, filters, streaming release rules.

The oracle layer is the card's acceptance test and it is deliberately not
ours: for each question shape Claudes actually hand-rolled (A-M on the card,
programs in tools/jq-programs-2026-09-13.txt), the REAL jq program runs over
a REAL transcript and the deglacer+jq equivalent must print the same values.
jq generates the expected answer; the transcripts are chosen by rule from the
machine's own corpus (oldest and newest that clear a size and richness bar),
never committed — this repo is public. Skipped, and says so, where there is
no corpus or no jq.
"""

import json
import os
import shutil
import subprocess
import sys
from collections import Counter
from pathlib import Path

import pytest

from deglacer import entries as dge
from deglacer.parsing import parse_session, dedupe_by_request


def run_cli(*argv):
    return subprocess.run(
        [sys.executable, "-m", "deglacer.cli", *argv],
        capture_output=True, text=True,
    )


def rows_of(*argv):
    cp = run_cli("--entries", *argv)
    assert cp.returncode == 0, cp.stderr
    return [json.loads(line) for line in cp.stdout.splitlines()]


def _write(path: Path, entries: list[dict]) -> str:
    with open(path, "w") as f:
        for e in entries:
            f.write(json.dumps(e) + "\n")
    return str(path)


# -- classification on the shared fixture -------------------------------------

def test_every_line_is_accounted_for(session_file):
    rows = rows_of(session_file)
    raw_lines = [n for n, line in enumerate(Path(session_file).read_text().splitlines(), 1) if line.strip()]
    entry_rows = [r for r in rows if "type" in r]
    assert sorted({r["i"] for r in entry_rows}) == raw_lines
    assert [r["i"] for r in rows] == sorted(r["i"] for r in rows), "file order is kept"


def test_kind_census_matches_the_fixture(session_file):
    kinds = Counter(r["kind"] for r in rows_of(session_file))
    assert kinds == {
        "human": 2, "assistant": 4, "tool_use": 2, "tool_result": 1, "meta": 1,
        "summary": 1, "progress": 1, "system:turn_duration": 1, "system:api_error": 1,
    }


def test_human_text_is_stripped_and_meta_is_not_human(session_file):
    rows = rows_of(session_file, "--kind", "human")
    assert [r["text"] for r in rows] == ["What is deglacer?", "Tell me about the kitchen metaphor"]
    assert all(r["type"] == "user" for r in rows)


def test_unknown_types_pass_through_by_name(session_file):
    rows = rows_of(session_file)
    summary = next(r for r in rows if r["kind"] == "summary")
    assert summary["summary"].startswith("The conversation covered")
    assert summary["leafUuid"] == "uuid-123"
    progress = next(r for r in rows if r["kind"] == "progress")
    assert progress["data"]["type"] == "bash_progress"


def test_system_rows_keep_subtype_and_payload(session_file):
    err = rows_of(session_file, "--kind", "system:api_error")
    assert len(err) == 1 and err[0]["subtype"] == "api_error"
    assert err[0]["error"] == {"message": "rate limited"}
    both = rows_of(session_file, "--kind", "system")
    assert {r["kind"] for r in both} == {"system:turn_duration", "system:api_error"}


def test_streaming_dragon_dedupes_repeated_tool_use_blocks(session_file):
    """tool-001 appears in two entries of one message; one call, one row."""
    tools = rows_of(session_file, "--kind", "tool_use")
    assert [t["toolUseId"] for t in tools] == ["tool-001", "tool-002"]
    assert [t["name"] for t in tools] == ["Read", "Bash"]
    assert tools[1]["input"] == {"command": "ls -la"}


def test_usage_appears_once_per_request_on_the_max_output_entry(session_file):
    """Entries 1-3 share msg-001 (no requestId in the fixture, so message.id is
    the key). Only the highest-output entry carries usage, so a naive sum over
    --entries equals dedupe_by_request's answer rather than 2.3x it."""
    assistant = rows_of(session_file, "--kind", "assistant")
    with_usage = [r for r in assistant if "usage" in r]
    assert len(with_usage) == 2                       # msg-001 group + msg-002
    assert with_usage[0]["usage"]["output_tokens"] == 80
    expected = sum(e["message"]["usage"]["output_tokens"] for e in dedupe_by_request(parse_session(session_file)))
    assert sum(r["usage"]["output_tokens"] for r in with_usage) == expected


def test_thinking_and_text_split(session_file):
    second = next(r for r in rows_of(session_file, "--kind", "assistant") if r["messageId"] == "msg-002")
    assert second["text"] == "Here are the details."
    assert second["thinking"].startswith("Let me think")
    assert second["model"] == "claude-sonnet-4-6"


# -- pairing on a purpose-built file ------------------------------------------

PAIRED = [
    {"type": "user", "uuid": "u1", "sessionId": "s", "cwd": "/w", "timestamp": "2026-09-01T10:00:00Z",
     "message": {"content": "run it"}, "permissionMode": "default", "version": "2.1.270", "gitBranch": "main"},
    {"type": "assistant", "uuid": "a1", "sessionId": "s", "cwd": "/w", "requestId": "req_1",
     "timestamp": "2026-09-01T10:00:01Z",
     "message": {"id": "m1", "model": "claude-fable-5-1", "content": [{"type": "text", "text": "Running."}],
                 "usage": {"input_tokens": 1, "output_tokens": 5}}},
    {"type": "assistant", "uuid": "a2", "sessionId": "s", "cwd": "/w", "requestId": "req_1",
     "timestamp": "2026-09-01T10:00:02Z",
     "message": {"id": "m1", "model": "claude-fable-5-1", "stop_reason": "tool_use",
                 "content": [{"type": "tool_use", "id": "t1", "name": "Bash", "input": {"command": "false"}},
                             {"type": "tool_use", "id": "t2", "name": "Read", "input": {"file_path": "/x"}}],
                 "usage": {"input_tokens": 1, "output_tokens": 40}}},
    {"type": "user", "uuid": "r1", "sessionId": "s", "cwd": "/w", "timestamp": "2026-09-01T10:00:03Z",
     "message": {"content": [{"type": "tool_result", "tool_use_id": "t1", "is_error": True,
                              "content": "Exit code 1\n" + "x" * 500}]},
     "toolUseResult": "Error: Exit code 1", "sourceToolAssistantUUID": "a2"},
    {"type": "user", "uuid": "r2", "sessionId": "s", "cwd": "/w", "timestamp": "2026-09-01T10:00:04Z",
     "message": {"content": [{"type": "tool_result", "tool_use_id": "t2",
                              "content": [{"type": "text", "text": "file body"}]}]},
     "toolUseResult": {"file": {"filePath": "/x"}, "type": "text"}},
    {"type": "assistant", "uuid": "a3", "sessionId": "s", "cwd": "/w", "requestId": "req_2",
     "timestamp": "2026-09-01T10:00:05Z",
     "message": {"id": "m2", "model": "claude-fable-5-1",
                 "content": [{"type": "tool_use", "id": "t3", "name": "Bash", "input": {"command": "sleep 99"}}],
                 "usage": {"input_tokens": 1, "output_tokens": 7}}},
    {"type": "user", "uuid": "u2", "sessionId": "s", "cwd": "/w", "timestamp": "2026-09-01T10:01:00Z",
     "message": {"content": "never mind"}, "permissionMode": "default", "isCompactSummary": True},
    {"type": "attachment", "uuid": "at1", "sessionId": "s", "timestamp": "2026-09-01T10:01:01Z",
     "attachment": {"type": "hook_success", "hookEvent": "SessionStart", "stdout": "HANDOFF=x"}},
    {"type": "assistant", "uuid": "a4", "sessionId": "s", "cwd": "/w", "requestId": "req_3",
     "timestamp": "2026-09-01T10:01:02Z",
     "message": {"id": "m3", "model": "claude-fable-5-1",
                 "content": [{"type": "tool_use", "id": "t4", "name": "Bash", "input": {"command": "true"}}]}},
]


@pytest.fixture
def paired(tmp_path):
    return _write(tmp_path / "paired.jsonl", PAIRED)


def test_tool_use_rows_carry_their_results(paired):
    tools = {r["toolUseId"]: r for r in rows_of(paired, "--kind", "tool_use")}
    assert tools["t1"]["result"]["isError"] is True
    assert tools["t1"]["result"]["text"].startswith("Exit code 1")
    assert len(tools["t1"]["result"]["text"]) == dge.RESULT_PREVIEW_CHARS
    assert tools["t1"]["result"]["chars"] == len("Exit code 1\n") + 500
    assert tools["t1"]["result"]["uuid"] == "r1"
    assert tools["t2"]["result"] == {"isError": False, "text": "file body", "chars": 9, "uuid": "r2"}
    assert tools["t1"]["i"] == tools["t2"]["i"] == 3          # both from raw line 3


def test_tool_result_rows_know_their_tool_name_and_keep_full_text(paired):
    results = rows_of(paired, "--kind", "tool_result")
    by_id = {r["toolUseId"]: r for r in results}
    assert by_id["t1"]["name"] == "Bash" and by_id["t1"]["isError"] is True
    assert len(by_id["t1"]["text"]) == len("Exit code 1\n") + 500     # full, not the preview
    assert by_id["t1"]["toolUseResult"] == "Error: Exit code 1"       # raw passes through
    assert by_id["t2"]["name"] == "Read" and by_id["t2"]["text"] == "file body"


def test_unanswered_tool_use_is_released_by_the_next_human_turn_and_at_eof(paired):
    rows = rows_of(paired)
    t3 = next(r for r in rows if r.get("toolUseId") == "t3")
    t4 = next(r for r in rows if r.get("toolUseId") == "t4")
    assert t3["result"] is None and t4["result"] is None
    order = [r.get("toolUseId") or r["kind"] for r in rows]
    assert order.index("t3") < order.index("human") + 10           # t3 came out before the second human row
    assert order.index("t3") < order.index("attachment:hook_success")


def test_usage_lands_on_the_highest_output_entry_of_a_request(paired):
    assistant = rows_of(paired, "--kind", "assistant")
    req1 = [r for r in assistant if r["requestId"] == "req_1"]
    assert [("usage" in r) for r in req1] == [False, True]
    assert req1[1]["usage"]["output_tokens"] == 40
    assert "usage" not in next(r for r in assistant if r["requestId"] == "req_3")   # none in the raw either


def _usage(out):
    return {"input_tokens": 32, "cache_read_input_tokens": 1000, "output_tokens": out}


# Parallel tool calls, as CC 2.1.27x writes them: block 0's entry, then block
# 0's RESULT, then block 1's entry — one requestId, identical final usage.
INTERLEAVED = [
    {"type": "user", "uuid": "u1", "sessionId": "s", "timestamp": "2026-09-13T10:00:00Z", "message": {"content": "go"}},
    {"type": "assistant", "uuid": "a1", "sessionId": "s", "requestId": "req_p", "timestamp": "2026-09-13T10:00:01Z", "apiBlockIndex": 0,
     "message": {"id": "m1", "model": "claude-fable-5-1", "stop_reason": "tool_use", "usage": _usage(408),
                 "content": [{"type": "tool_use", "id": "p0", "name": "Bash", "input": {"command": "a"}}]}},
    {"type": "attachment", "uuid": "at", "sessionId": "s", "timestamp": "2026-09-13T10:00:02Z",
     "attachment": {"type": "bash_output_audience_note"}},
    {"type": "user", "uuid": "r0", "sessionId": "s", "timestamp": "2026-09-13T10:00:03Z",
     "message": {"content": [{"type": "tool_result", "tool_use_id": "p0", "content": "A"}]}, "toolUseResult": {"stdout": "A"}},
    {"type": "assistant", "uuid": "a2", "sessionId": "s", "requestId": "req_p", "timestamp": "2026-09-13T10:00:04Z", "apiBlockIndex": 1,
     "message": {"id": "m1", "model": "claude-fable-5-1", "stop_reason": "tool_use", "usage": _usage(408),
                 "content": [{"type": "tool_use", "id": "p1", "name": "Bash", "input": {"command": "b"}}]}},
    {"type": "user", "uuid": "r1", "sessionId": "s", "timestamp": "2026-09-13T10:00:05Z",
     "message": {"content": [{"type": "tool_result", "tool_use_id": "p1", "content": "B"}]}, "toolUseResult": {"stdout": "B"}},
    {"type": "assistant", "uuid": "a3", "sessionId": "s", "requestId": "req_q", "timestamp": "2026-09-13T10:00:06Z",
     "message": {"id": "m2", "model": "claude-fable-5-1", "stop_reason": "end_turn", "usage": _usage(9),
                 "content": [{"type": "text", "text": "done"}]}},
]


def test_parallel_tool_calls_split_by_their_results_are_one_request(tmp_path):
    path = _write(tmp_path / "interleaved.jsonl", INTERLEAVED)
    rows = rows_of(path)
    usage_rows = [r for r in rows if r.get("usage")]
    assert [r["requestId"] for r in usage_rows] == ["req_p", "req_q"]
    assert sum(r["usage"]["output_tokens"] for r in usage_rows) == 408 + 9
    assert sum(r["usage"]["output_tokens"] for r in usage_rows) == sum(
        e["message"]["usage"]["output_tokens"] for e in dedupe_by_request(parse_session(path)))
    tools = {r["toolUseId"]: r for r in rows if r["kind"] == "tool_use"}
    assert tools["p0"]["result"]["text"] == "A" and tools["p1"]["result"]["text"] == "B"
    assert [r["i"] for r in rows] == sorted(r["i"] for r in rows)   # order still the file's


def test_tool_filter_implies_the_two_tool_kinds(paired):
    bash = rows_of(paired, "--tool", "Bash")
    assert {r["kind"] for r in bash} == {"tool_use", "tool_result"}
    assert all(r["name"] == "Bash" for r in bash)
    assert [r["toolUseId"] for r in bash] == ["t1", "t1", "t3", "t4"]
    only_results = rows_of(paired, "--tool", "Read", "--kind", "tool_result")
    assert [r["toolUseId"] for r in only_results] == ["t2"]


def test_passthrough_keeps_raw_top_level_keys_flat(paired):
    human = rows_of(paired, "--kind", "human")
    assert human[0]["permissionMode"] == "default" and human[0]["version"] == "2.1.270"
    assert human[0]["gitBranch"] == "main"
    assert human[1]["isCompactSummary"] is True
    att = rows_of(paired, "--kind", "attachment")[0]
    assert att["attachment"]["stdout"] == "HANDOFF=x"
    assert "message" not in att and "message" not in human[0]


def test_tool_use_rows_have_no_raw_type(paired):
    """They are not entries; a census over `.type` must not count them."""
    rows = rows_of(paired)
    assert all("type" not in r for r in rows if r["kind"] == "tool_use")
    assert Counter(r["type"] for r in rows if "type" in r) == Counter(e["type"] for e in PAIRED)


def test_meta_block(paired):
    cp = run_cli("--meta", paired)
    assert cp.returncode == 0, cp.stderr
    meta = json.loads(cp.stdout)
    assert meta["sessionId"] == "s" and meta["cwd"] == "/w" and meta["gitBranch"] == "main"
    assert meta["versions"] == ["2.1.270"] and meta["models"] == ["claude-fable-5-1"]
    assert meta["firstTs"] == "2026-09-01T10:00:00Z" and meta["lastTs"] == "2026-09-01T10:01:02Z"
    assert meta["entries"] == len(PAIRED) and meta["requests"] == 3
    assert meta["tools"] == {"Bash": 3, "Read": 1}
    assert meta["kinds"]["tool_use"] == 4 and meta["kinds"]["attachment:hook_success"] == 1


def test_entries_survive_a_closed_pipe(paired):
    """`deglacer --entries F | head -1` must not traceback."""
    cp = subprocess.run(
        f"{sys.executable} -m deglacer.cli --entries {paired} | head -1",
        shell=True, capture_output=True, text=True,
    )
    assert "Traceback" not in cp.stderr
    assert cp.stdout.count("\n") == 1


def test_classify_vocabulary():
    assert dge.classify({"type": "user", "message": {"content": "hi"}}) == "human"
    assert dge.classify({"type": "user", "isMeta": True, "message": {"content": []}}) == "meta"
    assert dge.classify({"type": "user", "toolUseResult": {}, "message": {"content": []}}) == "tool_result"
    assert dge.classify({"type": "user", "message": {"content": [{"type": "image"}]}}) == "user"
    assert dge.classify({"type": "system"}) == "system:?"
    assert dge.classify({"type": "attachment", "attachment": {"type": "date"}}) == "attachment:date"
    assert dge.classify({"type": "atis-latch"}) == "atis-latch"
    assert dge.classify({}) == "untyped"


# -- the oracle: real jq programs over real transcripts -----------------------

JQ = shutil.which("jq")


def _jq(prog: str, path: str, slurp: bool = False) -> list[str]:
    argv = [JQ, "-r"] + (["-s"] if slurp else []) + [prog, path]
    cp = subprocess.run(argv, capture_output=True, text=True)
    assert cp.returncode == 0, cp.stderr
    return cp.stdout.splitlines()


def _census(path: str) -> Counter:
    return Counter(_jq(".type", path))


def _pick_fixtures() -> dict[str, str]:
    """Oldest and newest transcript on this machine that clear a richness bar.
    Chosen by rule, not by hand, so the worker did not choose the file."""
    base = Path.home() / ".claude" / "projects"
    if not base.is_dir():
        return {}
    candidates = []
    for p in base.glob("*/*.jsonl"):
        try:
            st = p.stat()
        except OSError:
            continue
        if 300_000 <= st.st_size <= 3_000_000:
            candidates.append((st.st_mtime, str(p)))
    candidates.sort()

    def rich(path, need_attachment):
        c = _census(path)
        return c["assistant"] >= 50 and c["user"] >= 20 and (c["attachment"] >= 1 or not need_attachment)

    picked = {}
    for _, path in candidates:
        if rich(path, need_attachment=False):
            picked["old"] = path
            break
    for _, path in reversed(candidates):
        if rich(path, need_attachment=True) and path != picked.get("old"):
            picked["new"] = path
            break
    return picked


FIXTURES = _pick_fixtures() if JQ else {}

# (shape, raw jq from the corpus, deglacer --entries + jq equivalent, comparison)
# comparison: exact = same values in the same order; multiset = same values any
# order (a census); set = same distinct values (session constants the corpus
# piped to sort -u / head -1 — tool_use rows repeat their entry's cwd/ts).
ORACLE = [
    ("A tool calls", 'select(.type=="assistant") | .message.content[]? | select(.type=="tool_use" and .name=="Bash") | .input.command',
     'select(.kind=="tool_use" and .name=="Bash") | .input.command', "exact"),
    ("A tool names", 'select(.type=="assistant") | .message.content[]? | select(.type=="tool_use") | .name',
     'select(.kind=="tool_use") | .name', "exact"),
    ("B cwd", 'select(.cwd) | .cwd', 'select(.cwd) | .cwd', "set"),
    ("B version", 'select(.version) | .version', 'select(.version) | .version', "set"),
    ("B timestamp", 'select(.timestamp) | .timestamp', 'select(.ts) | .ts', "set+ends"),
    ("B entrypoint", 'select(.entrypoint) | .entrypoint', 'select(.entrypoint) | .entrypoint', "set"),
    ("C type census", '.type', 'select(.type) | .type', "multiset"),
    ("D model", 'select(.type=="assistant") | .message.model', 'select(.kind=="assistant") | .model', "exact"),
    ("D requestId", 'select(.type=="assistant") | .requestId // empty', 'select(.kind=="assistant") | .requestId // empty', "exact"),
    ("D message.id", 'select(.type=="assistant") | .message.id', 'select(.kind=="assistant") | .messageId', "exact"),
    ("E system subtype", 'select(.type=="system") | .subtype', 'select(.kind | startswith("system:")) | .subtype', "exact"),
    ("F attachment type", 'select(.type=="attachment") | .attachment.type', 'select(.kind | startswith("attachment:")) | .attachment.type', "exact"),
    ("H permissionMode", 'select(.type == "user" and .permissionMode) | [.timestamp, .permissionMode] | @tsv',
     'select(.type == "user" and .permissionMode) | [.ts, .permissionMode] | @tsv', "exact"),
    ("I tool_result ids+errors", 'select(.type=="user") | .message.content[]? | select(.type=="tool_result") | [.tool_use_id, (.is_error // false)] | @tsv',
     'select(.kind=="tool_result") | [.toolUseId, .isError] | @tsv', "exact"),
    # K: a raw line's content is spread over its assistant row (text, thinking)
    # and its tool_use rows (input), so the union by `i` is the line — once the
    # paired RESULT is dropped, since that text comes from a later line. The
    # useful idiom keeps `.result` (a term inside a tool's output lights up the
    # call that produced it); the faithful one, asserted here, drops it.
    ("K term, by line", 'select(tostring | test(" the ")) | input_line_number',
     'del(.result) | select(tostring | test(" the ")) | .i', "set"),
    ("L compaction", 'select(.isCompactSummary==true) | .timestamp', 'select(.isCompactSummary==true) | .ts', "exact"),
    ("M ai-title", 'select(.type=="ai-title") | .aiTitle', 'select(.kind=="ai-title") | .aiTitle', "exact"),
]


@pytest.fixture(scope="module")
def entries_dump(tmp_path_factory):
    """--entries run once per real fixture; rows land in a file jq can read."""
    out = {}
    for label, path in FIXTURES.items():
        target = tmp_path_factory.mktemp("entries") / f"{label}.jsonl"
        cp = run_cli("--entries", path)
        assert cp.returncode == 0, cp.stderr
        target.write_text(cp.stdout)
        out[label] = str(target)
    return out


@pytest.mark.skipif(not JQ, reason="jq not installed — the oracle is jq's answer")
@pytest.mark.skipif(not FIXTURES, reason="no real transcripts under ~/.claude/projects clear the richness bar")
@pytest.mark.parametrize("label", sorted(FIXTURES))
@pytest.mark.parametrize("shape,raw,equiv,mode", ORACLE, ids=[o[0] for o in ORACLE])
def test_oracle_same_values_as_the_hand_rolled_jq(label, shape, raw, equiv, mode, entries_dump):
    path = FIXTURES[label]
    expected = _jq(raw, path)
    actual = _jq(equiv, entries_dump[label])
    if mode == "exact":
        assert actual == expected, f"{shape} on {label} ({path})"
    elif mode == "multiset":
        assert Counter(actual) == Counter(expected), f"{shape} on {label}"
    elif mode == "set" and shape.startswith("K"):
        # One designed divergence: a human row's `text` is extract_human_text,
        # which strips <system-reminder>/<task-notification>/<command-*> blocks
        # (as --summary does), so a term that lived only inside such a block
        # is absent from the row. Every raw-only line must be exactly that.
        assert set(actual) <= set(expected), f"{shape} on {label}: rows matched lines jq did not"
        residue = sorted(set(expected) - set(actual), key=int)
        raw_lines = Path(path).read_text().splitlines()
        for n in residue:
            e = json.loads(raw_lines[int(n) - 1])
            content = (e.get("message") or {}).get("content")
            assert dge.classify(e) == "human" and isinstance(content, str) and "</" in content, \
                f"{shape} on {label}: raw line {n} ({e.get('type')}) matched and no row did"
    elif mode == "set":
        assert set(actual) == set(expected), f"{shape} on {label}"
    elif mode == "set+ends":
        assert set(actual) == set(expected) and actual[:1] == expected[:1] and actual[-1:] == expected[-1:], f"{shape} on {label}"


@pytest.mark.skipif(not JQ, reason="jq not installed")
@pytest.mark.skipif(not FIXTURES, reason="no real transcripts clear the richness bar")
@pytest.mark.parametrize("label", sorted(FIXTURES))
def test_oracle_pairing_matches_a_slurped_jq_join(label, entries_dump):
    """Shape I proper: the pairing across lines. jq, slurping the whole file,
    builds tool_use_id -> is_error; every tool_use row's result must agree."""
    path = FIXTURES[label]
    expected = json.loads("\n".join(_jq(
        '[.[] | select(.type=="user") | .message.content[]? | select(.type=="tool_result") '
        '| {(.tool_use_id): (.is_error // false)}] | add // {}', path, slurp=True)))
    rows = [json.loads(l) for l in Path(entries_dump[label]).read_text().splitlines()]
    tools = [r for r in rows if r["kind"] == "tool_use"]
    assert tools, "a rich transcript has tool calls"
    paired = {t["toolUseId"]: t["result"]["isError"] for t in tools if t["result"] is not None}
    assert paired == {k: v for k, v in expected.items() if k in paired}
    assert set(expected) <= {t["toolUseId"] for t in tools}, "every result found its call"
    unpaired = [t["toolUseId"] for t in tools if t["result"] is None]
    assert set(unpaired).isdisjoint(expected), "a call with a result on disk was left unpaired"


@pytest.mark.skipif(not FIXTURES, reason="no real transcripts clear the richness bar")
@pytest.mark.parametrize("label", sorted(FIXTURES))
def test_oracle_usage_sum_equals_dedupe_by_request(label, entries_dump):
    """Shape G, against deglacer's own already-measured dedupe rather than the
    corpus jq — because the corpus jq is the 2.3x overcount."""
    rows = [json.loads(l) for l in Path(entries_dump[label]).read_text().splitlines()]
    ours = sum(r["usage"].get("output_tokens", 0) for r in rows if r.get("usage"))
    ref = sum((e["message"].get("usage") or {}).get("output_tokens", 0)
              for e in dedupe_by_request(parse_session(FIXTURES[label])))
    assert ours == ref


@pytest.mark.skipif(not JQ or not FIXTURES, reason="needs jq and a real transcript")
@pytest.mark.parametrize("label", sorted(FIXTURES))
def test_oracle_meta_agrees_with_jq(label):
    path = FIXTURES[label]
    cp = run_cli("--meta", path)
    meta = json.loads(cp.stdout)
    assert meta["sessionId"] == _jq('first(.[] | select(.sessionId) | .sessionId)', path, slurp=True)[0]
    assert sorted(meta["versions"]) == sorted(_jq('[.[] | .version | select(.)] | unique | .[]', path, slurp=True))
    assert meta["entries"] == int(_jq('length', path, slurp=True)[0])
    assert meta["firstTs"] == _jq('first(.[] | select(.timestamp) | .timestamp)', path, slurp=True)[0]
