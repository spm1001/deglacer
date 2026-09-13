# Deglacer

Library + CLI for parsing Claude Code session JSONL files. The live consumers are glaneur's `glean-code` (the nightly transcript capture, via the CLI), terroir (library, git dependency) and the trousse `/deglacer` skill (Claude-facing); garde-manger was the original consumer and was decommissioned 2026-06-03. `.bon/understanding.md` carries the measured picture.

## Quick Commands

```bash
uv run --group dev pytest                          # run tests
uv tool install ~/repos/spm1001/deglacer --reinstall  # install/upgrade CLI
deglacer --help                                    # CLI usage
deglacer --index                                   # build/refresh the whole-history search index (~2 min cold, <1 s warm)
deglacer --search marmite diet                     # ranked session search over it
deglacer --entries --tool Bash S.jsonl | jq '...'  # forensic view: classified entries, tool calls paired with results
```

## Module Map

| Module | Role |
|--------|------|
| `parsing` | JSONL loading (optional `health=` counter), entry type classification (`is_human_message`, `is_tool_result`, `is_meta`), billing-lane attribution (`billing_lane`), per-request usage dedupe (`dedupe_by_request`) |
| `content` | System tag stripping, assistant content extraction (text/tools/thinking) |
| `conversation` | Message.id merging (streaming dragon), turn building, text/JSON formatting |
| `markdown` | Claude.ai-export-format markdown export (frontmatter + coalesced turns + inline tool markers) |
| `stats` | Token/model/tool counting, timeline, summary |
| `health` | Parse-health assessment behind `--doctor` — tripwires for CC format drift |
| `tools` | Tool-call labelling — one call becomes a groupable label plus a detail (`normalize_tool`, `bash_command`, `tool_calls`) |
| `discovery` | Session listing (`find_sessions`, `session_title`, `count_sessions`) and the recent-window search (`search_sessions`). Both caps are named constants and both are printed to stderr at call time — a scoped null must never read as an absence |
| `entries` | The forensic view: `--entries` streams one JSON line per entry with deglacer's classification in `kind` (human / tool_result / meta / assistant / system:<subtype> / attachment:<type> / raw type by name — nothing dropped) and each `tool_use` as its own row paired with its result across lines; `--kind` / `--tool` filter; `--meta` is the one-line session block. `usage` is emitted once per request (the entry `dedupe_by_request` keeps) so summing it is right; every other raw top-level key passes through flat. Acceptance test replays the corpus's real jq programs (`tools/jq-programs-2026-09-13.txt`) against real transcripts and asserts the same values |
| `index` | Whole-history search: SQLite FTS5 over human + assistant text only, one row per turn, ranked to session (`build_index`, `search_index`). `~/.cache/deglacer/index.db`, 0600 — it holds conversation text. Incremental on (size, mtime); a run that would prune ≥50% of the index refuses without `--force-prune`; the root is realpath-resolved so a symlinked `projects/` indexes rather than reading empty. Query terms are OR-ed and bm25-ranked; a quoted phrase is a phrase |
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

## What's new

**2026-09-13 — v0.5.0: `--entries` / `--meta`, the forensic view** (dgc-mahula). One JSON line per entry, streaming, classified (`kind`), with every `tool_use` paired to its `tool_result` across lines and `usage` once per request; `--kind K` (repeatable, `system` keeps all `system:*`), `--tool NAME`; `--meta FILE` for the session block. Built from the 605 jq programs Claudes hand-rolled since July, and tested by replaying those programs against two real transcripts (oldest and newest that clear a richness bar, picked by rule at test time) and asserting the same values. Two things the oracle taught: **a `requestId` spans a tool round-trip when tools run in parallel** — CC writes block 0, runs it, writes its result, then writes block 1's entry (`apiBlockIndex` orders them), so grouping a request by contiguity emits its usage twice; and current CC stamps `origin.kind` / `promptSource` on string-content user entries (`human`/`typed` vs `task-notification`/`system`), which `is_human_message` does not read yet (see dgc-zekodo).

**2026-09-13 — v0.4.0: `--index` / `--search`, the native replacement for deja** (dgc-puwupa). A SQLite FTS5 index over conversation text — human and assistant turns only, never tool results or thinking — at `~/.cache/deglacer/index.db` (0600), built in 113 s over 7,646 sessions / 5.5 GB into 208 MB, refreshed in under a second, searched warm in 125–480 ms. On the 13-task `banc/session-search` bench that judged deja: whole-question recall 13/13 (deja 64%), short-fragment 12/13 (deja 85%), hit@1 54% (deja 8–31%). Also `iter_session`, the streaming twin of `parse_session`. The one bench miss is a single crowded term (`marmite`) — a short query is a guess, and the whole question found that session at rank 1.

**2026-09-13 — v0.3.1: scoped search says so, listings show titles.** `--find` prints its window ("searched the 200 most-recent sessions of N; K shown") and `--since` widens it; `--recent` shows the transcript's `ai-title` instead of a blank slug column; `--version` exists; a multi-file glob gets a loop hint instead of an argparse error. The check-in that produced these also measured that 251 of 301 transcript-reading sessions since July hand-rolled jq instead of using deglacer, for forensic fields the turn-shaped `--json` doesn't expose — see dgc-mahula and understanding.md.

**2026-08-28 — usage counted per request, and two ways to interrogate a session.**

- **`--stats` was overcounting tokens 2.3×** (dgc-tenoze). It summed `message.usage` over raw assistant entries, but CC writes one response as one entry per content block, each repeating the same usage object. `dedupe_by_request` fixes it. The rule is subtler than "keep one per id": output *grows* as the response streams, so keeping the first entry undercounts output by 50.6%. Take the whole usage row from the highest-`output_tokens` entry.
- **`--doctor`** (dgc-guriso) reports parse health and exits 1 when flagged. The load-bearing check is **zero duplicates collapsed** — on a real-sized transcript that means the dedupe key has stopped matching and every total is inflated, which is exactly the bug above, catchable on day one instead of week seven.
- **`--stats --tools`** (dgc-fovipe) breaks tool counts down by file, command or host. Bash labels strip leading wrappers, because **42.9% of real Bash calls start with `cd`** — unstripped, the commonest label in any report is a directory change.

**v0.3.0 — `--markdown`.** Produces claude.ai-export-format files: frontmatter (title, date, source, uuid, message_count, tools_used), H1 title, alternating `## You · ts` / `## Claude · ts` bubbles, tool calls inline-collapsed with emoji markers (`🔧 Bash`, `📄 Read`, `🌐 Fetched`…). Suggested filename printed to stderr so `deglacer --markdown … > sample.md` is the natural pattern. Built for the cornichon persona-forge dataset, but generally useful for any corpus that mixes CC and claude.ai conversations.

Work is tracked on a bon board in `.bon/` — read `.bon/README.md` before reading or changing anything there.
