"""Command-line interface for deglacer.

Usage:
    deglacer SESSION.jsonl                  # conversation text
    deglacer --with-tools SESSION.jsonl     # include tool calls
    deglacer --with-thinking SESSION.jsonl  # include thinking blocks
    deglacer --last 5 SESSION.jsonl         # last 5 turns only
    deglacer --json SESSION.jsonl           # structured JSON output
    deglacer --markdown SESSION.jsonl       # claude.ai-export-style markdown
    deglacer --stats SESSION.jsonl          # session statistics, incl. billing lane
    deglacer --doctor SESSION.jsonl         # does the parser still fit the format?
    deglacer --stats --tools SESSION.jsonl  # + which file/command/host each call went to
    deglacer --summary SESSION.jsonl        # human messages only
    deglacer --timeline SESSION.jsonl       # timestamped turn log
    deglacer --find "search term"           # search across recent sessions
    deglacer --recent                       # list recent sessions
    deglacer --recent 10                    # list 10 most recent
    deglacer --today                        # list today's sessions
"""

import argparse
import os
import sys
from datetime import datetime

import deglacer
from deglacer import _invlog


def main():
    """Entry point: invocation logging around the real main.

    Every invocation — success and failure alike — appends one caller-stamped
    JSONL line via the vendored shim (src/deglacer/_invlog.py; canonical copy
    and cross-estate conformance test live in spm1001/harness-ergonomics).
    Logging is best-effort: a broken log path never breaks the CLI (erg-tebapi).
    """
    with _invlog.capture("deglacer", deglacer.__version__) as inv:
        _main(inv)


def _mode(args) -> str:
    """The REQUESTED mode, tested in dispatch order.

    deglacer has no subcommands — its modes are flags — so this derived mode
    is what goes in the log's subcommand field. A null there would gut
    per-mode analysis, which is the whole point of the field. On error paths
    (e.g. --stats with no file) the logged mode is what was asked for, not
    the help-and-exit branch actually taken; outcome=error carries the rest —
    and the conformance test depends on exactly that reading.
    """
    if args.recent is not None:
        return "recent"
    if args.since and not args.find and not args.file:
        return "since"
    if args.find:
        return "find"
    if args.doctor:
        return "doctor"
    if args.stats:
        return "stats"
    if args.summary:
        return "summary"
    if args.timeline:
        return "timeline"
    if args.markdown:
        return "markdown"
    if args.json_output:
        return "json"
    return "text"


def _print_session_list(sessions):
    """One line per session: mtime, size, id prefix, title, path.

    The title column carries the transcript's ai-title (what /resume shows),
    falling back to its last prompt; a session with neither prints
    ``(no title)`` — the old slug column was blank on every current session
    (dgc-lubeku).
    """
    for s in sessions:
        mtime = datetime.fromtimestamp(s["mtime"]).strftime("%Y-%m-%d %H:%M")
        size_kb = s["size"] / 1024
        title = s.get("title") or s.get("slug") or "(no title)"
        sid = s.get("sessionId", "")[:8]
        print(f'{mtime}  {size_kb:8.0f}K  {sid}  {title[:40]:40s}  {s["path"]}')


