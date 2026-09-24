"""
Locate things: which project a session belongs to and where its folder lives.

Layout (one folder per session, grouped by project):

    <archive>/<project>/<YYYY-MM-DD_HHMM>_<session-id-8>/

Rules that keep exactly one folder per session:
    - A session is found by its short id plus the full id in meta.json.
    - A session first seen outside any repo lives under "_no-repo"; once it
      runs inside a repo, its folder moves there. After that it stays put,
      so hopping between repos never splits or duplicates a session.
"""

from __future__ import annotations

import re
import subprocess
from datetime import datetime
from pathlib import Path

from .fsutil import read_json

NO_REPO = "_no-repo"
_UNSAFE = re.compile(r"[^A-Za-z0-9._-]+")


# -----------------------------------------------------------------------------
# Projects
# -----------------------------------------------------------------------------

def repo_root(cwd: str) -> Path | None:
    """
    Main repository root for `cwd`, or None outside git.

    Uses --git-common-dir so a linked worktree resolves to its main checkout.
    Example: ~/proj/.claude/worktrees/feat-x  ->  ~/proj
    """
    try:
        out = subprocess.run(
            ["git", "-C", cwd, "rev-parse", "--path-format=absolute", "--git-common-dir"],
            capture_output=True, text=True, timeout=5,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if out.returncode != 0:
        return None
    common = Path(out.stdout.strip())
    return common.parent if common.name == ".git" else None


def safe_name(name: str) -> str:
    """Filesystem-safe folder name. Example: 'my app (v2)' -> 'my-app-v2'."""
    return _UNSAFE.sub("-", name).strip("-") or "unnamed"


def project_name(cwd: str | None, git_branch_hint: str | None = None) -> str:
    """
    Project folder name for a working directory.

    `git_branch_hint` is the branch recorded in the transcript; it lets a
    backfilled session whose folder no longer exists still be filed under its
    repo name instead of "_no-repo".

    Examples:
        project_name("/Users/me/Developer/datamover/src")          -> "datamover"
        project_name("/gone/old-repo", git_branch_hint="main")     -> "old-repo"
        project_name("/tmp/scratch")                                -> "_no-repo"
    """
    if not cwd:
        return NO_REPO
    root = repo_root(cwd) if Path(cwd).is_dir() else None
    if root is not None:
        return safe_name(root.name)
    if git_branch_hint and not Path(cwd).is_dir():
        return safe_name(Path(cwd).name)
    return NO_REPO


# -----------------------------------------------------------------------------
# Session folders
# -----------------------------------------------------------------------------

def find_session_dir(root: Path, session_id: str) -> Path | None:
    """Existing folder for this session anywhere in the archive, or None."""
    for candidate in sorted(root.glob(f"*/*_{session_id[:8]}")):
        recorded = read_json(candidate / "meta.json").get("session_id")
        if recorded in (None, session_id):  # None: created, meta not yet written
            return candidate
    return None


def session_dir(root: Path, session_id: str, project: str, started: datetime | None) -> Path:
    """
    Return (creating or relocating if needed) the one folder for this session.

    Example:
        session_dir(root, "b9230413-...", "dotfiles", datetime(2026, 9, 23, 10, 22))
        -> <root>/dotfiles/2026-09-23_1022_b9230413
    """
    existing = find_session_dir(root, session_id)
    if existing is not None:
        # Promote a _no-repo session once it is seen inside a repo; otherwise keep it.
        if existing.parent.name == NO_REPO and project != NO_REPO:
            target = root / project / existing.name
            target.parent.mkdir(parents=True, exist_ok=True)
            if not target.exists():
                existing.rename(target)
                return target
        return existing

    stamp = (started or datetime.now().astimezone()).strftime("%Y-%m-%d_%H%M")
    folder = root / project / f"{stamp}_{session_id[:8]}"
    folder.mkdir(parents=True, exist_ok=True)
    return folder
