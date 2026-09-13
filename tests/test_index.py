"""Whole-history search index (dgc-puwupa).

Every test runs against a fake HOME so nothing touches the real
~/.claude/projects or ~/.cache/deglacer. The CLI tests invoke deglacer as a
real subprocess, as the bench runner does; the library tests call index.py
directly where the assertion is about state rather than plumbing.
"""

import json
import os
import shutil
import stat
import subprocess
import sys
from pathlib import Path

import pytest

from deglacer import index as dgi


def run_cli(*argv, home):
    env = dict(os.environ, HOME=str(home))
    return subprocess.run(
        [sys.executable, "-m", "deglacer.cli", *argv],
        capture_output=True, text=True, env=env,
    )


@pytest.fixture
def home(tmp_path, session_file):
    """A HOME whose ~/.claude/projects holds one synthetic session."""
    proj = tmp_path / "home" / ".claude" / "projects" / "-tmp-testproj"
    proj.mkdir(parents=True)
    shutil.copy(session_file, proj / "abc123.jsonl")
    return tmp_path / "home"


def _projects(home: Path) -> Path:
    return home / ".claude" / "projects"


def _db(home: Path) -> Path:
    return home / ".cache" / "deglacer" / "index.db"


def _add_session(home: Path, name: str, human_text: str, session_id: str | None = None) -> Path:
    """Write a minimal session with one human line and one assistant line."""
    d = _projects(home) / "-tmp-other"
    d.mkdir(parents=True, exist_ok=True)
    p = d / f"{name}.jsonl"
    sid = session_id or name
    with open(p, "w") as f:
        f.write(json.dumps({"type": "user", "sessionId": sid, "cwd": "/tmp/other",
                            "message": {"content": human_text},
                            "timestamp": "2026-05-01T10:00:00Z"}) + "\n")
        f.write(json.dumps({"type": "assistant", "sessionId": sid,
                            "message": {"id": f"msg-{name}", "content": [{"type": "text", "text": "noted"}]},
                            "timestamp": "2026-05-01T10:00:05Z"}) + "\n")
    return p


# -- round trip and privacy ---------------------------------------------------

def test_index_then_search_round_trip(home):
    built = run_cli("--index", home=home)
    assert built.returncode == 0, built.stderr
    assert "indexed 1 files" in built.stderr

    found = run_cli("--search", "kitchen", "metaphor", home=home)
    assert found.returncode == 0, found.stderr
    assert "abc123.jsonl" in found.stdout
    assert "test-ses" in found.stdout                       # id prefix column, as --recent
    assert "searched 1 indexed sessions, indexed as of 20" in found.stderr


def test_index_file_and_dir_are_private(home):
    run_cli("--index", home=home)
    db = _db(home)
    assert stat.S_IMODE(db.stat().st_mode) == 0o600
    assert stat.S_IMODE(db.parent.stat().st_mode) == 0o700


def test_index_holds_conversation_text_only(home):
    """Decision 2: tool results, thinking, meta injections and compaction
    summaries never enter the index. The fixture carries a distinctive word
    in each; none is searchable."""
    run_cli("--index", home=home)
    for word, lives_in in [("contents", "tool result"), ("think", "thinking block"),
                           ("loaded", "meta injection"), ("covered", "summary entry")]:
        cp = run_cli("--search", word, home=home)
        assert cp.returncode == 1, f"{word!r} from a {lives_in} was indexed"
        assert "No matches" in cp.stderr


def test_help_names_the_secret_surface():
    cp = subprocess.run([sys.executable, "-m", "deglacer.cli", "--help"],
                        capture_output=True, text=True)
    assert "0600" in cp.stdout
    assert "conversation text" in cp.stdout


def test_search_without_index_says_so(home):
    cp = run_cli("--search", "anything", home=home)
    assert cp.returncode == 1
    assert "run `deglacer --index` first" in cp.stderr


# -- incremental --------------------------------------------------------------

