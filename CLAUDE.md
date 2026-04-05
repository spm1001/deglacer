# Deglacer

Shared Python library for parsing Claude Code session JSONL files. No CLI — that stays in trousse's ccconv. This package exists so garde-manger, gueridon, and any future consumer can import the same battle-tested parsing logic.

## Quick Commands

```bash
uv run --group dev pytest          # run tests
uv pip install -e .                # editable install
```

## Module Map

| Module | Role |
|--------|------|
| `parsing` | JSONL loading, entry type classification (`is_human_message`, `is_tool_result`, `is_meta`) |
| `content` | System tag stripping, assistant content extraction (text/tools/thinking) |
| `conversation` | Message.id merging (streaming dragon), turn building, text/JSON formatting |
| `stats` | Token/model/tool counting, timeline, summary |
| `discovery` | Session file finding and cross-session search |

Everything is re-exported from `__init__.py` — consumers just `import deglacer`.

## Key Conventions

- **No CLI entry point.** The CLI lives in trousse's ccconv, which will import this library.
- **Zero external dependencies.** Stdlib only. Keep it that way.
- **Parity tests.** `tests/test_parity.py` runs deglacer against ccconv on a real session file to verify identical output. If you change parsing logic, these must still pass.
- **The streaming dragon.** Multiple JSONL lines can share the same `message.id` — `merge_assistant_entries` handles deduplication. Don't bypass it.
