"""Tests for tool-call labelling.

The wrapper-stripping cases are the point: 42.9% of real Bash calls start with
`cd`, so getting that wrong makes the single most common label meaningless.
Shapes here are taken from the measurement (2026-08-28, 250 sessions), not
invented.
"""

from deglacer.stats import format_stats
from deglacer.tools import bash_command, normalize_tool, tool_calls


# -- the case that motivates the module --------------------------------------

def test_leading_cd_is_stripped_to_the_real_command():
    """`cd X && foo` must label as foo. Unstripped, 42.9% of Bash calls on this
    estate collapse into one `cd` bucket that says nothing."""
    assert bash_command("cd /tmp && uv run --script x.py") == "uv"
    assert bash_command("cd ~/repos/spm1001/deglacer && git status") == "git"


def test_every_separator_seen_in_real_transcripts_is_handled():
    """Measured separators after a cd clause: && (1,131), newline (93), ; (6)."""
    assert bash_command("cd /tmp && grep foo") == "grep"
    assert bash_command("cd /tmp\ngrep foo") == "grep"
    assert bash_command("cd /tmp ; grep foo") == "grep"
    assert bash_command("cd /tmp || echo nope") == "echo"


def test_a_pipe_is_not_a_separator():
    """`grep x | head` is ONE pipeline and grep is the point of it. Treating a
    pipe as a wrapper boundary would relabel every pipeline by its last stage."""
    assert bash_command("grep foo bar.txt | head -20") == "grep"
    assert bash_command("cd /tmp && rg foo | wc -l") == "rg"


def test_a_bare_cd_is_its_own_command():
    """`cd /tmp` with nothing after it really is a directory change."""
    assert bash_command("cd /tmp") == "cd"


def test_nested_wrappers_unwind():
    assert bash_command("cd /tmp && sudo systemctl restart foo") == "systemctl"
    assert bash_command("cd /x && timeout 60 rg pattern") == "rg"


def test_inline_wrappers_consume_their_own_arguments():
    assert bash_command("timeout 300 python3 x.py") == "python3"
    assert bash_command("timeout -k 5 30 curl example.com") == "curl"
    assert bash_command("env FOO=1 python3 x.py") == "python3"
    assert bash_command("command grep pattern") == "grep"


def test_env_assignments_are_not_the_command():
    """`FOO=1 bar` — the assignment is environment, bar is the work."""
    assert bash_command("DEJA_CLAUDE_ROOT=/x deja search") == "deja"
    assert bash_command("cd /tmp && LC_ALL=C comm -13 a b") == "comm"


def test_command_substitution_labels_the_inner_command():
    """`VAR=$(cmd …)` is not an env prefix — the command is INSIDE it. Skipping
    the word produced labels like `-td` and `&&` on 23 real calls, found by
    sweeping 2,869 commands rather than by these tests."""
    assert bash_command("BON=$(ls -td ~/.claude/*/scripts | head -1) && echo x") == "ls"
    assert bash_command("arch=$(curl -s https://example.com/x.json)") == "curl"
    assert bash_command("qpid=$(pgrep -f 'name foo'); echo hi") == "pgrep"
    assert bash_command("f=$(ls -t *.json | head -1) && python3 -c x") == "ls"


def test_command_substitution_of_an_absolute_path_uses_the_basename():
    assert bash_command("O=$(/usr/bin/find . -name x)") == "find"


def test_a_standalone_assignment_steps_over_the_separator():
    """`cd X && VAR=/path && cmd` — the assignment is its own statement, so
    without stepping the separator the label becomes `&&` (23 real calls)."""
    assert bash_command("cd /repo && S=/tmp/scratch && uv run x.py") == "uv"
    assert bash_command("A=/x && B=/y && python3 z.py") == "python3"


def test_a_quoted_assignment_value_may_contain_spaces():
    """`F="Lantern UK - Documents/x.md"` split on whitespace labelled 7 real
    calls in one session as "UK"."""
    assert bash_command('cd /n\nF="a b/c.md"\necho "$F"') == "echo"
    assert bash_command("X='one two' python3 s.py") == "python3"


def test_a_path_invocation_labels_by_its_basename():
    """`/usr/bin/find` and `find` are one command. Version-pinned plugin paths
    make splitting them worse: the same script under bon 1.70.4 and 1.71.4
    read as two different tools in a real sweep."""
    assert bash_command("/usr/bin/find . -name x") == "find"
    assert bash_command("~/.claude/plugins/cache/bon/1.70.4/scripts/x.sh") == "x.sh"
    assert bash_command("cd /tmp && /home/modha/.local/bin/claude --version") == "claude"