def test_second_run_skips_unchanged_file(home):
    run_cli("--index", home=home)
    again = run_cli("--index", home=home)
    assert again.returncode == 0
    assert "indexed 0 files" in again.stderr
    assert "1 unchanged" in again.stderr


def test_changed_file_is_reindexed_without_duplicates(home):
    run_cli("--index", home=home)
    f = _projects(home) / "-tmp-testproj" / "abc123.jsonl"
    with open(f, "a") as fh:
        fh.write(json.dumps({"type": "user", "sessionId": "test-session-001",
                             "message": {"content": "now discussing the zanzibar clause"},
                             "timestamp": "2026-04-05T09:30:00Z"}) + "\n")
    os.utime(f, (f.stat().st_mtime + 5, f.stat().st_mtime + 5))
    again = run_cli("--index", home=home)
    assert "indexed 1 files" in again.stderr

    with dgi.open_index(_db(home), readonly=True) as conn:
        assert conn.execute("SELECT COUNT(*) FROM files").fetchone()[0] == 1
        rows = conn.execute("SELECT COUNT(*) FROM turns WHERE text LIKE '%What is deglacer%'").fetchone()[0]
    assert rows == 1, "re-indexing a changed file must replace its rows, not add to them"
    assert run_cli("--search", "zanzibar", home=home).returncode == 0


# -- the prune guard (decision 6) ---------------------------------------------

def test_prune_guard_refuses_at_half_and_changes_nothing(home):
    _add_session(home, "second", "the second session")
    run_cli("--index", home=home)
    os.remove(_projects(home) / "-tmp-other" / "second.jsonl")   # 1 of 2 gone = 50%

    with pytest.raises(dgi.PruneRefused) as exc:
        dgi.build_index(root=_projects(home), db_path=_db(home))
    assert exc.value.indexed == 2 and len(exc.value.would_remove) == 1

    with dgi.open_index(_db(home), readonly=True) as conn:
        assert conn.execute("SELECT COUNT(*) FROM files").fetchone()[0] == 2

    cp = run_cli("--index", home=home)
    assert cp.returncode == 1
    assert "REFUSED" in cp.stderr and "second.jsonl" in cp.stderr and "--force-prune" in cp.stderr


def test_prune_guard_refuses_an_empty_scan(home):
    """deja's failure exactly: the root reads as empty, the index is full."""
    run_cli("--index", home=home)
    shutil.rmtree(_projects(home))
    _projects(home).mkdir()
    cp = run_cli("--index", home=home)
    assert cp.returncode == 1 and "REFUSED" in cp.stderr
    with dgi.open_index(_db(home), readonly=True) as conn:
        assert conn.execute("SELECT COUNT(*) FROM files").fetchone()[0] == 1


def test_force_prune_removes(home):
    _add_session(home, "second", "the second session")
    run_cli("--index", home=home)
    os.remove(_projects(home) / "-tmp-other" / "second.jsonl")
    cp = run_cli("--index", "--force-prune", home=home)
    assert cp.returncode == 0
    assert "1 removed" in cp.stderr
    with dgi.open_index(_db(home), readonly=True) as conn:
        assert conn.execute("SELECT COUNT(*) FROM files").fetchone()[0] == 1
        assert conn.execute("SELECT COUNT(*) FROM turns WHERE text LIKE '%second session%'").fetchone()[0] == 0


def test_prune_below_half_proceeds(home):
    _add_session(home, "second", "the second session")
    _add_session(home, "third", "the third session")
    run_cli("--index", home=home)
    os.remove(_projects(home) / "-tmp-other" / "third.jsonl")    # 1 of 3 = 33%
    cp = run_cli("--index", home=home)
    assert cp.returncode == 0
    assert "1 removed" in cp.stderr


# -- root resolution and session identity -------------------------------------

