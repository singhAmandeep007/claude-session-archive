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

# Tools whose output is a file worth versioning. The hook matcher mirrors this set.
SNAPSHOT_TOOLS = frozenset({"Write", "Edit", "MultiEdit", "NotebookEdit", "Artifact"})

# Files larger than this are recorded in versions.jsonl but not copied.
MAX_SNAPSHOT_BYTES = 25 * 1024 * 1024

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


def claude_home() -> Path:
    """
    Claude Code's data directory: $CLAUDE_CONFIG_DIR or ~/.claude.

    Example: claude_home() / "projects" holds one folder of transcripts per project path.
    """
    return Path(os.path.expanduser(os.environ.get("CLAUDE_CONFIG_DIR") or "~/.claude"))
