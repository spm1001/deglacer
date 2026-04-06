# Deglacer — Understanding

Shared Python library for parsing Claude Code session JSONL files. The name follows the kitchen metaphor: deglazing the pan to lift the fond — extracting the good bits from past sessions.

## What it does

Five modules, all re-exported from `__init__.py`:

| Module | Role |
|--------|------|
| `parsing` | JSONL loading, entry type classification (`is_human_message`, `is_tool_result`, `is_meta`) |
| `content` | System tag stripping, assistant content extraction (text/tools/thinking) |
| `conversation` | Message.id merging (streaming dragon), turn building, text/JSON formatting |
| `stats` | Token/model/tool counting, timeline, summary |
| `discovery` | Session file finding and cross-session search |
| `cli` | `deglacer` command — argparse wrapper over the library |

Zero external dependencies. Stdlib only. This is deliberate — consumers install deglacer via git dep and don't inherit a dependency tree.

## The CLI

`deglacer` is installed as a uv tool (`uv tool install ~/Repos/batterie/deglacer`). Six output modes: text (default), json, stats, summary, timeline, plus discovery (--recent, --find, --today, --since). It replaced `ccconv.py` which lived in trousse as a PEP 723 script with all parsing inline. The CLI is a thin argparse wrapper — all logic is in the library modules.

## Consumers

| Consumer | How it uses deglacer | Dependency declaration |
|----------|---------------------|----------------------|
| **garde-manger** | `import deglacer as dg` — `parse_session`, `build_turns`, `format_text` for conversation extraction. Metadata (tool calls, files, skills, commits) extracted locally | `[tool.uv.sources] deglacer = { git = "..." }` |
| **trousse** | Skill (`/deglacer`) invokes the `deglacer` CLI. SKILL.md has the schema reference and anti-patterns. No Python import | CLI only — `deglacer` must be installed as a uv tool |
| **gueridon** | Independent TypeScript JSONL parser in `bridge-logic.ts`. Cannot import Python. Reimplements ID-merging for SSE replay | Not a consumer (yet) |

## The streaming dragon

Multiple JSONL lines can share the same `message.id` — CC streams incremental updates. `merge_assistant_entries` deduplicates by merging content blocks and deduplicating `tool_use` by block id. Every consumer must handle this or produce duplicated output.

## Schema reference

The CC JSONL schema reference lives in trousse's SKILL.md (`skills/deglacer/SKILL.md`), not in this repo. That's deliberate — the skill is Claude-facing context (entry types, anti-patterns, jq recipes), the library is code. The separation keeps deglacer dependency-free and focused.

## Parity testing

`tests/test_parity.py` runs deglacer against the original ccconv.py on shared fixtures to verify identical output. If parsing logic changes, these tests catch divergence. The CLI was also parity-verified against ccconv across all six output modes before ccconv was deleted.

## Landmines

**The `user` entry is triple-duty.** type=user can be a human message, a tool result, or a skill/system injection. `is_human_message`, `is_tool_result`, `is_meta` discriminate. Don't filter by type alone.

**`input_tokens` is not total input.** Real input = `input_tokens + cache_creation_input_tokens + cache_read_input_tokens`. The `format_stats` function handles this correctly.

**Summary entries are minimal.** No uuid, parentUuid, timestamp, version, or sessionId. Three fields only: type, summary, leafUuid.
