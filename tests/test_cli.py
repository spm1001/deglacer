"""Smoke tests for the deglacer CLI (bds-devece).

The parsing tests prove correctness; these prove the plumbing — argparse
wiring, exit codes, output routing — which could otherwise break silently.
Each test invokes the CLI as a real subprocess, exactly as a shell would.
Discovery tests point HOME at a temp dir so they never touch the real
~/.claude/projects.
"""

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest


def run_cli(*argv, home=None):
    env = dict(os.environ)
    if home is not None:
        env["HOME"] = str(home)
    return subprocess.run(
        [sys.executable, "-m", "deglacer.cli", *argv],
        capture_output=True, text=True, env=env,
    )


@pytest.fixture
def fake_home(tmp_path, session_file):
    """A HOME whose ~/.claude/projects holds one real session file."""
    proj = tmp_path / "home" / ".claude" / "projects" / "-tmp-testproj"
    proj.mkdir(parents=True)
    shutil.copy(session_file, proj / "abc123.jsonl")
    return tmp_path / "home"


# -- Output modes against the synthetic session --

def test_text_default(session_file):
    cp = run_cli(session_file)
    assert cp.returncode == 0
    assert "What is deglacer?" in cp.stdout


def test_json_output(session_file):
    cp = run_cli("--json", session_file)
    assert cp.returncode == 0
    parsed = json.loads(cp.stdout)
    turns = parsed if isinstance(parsed, list) else parsed.get("turns")
    assert turns


def test_json_last_n_limits_turns(session_file):
    all_cp = run_cli("--json", session_file)
    one_cp = run_cli("--json", "--last", "1", session_file)
    assert one_cp.returncode == 0

    def count(cp):
        parsed = json.loads(cp.stdout)
        turns = parsed if isinstance(parsed, list) else parsed.get("turns")
        return len(turns)

    assert count(one_cp) == 1
    assert count(all_cp) > 1


def test_stats(session_file):
    cp = run_cli("--stats", session_file)
    assert cp.returncode == 0
    assert cp.stdout.strip()


def test_summary(session_file):
    cp = run_cli("--summary", session_file)
    assert cp.returncode == 0
    assert "What is deglacer?" in cp.stdout


def test_timeline(session_file):
    cp = run_cli("--timeline", session_file)
    assert cp.returncode == 0
    assert cp.stdout.strip()


def test_markdown(session_file):
    cp = run_cli("--markdown", session_file)
    assert cp.returncode == 0
    assert cp.stdout.strip()
    assert "Suggested filename:" in cp.stderr


def test_with_tools_adds_tool_content(session_file):
    plain = run_cli(session_file)
    tools = run_cli("--with-tools", session_file)
    assert tools.returncode == 0
    assert tools.stdout != plain.stdout


def test_with_thinking_adds_thinking(session_file):
    plain = run_cli(session_file)
    thinking = run_cli("--with-thinking", session_file)
    assert thinking.returncode == 0
    assert "Let me think about this" in thinking.stdout
    assert "Let me think about this" not in plain.stdout


# -- Error cases --

def test_help_exits_zero():
    cp = run_cli("--help")
    assert cp.returncode == 0
    assert "usage" in cp.stdout.lower()


def test_no_args_prints_help_and_fails():
    cp = run_cli()
    assert cp.returncode == 1
    assert "usage" in cp.stdout.lower()


def test_missing_file():
    cp = run_cli("/nonexistent/nope.jsonl")
    assert cp.returncode == 1
    assert "File not found" in cp.stderr


def test_empty_file(tmp_path):
    empty = tmp_path / "empty.jsonl"
    empty.touch()
    cp = run_cli(str(empty))
    assert cp.returncode == 1
    assert "Empty or unparseable" in cp.stderr


# -- Discovery (hermetic via HOME override) --

def test_recent_lists_session(fake_home):
    cp = run_cli("--recent", home=fake_home)
    assert cp.returncode == 0
    assert "abc123.jsonl" in cp.stdout


def test_recent_empty_projects(tmp_path):
    (tmp_path / ".claude" / "projects").mkdir(parents=True)
    cp = run_cli("--recent", home=tmp_path)
    assert cp.returncode == 0
    assert cp.stdout.strip() == ""


def test_today_lists_fresh_session(fake_home):
    cp = run_cli("--today", home=fake_home)
    assert cp.returncode == 0
    assert "abc123.jsonl" in cp.stdout


def test_since_lists_session(fake_home):
    cp = run_cli("--since", "2020-01-01", home=fake_home)
    assert cp.returncode == 0
    assert "abc123.jsonl" in cp.stdout


def test_find_match(fake_home):
    cp = run_cli("--find", "deglacer", home=fake_home)
    assert cp.returncode == 0
    assert "abc123.jsonl" in cp.stdout


def test_find_no_match_exits_one(fake_home):
    cp = run_cli("--find", "xyzzy-not-in-any-session", home=fake_home)
    assert cp.returncode == 1
    assert "No matches" in cp.stderr
