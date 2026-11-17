#!/usr/bin/env python3
"""
Session Archive command line, used by the plugin's hooks and its slash commands.

Commands:
    archive.py hook                        read a hook payload on stdin (all hook events)
    archive.py save --session ID [--cwd D] archive one session now (folder defaults to the
                                           latest one recorded in the transcript)
    archive.py backfill                    archive every transcript still on disk
    archive.py import [ID] [--list] [--from DIR]
                                           put archived sessions back into Claude Code
                                           (--from first archives a backup folder)
    archive.py index                       rebuild <archive>/README.md

The /session-archive:save and /session-archive:import commands cost zero tokens:
a UserPromptExpansion hook runs them itself and blocks the command before it
reaches the model, showing this script's report as the block reason.

    /session-archive:save          save this chat now
    /session-archive:save all      archive every transcript still on disk (backfill)
    /session-archive:import [...]  see `import` above

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
import shlex
import sys
import time
import traceback
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from session_archive import restore, sync  # noqa: E402
from session_archive.config import archive_root, claude_home, min_sync_minutes, remember_plugin_option  # noqa: E402
from session_archive.fsutil import file_lock  # noqa: E402
from session_archive.index import rebuild_index  # noqa: E402
from session_archive.locate import find_session_dir  # noqa: E402

FULL_SYNC_EVENTS = {"Stop", "SubagentStop", "PreCompact", "SessionEnd"}
THROTTLED_EVENTS = {"Stop", "SubagentStop"}  # after-reply syncs; see config.DEFAULT_MIN_SYNC_MINUTES
IMPORT_COMMAND = "session-archive:import"
SAVE_COMMAND = "session-archive:save"


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


def recently_saved(root: Path, session_id: str) -> bool:
    """
    True when this session's folder was written less than min_sync_minutes() ago.

    meta.json is rewritten on every sync that found something new, so its age is the
    time since the last real save. Example: saved 3 minutes ago, limit 10 -> True.
    """
    limit = min_sync_minutes()
    folder = find_session_dir(root, session_id) if limit else None
    meta = folder / "meta.json" if folder else None
    return bool(meta and meta.exists() and time.time() - meta.stat().st_mtime < limit * 60)


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
        if event == "UserPromptExpansion" and payload.get("command_name") in (IMPORT_COMMAND, SAVE_COMMAND):
            return block_with(run_slash_command(root, payload))
        if event == "PostToolUse":
            sync.capture_tool(root, payload)
        elif event in FULL_SYNC_EVENTS:
            transcript = Path(payload.get("transcript_path") or "")
            session_id = payload.get("session_id")
            if transcript.is_file() and session_id:
                if event in THROTTLED_EVENTS and recently_saved(root, session_id):
                    return 0
                sync.sync_session(root, session_id, transcript, payload.get("cwd"))
    except Exception as exc:  # never block a turn; keep the evidence instead
        log_error(root, f"hook {event}")
        if event == "UserPromptExpansion":
            return block_with(f"failed: {exc} (details in {root / '_logs' / 'errors.log'})")
    return 0


def block_with(message: str) -> int:
    """Stop a slash command before it reaches the model, showing `message` instead."""
    print(json.dumps({"decision": "block", "reason": f"session-archive {message}"}))
    return 0


def save_report(root: Path, session_id: str, transcript: Path | None = None, cwd: str | None = None) -> str:
    """
    Archive one session now; return what was new.

    Example: "saved 12 new messages (148 total) to ~/claude-archive/dotfiles/2026-09-23_1022_b9230413"
    Raises LookupError when Claude Code has no transcript for the session.
    """
    transcript = transcript or sync.find_transcript(session_id)
    if transcript is None:
        raise LookupError(f"no transcript found for session {session_id}")
    result = sync.sync_session(root, session_id, transcript, cwd)
    lines = [f"saved {result.new_messages} new messages ({result.total_messages} total) to {result.folder}"]
    lines += [f"note: earlier hook error logged at {line} (see {root / '_logs' / 'errors.log'})"
              for line in recent_errors(root)]
    return "\n".join(lines)


def backfill_report(root: Path) -> tuple[str, bool]:
    """Archive every transcript Claude Code still keeps, oldest first; return (report, ok)."""
    done = failed = 0
    for session_id, transcript in sync.all_transcripts():
        try:
            sync.sync_session(root, session_id, transcript, cwd=None)
            done += 1
        except Exception:
            log_error(root, f"backfill {session_id}")
            failed += 1
    report = f"backfilled {done} sessions into {root}" + (f" ({failed} failed, see _logs/errors.log)" if failed else "")
    return report, not failed


def run_slash_command(root: Path, payload: dict) -> str:
    """
    Answer /session-archive:save or /session-archive:import from a UserPromptExpansion payload.

    The payload carries the live session, so `save` needs no arguments:
        {"command_name": "session-archive:save", "command_args": "", "session_id": "...",
         "transcript_path": "...", "cwd": "..."}
    """
    args = (payload.get("command_args") or "").strip()
    if payload.get("command_name") == IMPORT_COMMAND:
        return run_import(root, args)
    if args == "all":
        return backfill_report(root)[0]
    if args:
        return "save: usage: /session-archive:save [all]"
    transcript = Path(payload.get("transcript_path") or "")
    try:
        return save_report(root, payload["session_id"], transcript if transcript.is_file() else None, payload.get("cwd"))
    except LookupError:
        return "save: nothing to save yet, this chat has no messages"


def cmd_save(root: Path, session_id: str, cwd: str | None) -> int:
    try:
        print(save_report(root, session_id, cwd=cwd))
    except LookupError as exc:
        print(f"session-archive: {exc}", file=sys.stderr)
        return 1
    return 0


def cmd_backfill(root: Path) -> int:
    report, ok = backfill_report(root)
    print(report)
    return 0 if ok else 1


def run_import(root: Path, args: str | list[str]) -> str:
    """
    Shared by the CLI and the zero-token hook. Returns the report text.

    Examples of args: "", "--list", "75cb6e36", "--from /Volumes/backup/.claude"
    """
    parser = argparse.ArgumentParser(prog="/session-archive:import", add_help=False, exit_on_error=False)
    parser.add_argument("session", nargs="?", help="session id or its first characters")
    parser.add_argument("--list", action="store_true", help="only show what is missing")
    parser.add_argument("--from", dest="source", help="archive this backup folder first")
    try:
        opts = parser.parse_args(shlex.split(args) if isinstance(args, str) else args)
    except (argparse.ArgumentError, SystemExit, ValueError):
        return "import: usage: /session-archive:import [session-id] [--list] [--from <folder>]"

    prefix = ""
    if opts.source:
        found = sync.transcripts_in(Path(opts.source))
        for session_id, transcript, claude_dir in found:
            sync.sync_session(root, session_id, transcript, cwd=None, claude_dir=claude_dir)
        prefix = f"archived {len(found)} sessions from {opts.source}\n"
    results = restore.restore(root, claude_home(), opts.session, dry_run=opts.list)
    if opts.session and not results:
        return prefix + f"import: no archived session starts with {opts.session!r}"
    return prefix + restore.format_report(results, list_only=opts.list)


def cmd_import(root: Path, args: list[str]) -> int:
    print(run_import(root, args))
    return 0


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
    sub.add_parser("import", help="put archived sessions back into Claude Code "
                                  "([session-id] [--list] [--from DIR])")
    # import's own options (--list, --from) are parsed by run_import, shared with the hook.
    args, extra = parser.parse_known_args(argv)
    if extra and args.command != "import":
        parser.error(f"unrecognized arguments: {' '.join(extra)}")

    root = archive_root(args.archive_dir)
    if args.command == "hook":
        return cmd_hook(root)
    try:
        sync.ensure_root(root)
        if args.command == "save":
            return cmd_save(root, args.session, args.cwd)
        if args.command == "backfill":
            return cmd_backfill(root)
        if args.command == "import":
            return cmd_import(root, extra)
        return cmd_index(root)
    except Exception as exc:
        log_error(root, args.command)
        print(f"session-archive: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
