"""Parse health — does the parser still fit the format it is reading?

The CC JSONL schema moves with Claude Code releases and nothing announces a
new entry type, a renamed field, or a changed shape. A parser that has drifted
does not raise: it quietly reports smaller or larger numbers, and every
downstream figure inherits the error.

That is not hypothetical here. `--stats` summed `message.usage` over raw
assistant entries for months, double-counting every streamed response by 2.3x,
and nothing in the output looked wrong (dgc-tenoze). The check that would have
caught it on day one is the dedupe count: if collapsing entries by requestId
removes *nothing* on a real transcript, the key has stopped matching and every
total is inflated.

So the counters here are not diagnostics for a curious user — they are
tripwires for a format that changes underneath us. Each one is written so that
the healthy state is a specific number and drift is a different number, rather
than the usual arrangement where drift looks like silence.
"""

from collections import Counter

from deglacer.parsing import dedupe_by_request

# A transcript this short can legitimately contain no streamed response, so the
# zero-duplicates alarm would fire on a fluke rather than on drift.
MIN_ASSISTANT_ENTRIES_FOR_DUPE_CHECK = 10

# A few entries without a requestId are synthetic responses; most of them means
# the field itself has gone. Set from a real drift run: renaming requestId on a
# live transcript reported "492 assistant entries carry no requestId" as
# EXPECTED, directly under the flag saying the key had stopped matching. A
# reassuring row beside an alarm about the same cause is worse than no row.
MOSTLY_MISSING_REQUEST_ID = 0.5

USAGE_KEYS = (
    "input_tokens",
    "cache_creation_input_tokens",
    "cache_read_input_tokens",
    "output_tokens",
)


def new_health() -> Counter:
    """A fresh line-level accumulator to pass to `parse_session(health=...)`."""
    return Counter()


def assess(entries: list[dict], health: Counter | None = None) -> dict:
    """Measure how well the parser fits this transcript.

    `health` is the line-level Counter accumulated during `parse_session`
    (lines, blank, bad_json, not_object). Pass it when you have it; the
    entry-level findings below are computed from `entries` either way, so the
    assessment degrades to "everything except the line counts" without it.
    """
    health = health if health is not None else Counter()

    assistant = [e for e in entries if e.get("type") == "assistant"]
    requests = dedupe_by_request(assistant)

    no_request_id = sum(1 for e in assistant if not e.get("requestId"))
    zero_usage = 0
    for entry in requests:
        usage = (entry.get("message") or {}).get("usage") or {}
        if not any(usage.get(k) for k in USAGE_KEYS):
            zero_usage += 1

    return {
        "lines": health.get("lines", 0),
        "blank": health.get("blank", 0),
        "bad_json": health.get("bad_json", 0),
        "not_object": health.get("not_object", 0),
        "entries": len(entries),
        "entry_types": Counter(e.get("type") for e in entries),
        "assistant_entries": len(assistant),
        "api_requests": len(requests),
        "duplicates_collapsed": len(assistant) - len(requests),
        "no_request_id": no_request_id,
        "zero_usage_requests": zero_usage,
    }


