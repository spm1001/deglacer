"""Invocation-log adoption tests (erg-tebapi).

deglacer vendors the estate invocation-log shim as src/deglacer/_invlog.py —
canonical copy and the cross-estate conformance test live in
spm1001/harness-ergonomics (shim/invocation_log.py, tests/test_conformance.py).
These tests pin the adoption facts locally: every invocation appends exactly
one caller-stamped JSONL line — success and failure alike — and a broken log
path never breaks the CLI.

deglacer has no subcommands, so the log's subcommand field carries a mode
derived in dispatch order (recent/since/find/stats/summary/timeline/markdown/
json/text) — the derivation is the deglacer-specific part of the adoption and
gets its own assertions here.
"""

import json
import os
import subprocess
import sys


def _run(*argv, env):
    return subprocess.run(
        [sys.executable, "-m", "deglacer.cli", *argv],
        capture_output=True, text=True, env=env,
        stdin=subprocess.DEVNULL,
    )


def _env(tmp_path, **overrides):
    """Env with a hermetic log dir and a deterministic model caller stamp."""
    env = dict(os.environ)
    env.pop("CLAUDECODE", None)
    env.pop("CLAUDE_CODE_ENTRYPOINT", None)
    env["XDG_DATA_HOME"] = str(tmp_path / "xdg")
    env.update(overrides)
    return env


def _log_lines(tmp_path):
    log = tmp_path / "xdg" / "deglacer" / "invocations.jsonl"
    assert log.exists(), f"no invocation log at {log}"
    return [json.loads(l) for l in log.read_text().splitlines() if l.strip()]


class TestInvocationLog:
    def test_ok_invocation_logs_one_line(self, session_file, tmp_path):
        env = _env(tmp_path, CLAUDECODE="1", CLAUDE_CODE_ENTRYPOINT="cli")
        result = _run("--stats", str(session_file), env=env)
        assert result.returncode == 0, result.stderr
        (line,) = _log_lines(tmp_path)
        assert line["tool"] == "deglacer"
        assert line["subcommand"] == "stats"
        assert line["argv"] == ["--stats", str(session_file)]
        assert line["parsed"]["stats"] is True
        assert line["outcome"] == "ok" and line["exit_code"] == 0
        assert line["caller"] == "model" and line["caller_detail"] == "cli"
        assert line["duration_ms"] >= 0
        assert line["version"]  # whatever the CLI reports, non-empty

    def test_missing_file_error_logged_with_parsed_args(self, tmp_path):
        env = _env(tmp_path, CLAUDECODE="1", CLAUDE_CODE_ENTRYPOINT="cli")
        result = _run("/nonexistent/session.jsonl", env=env)
        assert result.returncode == 1
        (line,) = _log_lines(tmp_path)
        assert line["outcome"] == "error" and line["exit_code"] == 1
        assert line["subcommand"] == "text"  # default extraction mode
        assert line["parsed"]["file"] == "/nonexistent/session.jsonl"

    def test_misinvocation_dies_in_argparse_still_logged(self, tmp_path):
        """An invented flag never reaches post-parse — raw argv is the
        evidence."""
        env = _env(tmp_path, CLAUDECODE="1")
        result = _run("--definitely-not-a-flag", env=env)
        assert result.returncode == 2
        (line,) = _log_lines(tmp_path)
        assert line["outcome"] == "error" and line["exit_code"] == 2
        assert line["argv"] == ["--definitely-not-a-flag"]
        assert line["subcommand"] is None and line["parsed"] is None

    def test_mode_derivation_matches_dispatch(self, session_file, tmp_path):
        """One probe per mode family the dispatch distinguishes."""
        env = _env(tmp_path, CLAUDECODE="1", HOME=str(tmp_path / "home"))
        for argv, mode in [
            (["--recent"], "recent"),
            (["--today"], "recent"),          # sugar sets recent=100
            (["--find", "zz-no-match"], "find"),
            (["--summary", str(session_file)], "summary"),
            (["--json", str(session_file)], "json"),
            ([str(session_file)], "text"),
        ]:
            _run(*argv, env=env)
        modes = [l["subcommand"] for l in _log_lines(tmp_path)]
        assert modes == ["recent", "recent", "find", "summary", "json", "text"]

    def test_robot_stamp_without_cc_env_or_tty(self, session_file, tmp_path):
        env = _env(tmp_path)  # no CC env; stdin/stdout/stderr are pipes
        result = _run("--stats", str(session_file), env=env)
        assert result.returncode == 0
        (line,) = _log_lines(tmp_path)
        assert line["caller"] == "robot"
        assert line["caller_detail"]  # parent process name, non-empty

    def test_unwritable_log_path_never_breaks_cli(self, session_file, tmp_path):
        blocker = tmp_path / "xdg"
        blocker.write_text("occupied")  # a file where the data dir should be
        env = dict(os.environ, XDG_DATA_HOME=str(blocker), CLAUDECODE="1")
        result = _run("--stats", str(session_file), env=env)
        assert result.returncode == 0, result.stderr
        assert "Traceback" not in result.stderr