def test_symlinked_projects_root_indexes_non_empty(tmp_path, session_file):
    home = tmp_path / "home"
    real = tmp_path / "farm" / "projects" / "-tmp-testproj"
    real.mkdir(parents=True)
    shutil.copy(session_file, real / "abc123.jsonl")
    (home / ".claude").mkdir(parents=True)
    os.symlink(tmp_path / "farm" / "projects", home / ".claude" / "projects")

    cp = run_cli("--index", home=home)
    assert cp.returncode == 0
    assert "indexed 1 files" in cp.stderr
    assert str(tmp_path / "farm" / "projects") in cp.stderr    # the realpath, not the link


def test_same_session_under_two_paths_is_one_result(home):
    """A session copied between machines lands under two encoded cwds; the
    bench's answer key is a session id, so ranking collapses on it."""
    twin = _projects(home) / "-Users-modha-testproj"
    twin.mkdir()
    shutil.copy(_projects(home) / "-tmp-testproj" / "abc123.jsonl", twin / "abc123.jsonl")
    run_cli("--index", home=home)
    cp = run_cli("--search", "kitchen", home=home)
    assert cp.stdout.count("abc123.jsonl") == 1


def test_subagent_transcripts_are_skipped(home):
    sub = _projects(home) / "-tmp-testproj" / "abc123" / "subagents"
    sub.mkdir(parents=True)
    shutil.copy(_projects(home) / "-tmp-testproj" / "abc123.jsonl", sub / "agent-1.jsonl")
    cp = run_cli("--index", home=home)
    assert "indexed 1 files" in cp.stderr


# -- search semantics ---------------------------------------------------------

def test_build_match_quotes_every_term():
    assert dgi.build_match("marmite diet") == '"marmite" OR "diet"'
    assert dgi.build_match('"two zone" 25.12.5') == '"two zone" OR "25.12.5"'
    assert dgi.build_match("AND OR NOT") == '"AND" OR "OR" OR "NOT"'
    assert dgi.build_match('say "hi" there') == '"say" OR "hi" OR "there"'
    assert dgi.build_match("") == ""


def test_identifier_with_punctuation_matches_as_phrase(home):
    _add_session(home, "fw", "flashed openwrt 25.12.5 onto the router")
    run_cli("--index", home=home)
    assert "fw.jsonl" in run_cli("--search", "25.12.5", home=home).stdout
    assert run_cli("--search", "25.5.12", home=home).returncode == 1   # order matters in a phrase


def test_search_ranks_the_session_matching_more_terms_first(home):
    _add_session(home, "both", "the marmite diet went badly")
    _add_session(home, "one", "a diet of plain toast")
    run_cli("--index", home=home)
    out = run_cli("--search", "marmite", "diet", home=home).stdout
    assert out.index("both.jsonl") < out.index("one.jsonl")


def test_search_since_narrows_by_mtime(home):
    old = _add_session(home, "old", "ancient marmite lore")
    os.utime(old, (0, 0))
    _add_session(home, "new", "fresh marmite lore")
    run_cli("--index", home=home)
    cp = run_cli("--search", "marmite", "--since", "2020-01-01", home=home)
    assert "new.jsonl" in cp.stdout and "old.jsonl" not in cp.stdout
    assert "modified since 2020-01-01" in cp.stderr


def test_search_limit_caps_and_says_so(home):
    for i in range(3):
        _add_session(home, f"s{i}", "marmite again")
    run_cli("--index", home=home)
    cp = run_cli("--search", "marmite", "--limit", "2", home=home)
    assert cp.stdout.count(".jsonl") == 2
    assert "capped" in cp.stderr


def test_rebuild_starts_from_scratch(home):
    run_cli("--index", home=home)
    with dgi.open_index(_db(home)) as conn:
        conn.execute("INSERT INTO meta(key, value) VALUES ('canary', '1')")
        conn.commit()
    cp = run_cli("--index", "--rebuild", home=home)
    assert cp.returncode == 0
    with dgi.open_index(_db(home), readonly=True) as conn:
        assert conn.execute("SELECT COUNT(*) FROM meta WHERE key='canary'").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM files").fetchone()[0] == 1
