# Deglacer

Library + CLI for parsing Claude Code session JSONL files. Garde-manger, gueridon, and any future consumer import the same battle-tested parsing logic; the `deglacer` CLI exposes it for one-off introspection.

## Quick Commands

```bash
uv run --group dev pytest                          # run tests
uv tool install ~/Repos/batterie/deglacer --reinstall  # install/upgrade CLI
deglacer --help                                    # CLI usage
```

## Module Map

| Module | Role |
|--------|------|
| `parsing` | JSONL loading, entry type classification (`is_human_message`, `is_tool_result`, `is_meta`), billing-lane attribution (`billing_lane`) |
| `content` | System tag stripping, assistant content extraction (text/tools/thinking) |
| `conversation` | Message.id merging (streaming dragon), turn building, text/JSON formatting |
| `markdown` | Claude.ai-export-format markdown export (frontmatter + coalesced turns + inline tool markers) |
| `stats` | Token/model/tool counting, timeline, summary |
| `health` | Parse-health assessment behind `--doctor` — tripwires for CC format drift |
| `discovery` | Session file finding and cross-session search |
| `cli` | argparse entry point — wired via `[project.scripts]` in pyproject |
| `_invlog` | Vendored estate invocation-log shim — every CLI run appends one caller-stamped JSONL line to `~/.local/share/deglacer/invocations.jsonl`, subcommand field carrying the dispatch-order mode. Never edit here; re-vendor from canonical (spm1001/harness-ergonomics, which holds the conformance test) |

Everything is re-exported from `__init__.py` — consumers just `import deglacer`.

## Key Conventions

- **Zero external dependencies.** Stdlib only. Keep it that way.
- **Parity tests.** `tests/test_parity.py` runs deglacer against ccconv on a real session file (skipped if ccconv isn't installed). If you change parsing logic, these must still pass when ccconv is available.
- **The streaming dragon, and its two halves.** Multiple JSONL lines share one `message.id`. For *content*, `merge_assistant_entries` combines their blocks. For *usage* — tokens, models, per-request rates — use `dedupe_by_request`, which keys on `requestId` and takes the whole row from the highest-`output_tokens` entry, because output GROWS as the response streams. They answer different questions and neither substitutes for the other. Summing raw entries overcounts totals 2.3×; keeping the first of each group undercounts output 50.6% (measured over 400 files, dgc-tenoze).
- **A silent parser is the hazard, so `--doctor` exists.** The CC schema moves with releases and nothing announces it; a drifted parser reports different numbers rather than raising. `health.py` counts what was skipped and what collapsed, and flags the states that mean drift — chiefly **zero duplicates collapsed**, which is what the 2.3× bug would have tripped on day one. Exits 1 when flagged. If you add a parsing assumption, add the tripwire that fires when it stops holding.
- **Markdown coalesces consecutive same-role turns.** CC splits a single logical response across many `message.id`s when tool cycles run; claude.ai exports merge these. The markdown exporter mirrors that visual shape so register-study readers see one bubble per human prompt.
- **`content.py` strips system tags BUT unwraps `<command-args>`.** Slash-command preambles get stripped entirely; the user's actual prompt text wrapped in `<command-args>` is kept (tag removed, content preserved). New tag patterns added to either path affect ALL output modes — be deliberate.

## What's new (v0.3.0)

- `--markdown` mode produces claude.ai-export-format files: frontmatter (title, date, source, uuid, message_count, tools_used), H1 title, alternating `## You · ts` / `## Claude · ts` bubbles, tool calls inline-collapsed with emoji markers (`🔧 Bash`, `📄 Read`, `🌐 Fetched`…). Suggested filename printed to stderr so `deglacer --markdown … > sample.md` is the natural pattern. Built for the cornichon persona-forge dataset, but generally useful for any corpus that mixes CC and claude.ai conversations.

Work is tracked on a bon board in `.bon/` — read `.bon/README.md` before reading or changing anything there.
