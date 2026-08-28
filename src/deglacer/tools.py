"""Tool-call labelling — one call becomes a groupable label plus a detail.

`tools[block["name"]]` answers "how many Bash calls" and nothing else. The
interesting questions are one axis further down: which *file* keeps being read,
which *command* dominates, which *MCP server* the traffic goes to. So a call
splits into a **label** you group by and a **detail** that names the specific
thing.

The shape is borrowed from kelviq/tare's `normalize_tool`, with one correction
that only a measurement produces. Taking the first word of a Bash command
buckets every compound command starting `cd` together, and on this estate that
is not a rounding error: **42.9% of 2,869 real Bash calls begin with `cd`**
(measured 2026-08-28 over 250 sessions). Unstripped, the single most common
label in any report is a directory change that tells you nothing — it topped a
real tare run at 255M amplified tokens purely as a labelling artefact.

So leading wrappers are stripped to reach the command that actually did the
work. The wrapper list and the separator set are both measured rather than
imagined: separators seen in practice are `&&` (1,131), newline (93) and `;`
(6). A pipe is deliberately NOT a separator — `grep x | head` is one pipeline
whose first command is the point of it.
"""

import re

# Leading words that wrap another command rather than being the work.
# Measured over 250 sessions: cd 42.9%, sudo 0.8%, command 0.7%, `.` 0.1%,
# env 0.0%. The rest are included because they wrap by definition and cost
# nothing to list.
WRAPPERS = frozenset({
    "cd", "env", "sudo", "setsid", "nohup", "command", "time", "exec",
    "builtin", "source", ".", "timeout", "nice", "ionice", "xargs",
    "stdbuf", "unbuffer", "doas",
})

# Wrappers that take flags/arguments of their own before the wrapped command.
# `timeout 300 foo` and `cd /tmp && foo` need different handling: the first
# consumes its argument inline, the second ends at a separator.
_INLINE_WRAPPERS = frozenset({"timeout", "nice", "ionice", "xargs", "stdbuf",
                              "unbuffer", "env", "sudo", "command", "exec",
                              "builtin", "time", "doas", "setsid", "nohup"})

# Inline wrappers taking one positional operand before the command they wrap:
# `timeout 300 foo`, `nice -n 5 10 foo`. Without this the operand becomes the
# label — `timeout 300 python3 x.py` labelled as "300", caught by a test.
_TAKES_OPERAND = {"timeout": re.compile(r"^[\d.]+[smhd]?$"),
                  "nice": re.compile(r"^-?\d+$"),
                  "ionice": re.compile(r"^-?\d+$")}

# Statement separators. NOT `|` — a pipeline's first command is the point of it.
_SEPARATORS = re.compile(r"&&|\|\||;|\n")

# VAR=value prefixes (`FOO=1 bar`) are environment, not the command.
_ASSIGNMENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")

