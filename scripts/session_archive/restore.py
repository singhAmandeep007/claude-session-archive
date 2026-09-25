"""
Restore: put archived sessions back where Claude Code looks for them, so
`claude --resume <id>` (and VS Code's session history) can open them again.

What goes back, byte for byte, from <archive>/<project>/<session>/raw/:

    <id>.jsonl          -> <claude>/projects/<folder>/<id>.jsonl   (the transcript)
    <id>/               -> <claude>/projects/<folder>/<id>/        (subagents, tool results)
    file-history/       -> <claude>/file-history/<id>/             (enables rewind)
    uploads/, image-cache/ likewise

<folder> is taken from the path Claude Code originally used (meta.json
"source_transcript"), so restored files land exactly where Claude Code filed
them. Only if that is unknown is it derived from the session's working folder
the way Claude Code names project folders ("/Users/me/app" -> "-Users-me-app").

Safety: a file Claude Code still has is never overwritten. If the live copy is
an older prefix of the archived one it is extended; if it differs in any other
way it is left alone and reported as a conflict.

The Claude desktop app's sidebar lists only sessions in its own private
registry, which this module does not touch; restored sessions open from the CLI
and VS Code.
"""

from __future__ import annotations

import os
import re
import shutil
from dataclasses import dataclass
from pathlib import Path, PurePath

from .fsutil import atomic_write_bytes, read_json
from .mirror import _is_prefix

_PROJECT_DIR_UNSAFE = re.compile(r"[^A-Za-z0-9]")


@dataclass
class RestoreResult:
    session_id: str
    title: str
    status: str          # "restored", "extended", "present", "conflict", or "missing-raw"
    target: Path | None
    cwd: str | None


# -----------------------------------------------------------------------------
# Utilities
# -----------------------------------------------------------------------------

def restore_file(src: Path, dst: Path) -> str:
    """
    Copy src to dst without ever clobbering a different live file.

    Returns "restored" (dst was missing), "present" (identical), "extended"
    (dst was an older prefix and got the missing tail), or "conflict".
    Example: restore_file(Path("raw/a.jsonl"), Path("~/.claude/projects/-p/a.jsonl"))
    """
    src_stat = src.stat()
    if not dst.exists():
        atomic_write_bytes(dst, src.read_bytes())
        os.utime(dst, (src_stat.st_atime, src_stat.st_mtime))
        return "restored"
    dst_size = dst.stat().st_size
    if dst_size == src_stat.st_size and dst.read_bytes() == src.read_bytes():
        return "present"
    if 0 < dst_size < src_stat.st_size and _is_prefix(src, dst, dst_size):
        with src.open("rb") as fin, dst.open("ab") as fout:
            fin.seek(dst_size)
            shutil.copyfileobj(fin, fout)
        return "extended"
    return "conflict"


def restore_tree(src_dir: Path, dst_dir: Path) -> None:
    """restore_file for every file under src_dir (conflicting live files are kept)."""
    if src_dir.is_dir():
        for path in src_dir.rglob("*"):
            if path.is_file():
                restore_file(path, dst_dir / path.relative_to(src_dir))


def project_dir_name(cwd: str) -> str:
    """Claude Code's folder name for a working directory. Example: '/Users/me/my_app' -> '-Users-me-my-app'."""
    return _PROJECT_DIR_UNSAFE.sub("-", cwd)


def transcript_target(meta: dict, claude_dir: Path) -> Path | None:
    """
    Where Claude Code expects this session's transcript on this machine.

    Re-roots the original path under the current Claude data dir, so an archive
    made on another machine or from a backup folder still lands correctly.
    Example: /backup/.claude/projects/-Users-me-app/abc.jsonl -> ~/.claude/projects/-Users-me-app/abc.jsonl
    """
    session_id = meta["session_id"]
    parts = PurePath(meta.get("source_transcript") or "").parts
    if "projects" in parts:
        i = len(parts) - 1 - parts[::-1].index("projects")
        if len(parts) > i + 2:  # projects/<folder>/<file>
            return claude_dir / "projects" / parts[i + 1] / f"{session_id}.jsonl"
    cwds = meta.get("cwds") or []
    return claude_dir / "projects" / project_dir_name(cwds[-1]) / f"{session_id}.jsonl" if cwds else None


# -----------------------------------------------------------------------------
# Business logic
# -----------------------------------------------------------------------------

def archived_sessions(root: Path) -> list[tuple[Path, dict]]:
    """(folder, meta) for every archived session, oldest first."""
    found = []
    for meta_path in root.glob("*/*/meta.json"):
        meta = read_json(meta_path)
        if meta.get("session_id"):
            found.append((meta_path.parent, meta))
    return sorted(found, key=lambda item: item[1].get("started") or "")


def restore_session(folder: Path, meta: dict, claude_dir: Path, dry_run: bool = False) -> RestoreResult:
    """Restore one archived session into claude_dir (see module docstring)."""
    session_id = meta["session_id"]
    cwds = meta.get("cwds") or []
    result = RestoreResult(session_id, meta.get("title") or session_id[:8], "missing-raw", None, cwds[-1] if cwds else None)
    raw = folder / "raw"
    transcript = raw / f"{session_id}.jsonl"
    target = transcript_target(meta, claude_dir)
    if not transcript.is_file() or target is None:
        return result

    result.target = target
    if dry_run:
        result.status = "present" if target.exists() else "restored"
        return result

    result.status = restore_file(transcript, target)
    if result.status != "conflict":
        restore_tree(raw / session_id, target.parent / session_id)
        restore_tree(raw / "file-history", claude_dir / "file-history" / session_id)
        restore_tree(raw / "uploads", claude_dir / "uploads" / session_id)
        restore_tree(raw / "image-cache", claude_dir / "image-cache" / session_id)
    return result


def restore(root: Path, claude_dir: Path, selector: str | None = None,
            dry_run: bool = False) -> list[RestoreResult]:
    """
    Restore every archived session (or those whose id starts with `selector`).

    Example: restore(Path("~/claude-archive"), Path("~/.claude"), "75cb6e36")
    """
    results = []
    for folder, meta in archived_sessions(root):
        if selector and not meta["session_id"].startswith(selector):
            continue
        results.append(restore_session(folder, meta, claude_dir, dry_run))
    return results


def format_report(results: list[RestoreResult], list_only: bool = False) -> str:
    """
    Human-readable summary shown to the user (also as the hook's block reason).

    Example:
        import: 1 restored, 0 extended, 22 already in Claude Code, 0 conflicts
        - 75cb6e36 "Fix the login bug"  cd /Users/me/app && claude --resume 75cb6e36-...
    """
    missing = [r for r in results if r.status == "restored"]
    if list_only:
        head = f"{len(missing)} of {len(results)} archived sessions are missing from Claude Code"
        head += " (run /session-archive:import to restore them)." if missing else "."
        lines = [head]
    else:
        count = lambda status: sum(r.status == status for r in results)  # noqa: E731
        lines = [f"import: {count('restored')} restored, {count('extended')} extended, "
                 f"{count('present')} already in Claude Code, {count('conflict')} conflicts"
                 + (f", {count('missing-raw')} without a raw transcript" if count("missing-raw") else "")]
    for r in results:
        if r.status in ("restored", "extended") or (r.status == "conflict" and not list_only):
            where = f"cd {r.cwd!r} && " if r.cwd else ""
            note = " (kept the live copy, it differs)" if r.status == "conflict" else ""
            lines.append(f'- {r.session_id[:8]} "{r.title}"  {where}claude --resume {r.session_id}{note}')
    return "\n".join(lines)
