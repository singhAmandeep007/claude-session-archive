"""
Configuration: where the archive and Claude Code's data live, and global limits.

Resolution order for the archive root (first non-empty wins):

    1. $SESSION_ARCHIVE_DIR              (override for tests and one-off runs)
    2. an explicit path passed by the CLI (--archive-dir)
    3. $CLAUDE_PLUGIN_OPTION_ARCHIVE_DIR  (the plugin's "Archive folder" option;
       only hook processes receive it)
    4. the folder hooks last used, remembered in ~/.config/session-archive/archive-dir,
       so the /save skill (which runs outside a hook) writes to the same place
    5. ~/claude-archive

Example:
    >>> archive_root("~/tmp/arch")
    PosixPath('/Users/me/tmp/arch')
"""

from __future__ import annotations

import os
from pathlib import Path

DEFAULT_ARCHIVE_DIR = "~/claude-archive"

# Tools whose target file Claude wrote or published: every version is kept.
WRITE_TOOLS = frozenset({"Write", "Edit", "MultiEdit", "NotebookEdit", "Artifact"})

# Files shown in the chat are kept too: media Claude opened with Read (screenshots
# from a device or browser usually live in /tmp and vanish) and files sent with SendUserFile.
MEDIA_EXTENSIONS = frozenset({".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg", ".pdf", ".mp4", ".mov", ".webm"})

# Files larger than this are recorded in versions.jsonl but not copied.
MAX_SNAPSHOT_BYTES = 25 * 1024 * 1024

# A Stop or SubagentStop sync is skipped when the session was saved less than this many
# minutes ago: cloud-synced folders (Google Drive, iCloud) re-upload whole files, and a raw
# transcript can be 100 MB. Session end, compaction, /session-archive:save and file
# snapshots always run. Override with the plugin option or SESSION_ARCHIVE_MIN_SYNC_MINUTES.
DEFAULT_MIN_SYNC_MINUTES = 10

# Seconds to wait for another process holding a session or index lock.
LOCK_TIMEOUT_SECONDS = 60


def _usable(value: str | None) -> bool:
    """True for a real path; false for empty values or an unsubstituted ${...} placeholder."""
    return bool(value) and not value.strip().startswith("${")


def _pointer_file() -> Path:
    return Path(os.path.expanduser(os.environ.get("XDG_CONFIG_HOME") or "~/.config")) / "session-archive" / "archive-dir"


def _remembered() -> str | None:
    try:
        return _pointer_file().read_text(encoding="utf-8").strip()
    except OSError:
        return None


def archive_root(explicit: str | None = None) -> Path:
    """Resolve the archive root directory (not created here)."""
    for candidate in (
        os.environ.get("SESSION_ARCHIVE_DIR"),
        explicit,
        os.environ.get("CLAUDE_PLUGIN_OPTION_ARCHIVE_DIR"),
        _remembered(),
        DEFAULT_ARCHIVE_DIR,
    ):
        if _usable(candidate):
            return Path(os.path.expanduser(candidate.strip())).resolve()
    raise AssertionError("unreachable: DEFAULT_ARCHIVE_DIR is always usable")


def remember_plugin_option() -> None:
    """
    Persist the plugin's configured folder for non-hook callers (the /save skill).

    Called by hooks; writes only when the option is set and has changed.
    """
    option = os.environ.get("CLAUDE_PLUGIN_OPTION_ARCHIVE_DIR")
    if not _usable(option) or _remembered() == option.strip():
        return
    pointer = _pointer_file()
    pointer.parent.mkdir(parents=True, exist_ok=True)
    pointer.write_text(option.strip() + "\n", encoding="utf-8")


def min_sync_minutes() -> float:
    """
    Minimum minutes between automatic after-reply syncs (0 = sync after every reply).

    Example: SESSION_ARCHIVE_MIN_SYNC_MINUTES=0 -> 0.0
    """
    for raw in (os.environ.get("SESSION_ARCHIVE_MIN_SYNC_MINUTES"), os.environ.get("CLAUDE_PLUGIN_OPTION_MIN_SYNC_MINUTES")):
        if _usable(raw):
            try:
                return max(0.0, float(raw))
            except ValueError:
                pass
    return float(DEFAULT_MIN_SYNC_MINUTES)


def machine_name() -> str:
    """
    This computer's stable name: macOS LocalHostName, else the hostname.

    Example: "Amandeeps-MacBook-M3-Pro"
    """
    import socket
    import subprocess
    try:
        out = subprocess.run(["scutil", "--get", "LocalHostName"], capture_output=True, text=True, timeout=5).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        out = ""
    return out or socket.gethostname().split(".")[0]


def claude_account() -> str | None:
    """
    The signed-in Claude account's email (never a token), or None.

    Claude Code keeps it in .claude.json: inside $CLAUDE_CONFIG_DIR when that is set, else in the home folder.
    """
    import json
    folder = os.environ.get("CLAUDE_CONFIG_DIR")
    path = Path(os.path.expanduser(folder)) / ".claude.json" if folder else Path.home() / ".claude.json"
    try:
        account = json.loads(path.read_text(encoding="utf-8")).get("oauthAccount") or {}
    except (OSError, ValueError):
        return None
    return account.get("emailAddress")


def claude_home() -> Path:
    """
    Claude Code's data directory: $CLAUDE_CONFIG_DIR or ~/.claude.

    Example: claude_home() / "projects" holds one folder of transcripts per project path.
    """
    return Path(os.path.expanduser(os.environ.get("CLAUDE_CONFIG_DIR") or "~/.claude"))
