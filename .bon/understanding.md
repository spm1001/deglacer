# Deglacer — Understanding

Shared Python library and CLI for parsing Claude Code session JSONL files. The name follows the kitchen metaphor: deglazing the pan to lift the fond — extracting the good bits from past sessions. Second edition, 2026-09-13: rewritten from the 2026-08-28 handoff and a measured check-in on how the estate actually uses the tool; the first edition (April 2026) is in git.

## What it does

Ten modules, all re-exported from `__init__.py`:

| Module | Role |
|--------|------|
| `parsing` | JSONL loading (optional `health=` counter), entry classification (`is_human_message`, `is_tool_result`, `is_meta`), billing-lane attribution from the `requestId` prefix (`billing_lane`), per-request usage dedupe (`dedupe_by_request`) |
| `content` | System-tag stripping, assistant content extraction (text / tools / thinking) |
| `conversation` | `message.id` merging, turn building, text/JSON formatting |
| `markdown` | claude.ai-export-shaped markdown, consecutive same-role turns coalesced |
| `stats` | Token, model and tool counts, timeline, summary |
| `health` | Parse-health counters behind `--doctor` — tripwires for CC format drift |
| `tools` | Tool-call labelling: one call becomes a groupable label plus a detail |
| `discovery` | Session listing (`find_sessions`, `session_title`, `count_sessions`) and the recent-window search (`search_sessions`) |
| `cli` | argparse entry point; every mode is a flag, there are no subcommands |
| `_invlog` | Vendored invocation-log shim: one caller-stamped JSONL line per run to `~/.local/share/deglacer/invocations.jsonl`. Canonical copy and conformance test live in spm1001/harness-ergonomics; never edit here |

Zero external dependencies, stdlib only, and the constraint has paid for itself twice: consumers take it as a git dependency without a tree, and the 2026-09-13 proposal for a native search index (below) is feasible only because SQLite FTS5 ships in the system Python.

## Consumers — the live ones, measured

The first edition named garde-manger as the primary library consumer. It was decommissioned on 2026-06-03 and that table misled a consolidation decision (dgc-fozega). As of 2026-09-13:

