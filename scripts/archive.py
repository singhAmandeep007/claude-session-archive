#!/usr/bin/env python3
"""
Session Archive command line, used by the plugin's hooks and its /save skill.

Commands:
    archive.py hook                        read a hook payload on stdin (all hook events)
    archive.py save --session ID [--cwd D] archive one session now (folder defaults to the
                                           latest one recorded in the transcript)
    archive.py backfill                    archive every transcript still on disk
    archive.py index                       rebuild <archive>/README.md

Common option: --archive-dir DIR (default: see session_archive.config.archive_root).

Examples:
    python3 archive.py save --session "$CLAUDE_SESSION_ID"
    -> saved 12 new messages (148 total) to ~/claude-archive/dotfiles/2026-09-23_1022_b9230413

Hook mode always exits 0 so an archiving problem can never block a turn; the
error is appended to <archive>/_logs/errors.log instead of being lost.
"""

from __future__ import annotations

import argparse
import json
import sys
import traceback
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from session_archive import sync  # noqa: E402
from session_archive.config import archive_root, remember_plugin_option  # noqa: E402
from session_archive.fsutil import file_lock  # noqa: E402
from session_archive.index import rebuild_index  # noqa: E402

FULL_SYNC_EVENTS = {"Stop", "SubagentStop", "PreCompact", "SessionEnd"}


# -----------------------------------------------------------------------------
# Utilities
# -----------------------------------------------------------------------------

def log_error(root: Path, context: str) -> None:
    """Append the current exception to <archive>/_logs/errors.log."""
    try:
        log = root / "_logs" / "errors.log"
        log.parent.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().astimezone().isoformat(timespec="seconds")
        with log.open("a", encoding="utf-8") as fh:
            fh.write(f"--- {stamp} {context}\n{traceback.format_exc()}\n")
    except OSError:
        pass


def recent_errors(root: Path, limit: int = 3) -> list[str]:
    """Headlines of the most recent logged errors (for the save command's report)."""
    log = root / "_logs" / "errors.log"
    if not log.is_file():
        return []
    heads = [line[4:] for line in log.read_text(encoding="utf-8").splitlines() if line.startswith("--- ")]
    return heads[-limit:]


# -----------------------------------------------------------------------------
# Commands
# -----------------------------------------------------------------------------

def cmd_hook(root: Path) -> int:
    """Dispatch one hook event read from stdin."""
    event = "unknown"
    try:
        remember_plugin_option()
        payload = json.load(sys.stdin)
        event = payload.get("hook_event_name", "unknown")
        if event == "PostToolUse":
            sync.capture_tool(root, payload)
        elif event in FULL_SYNC_EVENTS:
            transcript = Path(payload.get("transcript_path") or "")
            if transcript.is_file() and payload.get("session_id"):
                sync.sync_session(root, payload["session_id"], transcript, payload.get("cwd"))
    except Exception:  # never block a turn; keep the evidence instead
        log_error(root, f"hook {event}")
    return 0


def cmd_save(root: Path, session_id: str, cwd: str | None) -> int:
    """Archive one session and report what was new."""
    transcript = sync.find_transcript(session_id)
    if transcript is None:
        print(f"session-archive: no transcript found for session {session_id}", file=sys.stderr)
        return 1
    result = sync.sync_session(root, session_id, transcript, cwd)
    print(f"saved {result.new_messages} new messages ({result.total_messages} total) to {result.folder}")
    for line in recent_errors(root):
        print(f"note: earlier hook error logged at {line} (see {root / '_logs' / 'errors.log'})")
    return 0


def cmd_backfill(root: Path) -> int:
    """Archive every transcript Claude Code still keeps, oldest first."""
    done = failed = 0
    for session_id, transcript in sync.all_transcripts():
        try:
            sync.sync_session(root, session_id, transcript, cwd=None)
            done += 1
        except Exception:
            log_error(root, f"backfill {session_id}")
            failed += 1
    print(f"backfilled {done} sessions into {root}" + (f" ({failed} failed, see _logs/errors.log)" if failed else ""))
    return 1 if failed else 0


def cmd_index(root: Path) -> int:
    with file_lock(root / ".locks" / "index.lock"):
        count = rebuild_index(root)
    print(f"indexed {count} sessions in {root / 'README.md'}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Permanent archive of Claude Code sessions.")
    parser.add_argument("--archive-dir", help="archive root (overrides env and plugin option)")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("hook", help="handle a hook payload from stdin")
    save = sub.add_parser("save", help="archive one session now")
    save.add_argument("--session", required=True)
    save.add_argument("--cwd")
    sub.add_parser("backfill", help="archive every transcript still on disk")
    sub.add_parser("index", help="rebuild README.md")
    args = parser.parse_args(argv)

    root = archive_root(args.archive_dir)
    if args.command == "hook":
        return cmd_hook(root)
    try:
        sync.ensure_root(root)
        if args.command == "save":
            return cmd_save(root, args.session, args.cwd)
        if args.command == "backfill":
            return cmd_backfill(root)
        return cmd_index(root)
    except Exception as exc:
        log_error(root, args.command)
        print(f"session-archive: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
