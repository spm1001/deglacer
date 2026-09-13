# tools/ — throwaway instruments worth keeping

## sweep-tool-calls.py

Walks every Claude Code transcript on the machine (`~/.claude/projects` plus any
sibling config dirs), pairs each Bash `tool_use` with its `tool_result`, and
buckets the commands that touch session data — deglacer, deja, jq, rg, python.
Writes one JSONL line per call.

```bash
python3 tools/sweep-tool-calls.py     # ~60s over 22,938 files; writes /tmp/dgc-sweep/calls.jsonl
```

## jq-programs-2026-09-13.txt

The **oracle itself**, extracted from that sweep and committed: 605 `jq` programs
Claudes ran against CC transcripts between 1 July and 13 September 2026, from 87
sessions, deduplicated to 400 distinct lines with counts. Scanned for
credential-shaped strings before committing; none.

This is `dgc-mahula`'s acceptance test data. For each of the thirteen question
shapes, the real command here is replayed against a real transcript and the
deglacer equivalent asserted to print the **same values** — so jq, which the
worker did not write, generates the expected answer.

**Why it lives in the repo rather than in `/tmp`.** It is the instrument that
produced the `--entries` specification on `dgc-mahula`: the 605 `jq` programs it
extracted ARE that card's acceptance oracle, and the card is fan-ready only
because they exist. The brief says "re-derive it, it is 60 lines", which is
optimistic — re-deriving the *script* is easy, re-deriving the *judgement about
what to bucket* is not. The original sat in `/tmp`, which on tube is disk-backed
rather than tmpfs — `tmp.mount` is masked, as `traps.md` records — so the danger
was never a reboot. It was `/usr/lib/tmpfiles.d/tmp.conf`, which sweeps `/tmp`
on a **10-day** age rule. A quieter deadline than a reboot, and a worse one,
because nothing announces it.

**What it is good for beyond that card.** Any question of the form "what do
Claudes actually DO on this estate", answered from behaviour rather than from
what we imagine. The 2026-08-28 handoff's lesson applies directly: test fixtures
encode what you already imagined, so a corpus sweep is the only step capable of
surprising you. This is that step, made cheap.

**Known limits.** It classifies on the whole command string, so a compound
command (`cd X && echo Y && jq …`) counts once per bucket it matches; the useful
grain came from extracting the `jq` programs themselves afterwards, not from the
bucket counts. Treat any count it produces as an upper bound until you have read
a sample — which is the same discipline the counts were used to argue for.