| Consumer | How | Volume |
|----------|-----|--------|
| **glaneur `glean-code`** (nightly timer 00:20 on tube, and bon's SessionEnd hook) | Shells to the CLI twice per session: `--json` for the keep predicate, then `--with-tools --with-thinking --json` for the body; writes markdown to `~/notes/raw/claude/code` | ~6,600 calls a night, 99.7% of the invocation log — it re-evaluates ~3,450 already-decided sessions every night (glan-zupoti) |
| **terroir** | `from deglacer.parsing import …` as a git dependency; a parity test binds its lane detection to ours | Library only |
| **trousse `/deglacer` skill** | Claude-facing schema reference; routes cold Claudes to the CLI | Hand-typed use: ~15 CLI calls a week |
| **gueridon** | Independent TypeScript parser; cites the schema reference in comments, imports nothing | Not a consumer |

Hand-typed use by Claudes, from a sweep of every transcript on tube: 137 deglacer invocations in 53 sessions since April; commonest flags `--last`, `--summary`, `--with-tools`, `--stats`, `--recent`. Error rate is low (2 of 137) and both were the same shape — a glob that matched several files, which argparse refused with a bare "unrecognized arguments". The CLI now says what it wants instead.

## The demand deglacer does not meet (the finding that matters)

Since July, 301 sessions read CC transcripts. 251 never called deglacer; they ran 1,743 hand-rolled jq / rg / python commands over the JSONL. The fields those commands project are stable: `.type`, `.message.content`, `.timestamp`, tool name and input, `.message.model`, `isMeta`, system `subtype`, `attachment`, `toolUseResult`, `permissionMode`, `requestId`, `cwd`. That is forensic work — which entries, which tool results, which model, which hook — and deglacer's turn-shaped `--json` answers a different question: what was said. The trousse skill frames raw jq as a mistake to be gated; the corpus says the gate does not hold because the tool does not cover the demand. The proposed answer is an `--entries` mode built from those projected fields, with deglacer's classification and tool-use/result pairing attached (dgc-mahula). Until it exists, a Claude hand-rolling jq on a transcript is not necessarily doing it wrong.

## Cross-session search, and deja

`deglacer --find` scans the 200 most-recently-modified sessions and stops at 10 matches. Since 0.3.1 it prints that scope on every run and `--since DATE` widens it to every session after the date; a null from it is a statement about the window, and the CLI now says so.

**deja** (github.com/vshulcz/deja-vu) was benched on 2026-08-09, routed to from the trousse skill as the ranked whole-history search, and **dropped from the estate on 2026-09-13** (Sameer's call, dgc-secise). What the measurement found: 52 invocations ever in 21 sessions, two genuine searches by a Claude in the preceding month, index last written 2026-09-02, installed build pinned at v0.16.9 against an upstream at v0.20.0 shipping every two to three days and heading toward auto-recall inside agents — the surface the adoption card had ruled out. Over the same period Claudes ran 192 cross-session `rg` searches. The recall need was real; deja was not what met it.

Gone with it: the binary, the 563 MB index, the `DEJA_CLAUDE_ROOT` guards in dotfiles and the commis unit, the weekly freshness doorbell in `update-dev-tools.sh`, and the skill's routing section. Kept deliberately: `banc/session-search`, because its 13-task bench is the **acceptance oracle** for the replacement, and its `runs-v0.16.9/` baseline is the 85% recall figure to beat. The replacement is specified on dgc-puwupa — `--index` / `--search` on SQLite FTS5 over conversation text only. Until it lands, cross-session search is `rg -li -F` over the corpus, unranked, and the trousse reference says so.

## The streaming dragon, and its two halves

Multiple JSONL lines share one `message.id`. For *content*, `merge_assistant_entries` combines their blocks and dedupes `tool_use` by block id. For *usage* — tokens, models, per-request rates — use `dedupe_by_request`, which keys on `requestId` and takes the whole usage row from the highest-`output_tokens` entry, because output grows as the response streams. Summing raw entries overcounts 2.3×; keeping the first of each group undercounts output 50.6% (400 files, dgc-tenoze). They answer different questions and neither substitutes for the other.

## Why `--doctor` exists

The CC schema moves with releases and nothing announces it; a drifted parser reports different numbers rather than raising. `health.py` counts what was skipped and what collapsed and flags the states that mean drift — chiefly **zero duplicates collapsed**, which is what the 2.3× bug would have tripped on day one. Two judgement values, unmeasured: `MOSTLY_MISSING_REQUEST_ID = 0.5`, and the eighteen `WRAPPERS` in `tools.py` of which five were observed. If CC ever writes one entry per response, the zero-duplicates alarm fires on every healthy transcript; invert it then, don't silence it.

## Telemetry — what the invocation log can and cannot say

- **Robot noise dominates.** 46,709 rows in the week to 2026-09-13; 46,556 from glean-code. Filter on `caller` before reading anything.
- **`caller=model` is not "a Claude typed this".** The shim reads CC's environment variables, and CC passes them into every hook, so glean-code run from the SessionEnd hook logs as `model/cli` (about 120 of that week's 153 model rows). Fix filed on the canonical shim (erg-lelace). Until it lands, the two-call signature (`--json` then `--with-tools --with-thinking --json`, seconds apart) is the hook.
- **Rotation is 50 MB × 10 files**, so the log caps itself; the first rotation happened 2026-09-07, two weeks after the shim shipped.

## Schema reference

The CC JSONL schema reference lives in trousse's `skills/deglacer/SKILL.md`, not here — Claude-facing context stays with the skill, code stays with the library. It lags the format (dgc-zekodo lists the entry types it misses); `--doctor` prints an entry-type table that makes auditing it a one-command job. Two entries the discovery code now relies on: `{"type":"ai-title","aiTitle":…}` (what `/resume` shows; 1,500 sessions carry one) and `{"type":"last-prompt","lastPrompt":…}`, both near the file tail.

## Landmines

**The `user` entry is triple-duty.** type=user can be a human message, a tool result, or a skill/system injection. `is_human_message`, `is_tool_result`, `is_meta` discriminate. Don't filter by type alone.

**`input_tokens` is not total input.** Real input = `input_tokens + cache_creation_input_tokens + cache_read_input_tokens`. `format_stats` handles this.

**Summary entries are minimal.** No uuid, parentUuid, timestamp, version or sessionId — three fields only.

**`bash_command` is a heuristic over a real shell grammar.** Good enough for grouping counts, wrong for anything that must be correct.

**A resumed session replays its history into a new file with the original timestamps** (OtoDock's tailer found this; dgc-raveve). Usage keys on `requestId` so cost totals should survive it; every other count is unmeasured until that card runs.

**Test fixtures encode what you already imagined.** The 2026-08-28 session's labelling tests were green while a sweep of 2,869 real Bash commands found five bugs. For anything that classifies, estimates or labels, the corpus sweep is the step that can surprise you; budget it as part of the work. A sweep script that pairs every Bash tool_use with its result over all 22,938 transcripts runs in about a minute and is 60 lines; rebuild it rather than trusting fixtures.

## Parity testing

`tests/test_parity.py` runs deglacer against the original ccconv on a real session file (skipped when ccconv isn't installed). terroir carries the mirror: its test binds to our `billing_lane`.
