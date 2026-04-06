"""Command-line interface for deglacer.

Usage:
    deglacer SESSION.jsonl                  # conversation text
    deglacer --with-tools SESSION.jsonl     # include tool calls
    deglacer --with-thinking SESSION.jsonl  # include thinking blocks
    deglacer --last 5 SESSION.jsonl         # last 5 turns only
    deglacer --json SESSION.jsonl           # structured JSON output
    deglacer --stats SESSION.jsonl          # session statistics
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


def main():
    parser = argparse.ArgumentParser(
        prog="deglacer",
        description="Extract conversation from Claude Code session JSONL files.",
    )
    parser.add_argument("file", nargs="?", help="Session JSONL file path")
    parser.add_argument("--with-tools", action="store_true", help="Include tool calls")
    parser.add_argument("--with-thinking", action="store_true", help="Include thinking blocks")
    parser.add_argument("--last", type=int, metavar="N", help="Last N turns only")
    parser.add_argument("--json", action="store_true", dest="json_output", help="JSON output")
    parser.add_argument("--stats", action="store_true", help="Session statistics")
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

    # --today is sugar for --since today
    if args.today:
        args.since = datetime.now().strftime("%Y-%m-%d")
        if args.recent is None:
            args.recent = 100

    # List recent sessions
    if args.recent is not None:
        sessions = deglacer.find_sessions(limit=args.recent, since=args.since)
        for s in sessions:
            mtime = datetime.fromtimestamp(s["mtime"]).strftime("%Y-%m-%d %H:%M")
            size_kb = s["size"] / 1024
            slug = s.get("slug", "")
            sid = s.get("sessionId", "")[:8]
            print(f'{mtime}  {size_kb:8.0f}K  {sid}  {slug:30s}  {s["path"]}')
        return

    # --since without --recent: list sessions since date
    if args.since and args.recent is None and not args.find and not args.file:
        sessions = deglacer.find_sessions(limit=0, since=args.since)
        for s in sessions:
            mtime = datetime.fromtimestamp(s["mtime"]).strftime("%Y-%m-%d %H:%M")
            size_kb = s["size"] / 1024
            slug = s.get("slug", "")
            sid = s.get("sessionId", "")[:8]
            print(f'{mtime}  {size_kb:8.0f}K  {sid}  {slug:30s}  {s["path"]}')
        return

    # Search across sessions
    if args.find:
        results = deglacer.search_sessions(args.find)
        if not results:
            print(f'No matches for "{args.find}"', file=sys.stderr)
            sys.exit(1)
        for r in results:
            slug = r.get("slug", "")
            sid = r.get("sessionId", "")[:8]
            match = r["match"].replace("\n", " ")[:100]
            print(f'{sid}  {slug:25s}  "{match}"')
            print(f'  {r["file"]}')
        return

    # Need a file for everything else
    if not args.file:
        parser.print_help()
        sys.exit(1)

    if not os.path.exists(args.file):
        print(f"File not found: {args.file}", file=sys.stderr)
        sys.exit(1)

    entries = deglacer.parse_session(args.file)
    if not entries:
        print("Empty or unparseable file.", file=sys.stderr)
        sys.exit(1)

    if args.stats:
        print(deglacer.format_stats(entries))
    elif args.summary:
        print(deglacer.format_summary(entries))
    elif args.timeline:
        print(deglacer.format_timeline(entries))
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