def test_a_wrapper_being_run_rather_than_wrapping_is_the_command():
    """`env | grep foo` runs env and pipes it; nothing is being wrapped."""
    assert bash_command("env | grep -i vertex") == "env"
    assert bash_command("time > /tmp/x") == "time"


def test_a_bare_wrapper_with_no_target_labels_as_itself():
    assert bash_command("sudo") == "sudo"


def test_empty_and_whitespace_commands_are_none():
    assert bash_command("") is None
    assert bash_command("   ") is None
    assert bash_command(None) is None


def test_pathological_nesting_terminates():
    """A bounded unwind — degrade to a label rather than spin."""
    assert bash_command("sudo " * 40 + "ls") is not None


# -- label/detail split -------------------------------------------------------

def test_mcp_tools_name_server_and_tool():
    label, detail = normalize_tool("mcp__plugin_mise_mise__search", {})
    assert label == "MCP plugin_mise_mise/search"
    assert detail is None


def test_agent_carries_its_subagent_type():
    assert normalize_tool("Task", {"subagent_type": "Explore"})[0] == "Agent:Explore"
    assert normalize_tool("Agent", {})[0] == "Agent:unnamed"


def test_skill_carries_its_name():
    assert normalize_tool("Skill", {"skill": "deglacer"})[0] == "Skill:deglacer"


def test_file_tools_carry_the_path_as_detail():
    assert normalize_tool("Read", {"file_path": "/tmp/x.py"}) == ("Read", "/tmp/x.py")
    assert normalize_tool("Edit", {"file_path": "/a/b.md"}) == ("Edit", "/a/b.md")


def test_webfetch_carries_the_host_not_the_whole_url():
    label, detail = normalize_tool("WebFetch", {"url": "https://example.com/a/b?c=1"})
    assert (label, detail) == ("WebFetch", "example.com")


def test_bash_detail_is_the_stripped_command():
    assert normalize_tool("Bash", {"command": "cd /tmp && git log"}) == ("Bash", "git")


def test_an_unknown_tool_keeps_its_name_and_has_no_detail():
    assert normalize_tool("SomeFutureTool", {"x": 1}) == ("SomeFutureTool", None)


def test_a_missing_name_does_not_raise():
    assert normalize_tool(None, {}) == ("unknown", None)
    assert normalize_tool("Bash", None) == ("Bash", None)


def test_long_details_are_truncated():
    label, detail = normalize_tool("Read", {"file_path": "/" + "x" * 400})
    assert len(detail) <= 120 and detail.endswith("…")


# -- extraction over entries --------------------------------------------------

def _assistant(blocks, message_id="msg_1"):
    return {"type": "assistant",
            "message": {"id": message_id, "model": "claude-opus-5",
                        "content": blocks, "usage": {}}}


def _use(tool_id, name, inp):
    return {"type": "tool_use", "id": tool_id, "name": name, "input": inp}


def test_tool_calls_deduplicates_streamed_repeats():
    entries = [
        _assistant([_use("t1", "Bash", {"command": "cd /x && ls"})]),
        _assistant([_use("t1", "Bash", {"command": "cd /x && ls"})]),
        _assistant([_use("t2", "Read", {"file_path": "/a.md"})]),
    ]
    assert tool_calls(entries) == [("Bash", "ls"), ("Read", "/a.md")]


def test_tool_calls_keeps_id_less_blocks_distinct():
    """merge_assistant_entries drops entries with no message.id; collapsing
    id-less tool_use blocks would hide real calls."""
    entries = [_assistant([{"type": "tool_use", "name": "Bash",
                            "input": {"command": "ls"}}] * 2)]
    assert len(tool_calls(entries)) == 2


# -- rendering ----------------------------------------------------------------

def test_stats_labels_bash_calls_by_their_real_command():
    entries = [_assistant([_use(f"t{i}", "Bash", {"command": f"cd /tmp && grep p{i}"})],
                          f"msg_{i}") for i in range(3)]
    out = format_stats(entries)
    assert "Bash" in out
    assert "cd" not in out.split("Tool usage:")[1]


def test_details_are_off_by_default_and_on_with_the_flag():
    entries = [
        _assistant([_use("t1", "Bash", {"command": "cd /tmp && git log"})], "m1"),
        _assistant([_use("t2", "Read", {"file_path": "/notes/x.md"})], "m2"),
    ]
    assert "What those calls went to" not in format_stats(entries)
    detailed = format_stats(entries, with_details=True)
    assert "What those calls went to" in detailed
    assert "git" in detailed
    assert "/notes/x.md" in detailed