def findings(h: dict) -> list[tuple[str, str, str]]:
    """Turn an assessment into (severity, headline, detail) rows.

    Severity is `flag` or `ok`. An `ok` row is not filler: "duplicates were
    collapsed" is the evidence that the dedupe key still matches, and it is
    worth printing precisely because its absence is the thing to notice.
    """
    out: list[tuple[str, str, str]] = []

    if h["bad_json"]:
        out.append((
            "flag",
            f'{h["bad_json"]} line(s) were not valid JSON',
            "Skipped, so anything they carried is missing from every count "
            "below. A handful can mean a truncated write; many mean this is "
            "not a CC transcript, or the format has changed.",
        ))

    if h["not_object"]:
        out.append((
            "flag",
            f'{h["not_object"]} line(s) parsed but were not JSON objects',
            "Every CC entry is an object. Bare arrays or scalars mean the "
            "file is not what this parser expects.",
        ))

    if h["assistant_entries"] < MIN_ASSISTANT_ENTRIES_FOR_DUPE_CHECK:
        out.append((
            "ok",
            f'{h["assistant_entries"]} assistant entries — too few to test the '
            "dedupe key",
            "A short transcript can honestly contain no streamed response, so "
            "a zero here would not distinguish drift from a small sample.",
        ))
    elif h["duplicates_collapsed"] == 0:
        out.append((
            "flag",
            "NO duplicate entries collapsed — the dedupe key may have stopped "
            "matching",
            f'{h["assistant_entries"]} assistant entries produced '
            f'{h["api_requests"]} requests, collapsing none. CC writes one API '
            "response as one entry per content block, all sharing a requestId, "
            "so a real transcript of this size almost always has some. If the "
            "key has drifted, every token and model count is inflated — the "
            "exact failure dgc-tenoze fixed (2.3x on one session). Check "
            "whether assistant entries still carry `requestId`.",
        ))
    else:
        pct = 100 * h["duplicates_collapsed"] / max(h["assistant_entries"], 1)
        out.append((
            "ok",
            f'{h["duplicates_collapsed"]} duplicate entries collapsed '
            f"({pct:.0f}% of assistant entries)",
            f'{h["assistant_entries"]} entries represent '
            f'{h["api_requests"]} API requests. Summing usage per entry would '
            "have overcounted by "
            f'{100 * h["assistant_entries"] / max(h["api_requests"], 1) - 100:.0f}%.',
        ))

    if h["no_request_id"]:
        share = h["no_request_id"] / max(h["assistant_entries"], 1)
        if share >= MOSTLY_MISSING_REQUEST_ID:
            out.append((
                "flag",
                f'{100 * share:.0f}% of assistant entries carry no requestId '
                f'({h["no_request_id"]} of {h["assistant_entries"]})',
                "A few means synthetic or local responses, which is normal. "
                "This many means the field is gone or renamed — so requests "
                "cannot be identified, dedupe falls back to message.id, and "
                "the billing lane cannot be read at all. Check what assistant "
                "entries actually carry.",
            ))
        else:
            out.append((
                "ok",
                f'{h["no_request_id"]} assistant entries carry no requestId',
                "Expected: synthetic and local responses make no API call. "
                "They are kept distinct rather than collapsed together, so "
                "they still count once each.",
            ))

    if h["zero_usage_requests"]:
        out.append((
            "ok",
            f'{h["zero_usage_requests"]} request(s) report zero usage',
            "Usually synthetic responses or an interrupted turn. Many, on a "
            "busy transcript, would instead suggest the usage block moved.",
        ))

    if not h["entries"]:
        out.append((
            "flag",
            "No entries parsed at all",
            "Empty file, or nothing in it was readable as JSONL.",
        ))
    elif not h["assistant_entries"]:
        types = ", ".join(f"{t}({n})" for t, n in h["entry_types"].most_common(4))
        out.append((
            "flag",
            f'No assistant entries — parsed {h["entries"]} entries of other types',
            f"Present instead: {types}. Three ordinary causes before you "
            "suspect drift: this is not a conversation transcript at all (a "
            "workflow journal.jsonl carries started/result and nothing else), "
            "the session was abandoned before the model replied, or the file "
            "is still being written. Only if it looks like a real conversation "
            'does this mean `.type == "assistant"` has stopped identifying '
            "model responses.",
        ))

    return out


def format_doctor(h: dict) -> str:
    """Render an assessment: counts, then findings, flags first."""
    lines = [
        "Parse health",
        "",
        f'  lines read           : {h["lines"]:,}'
        + (f' ({h["blank"]:,} blank)' if h["blank"] else ""),
        f'  entries parsed       : {h["entries"]:,}',
        f'  bad JSON             : {h["bad_json"]:,}',
        f'  non-object lines     : {h["not_object"]:,}',
        "",
        f'  assistant entries    : {h["assistant_entries"]:,}',
        f'  API requests         : {h["api_requests"]:,}',
        f'  duplicates collapsed : {h["duplicates_collapsed"]:,}',
        "",
        "Entry types:",
    ]
    for entry_type, count in h["entry_types"].most_common():
        lines.append(f"  {str(entry_type):25s} {count:5d}")

    rows = findings(h)
    if rows:
        lines.extend(["", "Findings:"])
        for severity, headline, detail in sorted(
            rows, key=lambda r: 0 if r[0] == "flag" else 1
        ):
            marker = "[!]" if severity == "flag" else "[ok]"
            lines.append(f"  {marker} {headline}")
            for wrapped in _wrap(detail, 68):
                lines.append(f"      {wrapped}")

    return "\n".join(lines)


def _wrap(text: str, width: int) -> list[str]:
    out, current = [], ""
    for word in text.split():
        if current and len(current) + 1 + len(word) > width:
            out.append(current)
            current = word
        else:
            current = f"{current} {word}" if current else word
    if current:
        out.append(current)
    return out
