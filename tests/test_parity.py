"""Parity tests — verify deglacer produces identical output to ccconv.

These tests run against real session files and compare deglacer's output
with ccconv's output to ensure the extraction is faithful.
"""

import json
import subprocess
import sys
from pathlib import Path

import pytest

import deglacer

CCCONV = Path.home() / "Repos/batterie/trousse/skills/deglacer/scripts/ccconv.py"

# Find a real session file to test against
_PROJECTS = Path.home() / ".claude" / "projects"


def _find_test_session() -> str | None:
    """Find a small-ish real session file for parity testing."""
    if not _PROJECTS.exists():
        return None
    candidates = []
    for p in _PROJECTS.rglob("*.jsonl"):
        if "/subagents/" in str(p):
            continue
        size = p.stat().st_size
        if 30_000 < size < 200_000:
            candidates.append(p)
    if not candidates:
        return None
    # Pick the most recently modified
    candidates.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    return str(candidates[0])


REAL_SESSION = _find_test_session()


@pytest.mark.skipif(
    REAL_SESSION is None or not CCCONV.exists(),
    reason="No real session file or ccconv not found",
)
class TestParityWithCcconv:
    """Compare deglacer output with ccconv on a real session."""

    def _run_ccconv(self, *args) -> str:
        result = subprocess.run(
            [sys.executable, str(CCCONV), *args, REAL_SESSION],
            capture_output=True, text=True, timeout=30,
        )
        assert result.returncode == 0, f"ccconv failed: {result.stderr}"
        return result.stdout

    def test_text_output_matches(self):
        ccconv_output = self._run_ccconv()
        entries = deglacer.parse_session(REAL_SESSION)
        turns = deglacer.build_turns(entries)
        deglacer_output = deglacer.format_text(turns) + '\n'
        # Normalize trailing whitespace
        assert ccconv_output.strip() == deglacer_output.strip()

    def test_json_output_matches(self):
        ccconv_output = self._run_ccconv("--json")
        entries = deglacer.parse_session(REAL_SESSION)
        turns = deglacer.build_turns(entries)
        ccconv_turns = json.loads(ccconv_output)
        deglacer_turns = json.loads(deglacer.format_json(turns))
        assert ccconv_turns == deglacer_turns

    def test_stats_output_matches(self):
        ccconv_output = self._run_ccconv("--stats")
        entries = deglacer.parse_session(REAL_SESSION)
        deglacer_output = deglacer.format_stats(entries)
        assert ccconv_output.strip() == deglacer_output.strip()

    def test_with_tools_matches(self):
        ccconv_output = self._run_ccconv("--with-tools")
        entries = deglacer.parse_session(REAL_SESSION)
        turns = deglacer.build_turns(entries, with_tools=True)
        deglacer_output = deglacer.format_text(turns)
        assert ccconv_output.strip() == deglacer_output.strip()

    def test_summary_matches(self):
        ccconv_output = self._run_ccconv("--summary")
        entries = deglacer.parse_session(REAL_SESSION)
        deglacer_output = deglacer.format_summary(entries)
        assert ccconv_output.strip() == deglacer_output.strip()