def _main(inv):
    parser = argparse.ArgumentParser(
        prog="deglacer",
        description="Extract conversation from Claude Code session JSONL files.",
    )
    parser.add_argument("file", nargs="*", help="Session JSONL file path (one file per run)")
    parser.add_argument(
        "--version", action="version", version=f"deglacer {deglacer.__version__}",
    )
    parser.add_argument("--with-tools", action="store_true", help="Include tool calls")
    parser.add_argument("--with-thinking", action="store_true", help="Include thinking blocks")
    parser.add_argument("--last", type=int, metavar="N", help="Last N turns only")
    parser.add_argument("--json", action="store_true", dest="json_output", help="JSON output")
    parser.add_argument("--markdown", action="store_true", help="Claude.ai-export-style markdown (suggested filename to stderr)")
    parser.add_argument("--stats", action="store_true", help="Session statistics")
    parser.add_argument(
        "--tools", action="store_true",
        help="With --stats: break tool counts down by file, command or host",
    )
    parser.add_argument(
        "--doctor", action="store_true",
        help="Parse health: does the parser still fit the format? "
             "Exits 1 if anything is flagged.",
    )
    parser.add_argument("--summary", action="store_true", help="Human messages only (what was discussed)")
    parser.add_argument("--timeline", action="store_true", help="Timestamped turn log")
    parser.add_argument(
        "--recent", nargs="?", type=int, const=20, metavar="N",
        help="List N most recent sessions (default 20)",
    )
    parser.add_argument("--today", action="store_true", help="List today's sessions")
    parser.add_argument("--since", type=str, metavar="DATE", help="Sessions since DATE (YYYY-MM-DD)")
    parser.add_argument("--find", type=str, metavar="TERM", help="Search across sessions")

    args = parser.parse_args()

    # A glob that matched several files is the commonest way to arrive here
    # (measured: the one --markdown error in the corpus, 2026-05-16). Refuse
    # with the loop the caller wanted rather than argparse's bare "unrecognized
    # arguments", which reads as a broken flag.
    if len(args.file) > 1:
        print(
            f"deglacer reads one session file per run; got {len(args.file)}.\n"
            f"  for f in <glob>; do deglacer <flags> \"$f\"; done",
            file=sys.stderr,
        )
        sys.exit(2)
    args.file = args.file[0] if args.file else None

    # --today is sugar for --since today
    if args.today:
        args.since = datetime.now().strftime("%Y-%m-%d")
        if args.recent is None:
            args.recent = 100

    # Note post-normalisation (after the --today sugar), so the logged mode
    # matches the branch actually dispatched below.
    inv.note(subcommand=_mode(args), parsed=args)

    # List recent sessions
    if args.recent is not None:
        _print_session_list(deglacer.find_sessions(limit=args.recent, since=args.since))
        return

    # --since without --recent: list sessions since date
    if args.since and args.recent is None and not args.find and not args.file:
        _print_session_list(deglacer.find_sessions(limit=0, since=args.since))
        return

    # Search across sessions
    if args.find:
        # --since widens the window to every session since that date; without
        # it the scan is the DEFAULT_SEARCH_WINDOW most-recent files. Either
        # way the scope is printed, because an empty result from a scoped
        # scan is not "never discussed" (dgc-nidobe).
        window = 0 if args.since else deglacer.discovery.DEFAULT_SEARCH_WINDOW
        limit = deglacer.discovery.DEFAULT_SEARCH_LIMIT
        results = deglacer.search_sessions(args.find, limit=limit, window=window, since=args.since)
        total = deglacer.discovery.count_sessions()
        scope = (f"every session since {args.since}" if args.since
                 else f"the {min(window, total)} most-recent sessions of {total}")
        if not results:
            print(f'No matches for "{args.find}" in {scope} — '
                  f'widen with --since DATE before reading this as never discussed',
                  file=sys.stderr)
            sys.exit(1)
        for r in results:
            title = r.get("title") or r.get("slug") or ""
            sid = r.get("sessionId", "")[:8]
            match = r["match"].replace("\n", " ")[:100]
            print(f'{sid}  {title[:25]:25s}  "{match}"')
            print(f'  {r["file"]}')
        capped = " (capped — narrow the term or add --since)" if len(results) >= limit else ""
        print(f"searched {scope}; {len(results)} shown{capped}", file=sys.stderr)
        return

    # Need a file for everything else
    if not args.file:
        parser.print_help()
        sys.exit(1)

    if not os.path.exists(args.file):
        print(f"File not found: {args.file}", file=sys.stderr)
        sys.exit(1)

    health = deglacer.new_health()
    entries = deglacer.parse_session(args.file, health=health)

    # --doctor runs before the empty-file guard on purpose: a file that parsed
    # to nothing is exactly the case it exists to explain, and bailing with
    # "unparseable" would withhold the line counts that say why.
    if args.doctor:
        assessment = deglacer.assess(entries, health)
        print(deglacer.format_doctor(assessment))
        flagged = [f for f in deglacer.findings(assessment) if f[0] == "flag"]
        sys.exit(1 if flagged else 0)

    if not entries:
        print("Empty or unparseable file.", file=sys.stderr)
        sys.exit(1)

    if args.stats:
        print(deglacer.format_stats(entries, with_details=args.tools))
    elif args.summary:
        print(deglacer.format_summary(entries))
    elif args.timeline:
        print(deglacer.format_timeline(entries))
    elif args.markdown:
        md, suggested = deglacer.format_markdown(
            entries,
            with_thinking=args.with_thinking,
            last_n=args.last,
        )
        print(md)
        print(f"Suggested filename: {suggested}", file=sys.stderr)
    elif args.json_output:
        turns = deglacer.build_turns(
            entries,
            with_tools=args.with_tools,
            with_thinking=args.with_thinking,
            last_n=args.last,
        )
        print(deglacer.format_json(turns))
    else:
        turns = deglacer.build_turns(
            entries,
            with_tools=args.with_tools,
            with_thinking=args.with_thinking,
            last_n=args.last,
        )
        print(deglacer.format_text(turns))


if __name__ == "__main__":
    main()