# `VAR=$(cmd …)` is a command substitution, not an env prefix — the command is
# INSIDE the assignment. Skipping the word yielded labels like `-td` and `&&`
# (23 real calls), caught by sweeping 2,869 commands rather than by the tests.
_SUBSTITUTION = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=[$`]\(?\s*([A-Za-z0-9_./-]+)")

MAX_DETAIL = 120


def bash_command(command: str) -> str | None:
    """The command that did the work, past any leading wrappers.

    `cd /tmp && uv run x.py` → `uv`;  `timeout 60 rg foo` → `rg`;
    `FOO=1 python3 x.py` → `python3`;  `grep x | head` → `grep` (one pipeline).
    Returns None for an empty command.
    """
    if not command or not command.strip():
        return None

    remaining = command.strip()
    # Bounded: each pass must consume at least one token, and a pathological
    # command should degrade to a label rather than spin.
    for _ in range(12):
        words = remaining.split()
        if not words:
            return None
        head = words[0]

        if _ASSIGNMENT.match(head):
            substituted = _SUBSTITUTION.match(remaining)
            if substituted:
                # VAR=$(cmd …): the command is inside the assignment.
                return substituted.group(1).rsplit("/", 1)[-1]
            # A quoted value may contain spaces, so consume to the closing
            # quote rather than to the next whitespace — `F="a b/c.md"` split
            # on whitespace labelled a real session's calls "UK".
            value = remaining[len(head.split("=")[0]) + 1:].lstrip()
            if value[:1] in ("'", '"'):
                closing = value.find(value[0], 1)
                consumed = len(value) if closing == -1 else closing + 1
                remaining = value[consumed:].strip()
            else:
                remaining = remaining[len(head):].strip()
            # `VAR=/path && cmd` — a standalone assignment, so the next word is
            # a separator. Step over it or the label becomes `&&`.
            remaining = _SEPARATORS.sub("", remaining, count=1).strip() \
                if _SEPARATORS.match(remaining) else remaining
            continue

        if head not in WRAPPERS:
            # `/usr/bin/find` and `find` are the same command; grouping them
            # apart splits a label for no gain. Version-pinned plugin paths
            # make this worse — the same script under 1.70.4 and 1.71.4 read
            # as two tools.
            return head.rsplit("/", 1)[-1] if "/" in head else head

        if head in _INLINE_WRAPPERS:
            # Consumes its own arguments inline: drop the wrapper word, any
            # flags after it, and one positional operand where the wrapper
            # takes one (`timeout 300 foo`).
            rest = remaining[len(head):].strip()
            operand = _TAKES_OPERAND.get(head)
            while rest.startswith("-"):
                flag, _, rest = rest.partition(" ")
                rest = rest.strip()
                if not flag:
                    break
                # A separated flag value (`timeout -k 5 30 curl`) looks exactly
                # like the wrapper's own operand, so consume it here or the
                # label becomes a number.
                if "=" not in flag and operand:
                    nxt = rest.split(" ", 1)[0] if rest else ""
                    if nxt and operand.match(nxt):
                        rest = rest[len(nxt):].strip()
            if operand:
                first = rest.split(" ", 1)[0] if rest else ""
                if first and operand.match(first):
                    rest = rest[len(first):].strip()
            # `env | grep x` — the wrapper is being *run*, not wrapping
            # anything, so it is the command.
            if not rest or rest[0] in "|&;<>":
                return head          # also bare `sudo`
            remaining = rest
            continue

        # Separator-terminated (cd, source): skip to the next statement.
        match = _SEPARATORS.search(remaining)
        if not match:
            return head              # `cd /tmp` alone — that IS the command
        remaining = remaining[match.end():].strip()

    return remaining.split()[0] if remaining.split() else None


def _truncate(text: str | None) -> str | None:
    if text is None:
        return None
    text = " ".join(str(text).split())
    return text if len(text) <= MAX_DETAIL else text[: MAX_DETAIL - 1] + "…"


def normalize_tool(name: str | None, tool_input: dict | None) -> tuple[str, str | None]:
    """Split a tool call into (label, detail).

    The label is what you group by; the detail names the specific file,
    command, host or subagent. Detail is None where there is no second axis
    worth having.
    """
    args = tool_input if isinstance(tool_input, dict) else {}
    if not name:
        return "unknown", None

    mcp = re.match(r"^mcp__(.+?)__(.+)$", name)
    if mcp:
        return f"MCP {mcp.group(1)}/{mcp.group(2)}", None

    if name in ("Task", "Agent"):
        return f"Agent:{args.get('subagent_type') or 'unnamed'}", None

    if name == "Skill":
        skill = args.get("skill") or args.get("skill_name") or args.get("command")
        return f"Skill:{skill or 'unnamed'}", None

    if name in ("Read", "Edit", "Write", "NotebookEdit", "MultiEdit"):
        return name, _truncate(args.get("file_path") or args.get("notebook_path"))

    if name == "Bash":
        return "Bash", _truncate(bash_command(args.get("command") or ""))

    if name in ("WebFetch", "Fetch"):
        url = args.get("url") or ""
        host = re.sub(r"^[a-z]+://([^/]+).*$", r"\1", url, flags=re.I)
        return name, _truncate(host or None)

    if name == "Grep":
        return name, _truncate(args.get("pattern"))

    if name == "Glob":
        return name, _truncate(args.get("pattern"))

    return name, None


def tool_calls(entries: list[dict]) -> list[tuple[str, str | None]]:
    """Every tool call in the transcript as (label, detail), deduplicated.

    Deduplicated on the `tool_use` block id rather than via
    `merge_assistant_entries`, which drops entries lacking a `message.id`. A
    block with no id counts as its own call — collapsing those would hide real
    calls.
    """
    out: list[tuple[str, str | None]] = []
    seen: set = set()
    for entry in entries:
        if entry.get("type") != "assistant":
            continue
        for block in (entry.get("message") or {}).get("content") or []:
            if not isinstance(block, dict) or block.get("type") != "tool_use":
                continue
            block_id = block.get("id")
            if block_id is not None:
                if block_id in seen:
                    continue
                seen.add(block_id)
            out.append(normalize_tool(block.get("name"), block.get("input")))
    return out
