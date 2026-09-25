"""
Sync: orchestration shared by the hooks and the CLI.

Two entry points:

    capture_tool(root, payload)
        PostToolUse on a file tool. Snapshots the file right after the edit,
        before anything else can change it.

    sync_session(root, session_id, transcript, cwd)
        Stop, SubagentStop, PreCompact, SessionEnd, /session-archive:save, backfill.
        Mirrors raw files (appending only new bytes), fills in missed file
        versions, re-renders chat.md, writes meta.json, and refreshes the index.
        If nothing changed on disk it returns early without parsing.

Locks: one per session id (so an async Stop never races the next PostToolUse)
and one for the index (so parallel sessions never interleave README writes).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from . import artifacts, index, mirror, render, transcript
from .config import SNAPSHOT_TOOLS, claude_home
from .fsutil import atomic_write_text, file_lock, read_json
from .locate import project_name, repo_root, session_dir


_UUID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")


@dataclass
class SyncResult:
    folder: Path
    total_messages: int
    new_messages: int
    changed: bool


def _lock(root: Path, name: str) -> Path:
    return root / ".locks" / f"{name}.lock"


def ensure_root(root: Path) -> None:
    """Create the archive root, private to the user (transcripts can hold secrets)."""
    root.mkdir(parents=True, exist_ok=True)
    root.chmod(0o700)


# -----------------------------------------------------------------------------
# Business logic
# -----------------------------------------------------------------------------

def capture_tool(root: Path, payload: dict) -> dict | None:
    """
    Snapshot the file a PostToolUse payload refers to.

    Example payload: {"session_id": "...", "transcript_path": "...", "cwd": "/p",
                      "tool_name": "Edit", "tool_use_id": "toolu_1",
                      "tool_input": {"file_path": "/p/a.py", ...}}
    """
    tool = payload.get("tool_name")
    path = artifacts.tool_file_path(payload.get("tool_input") or {})
    session_id = payload.get("session_id")
    if tool not in SNAPSHOT_TOOLS or not path or not session_id:
        return None

    ensure_root(root)
    cwd = payload.get("cwd")
    with file_lock(_lock(root, session_id)):
        started = transcript.first_timestamp(Path(payload.get("transcript_path") or ""))
        folder = session_dir(root, session_id, project_name(cwd), started)
        return artifacts.snapshot(
            folder, file_path=path, tool=tool, tool_use_id=payload.get("tool_use_id") or "",
            ts=None, project_root=repo_root(cwd) if cwd else None, source="hook",
        )


def sync_session(root: Path, session_id: str, transcript_path: Path,
                 cwd: str | None = None, force: bool = False,
                 claude_dir: Path | None = None) -> SyncResult:
    """
    Bring one session's archive folder fully up to date.

    `claude_dir` is the Claude data dir the transcript came from (default: this
    machine's); pass a backup's dir to archive sessions from that backup.
    """
    ensure_root(root)
    with file_lock(_lock(root, session_id)):
        parsed = None
        if cwd is None:  # backfill: take the folder from the transcript itself
            parsed = transcript.parse(transcript_path, session_id)
            cwd = parsed.cwds[-1] if parsed.cwds else None
            project = project_name(cwd, parsed.branch)
            started = parsed.started
        else:
            project = project_name(cwd)
            started = transcript.first_timestamp(transcript_path)

        folder = session_dir(root, session_id, project, started)
        previous = read_json(folder / "meta.json")
        changed = mirror.mirror_session(claude_dir or claude_home(), transcript_path, session_id, folder / "raw")
        if not (changed or force or not (folder / "chat.md").exists()):
            total = previous.get("messages", 0)
            return SyncResult(folder, total, 0, False)

        parsed = parsed or transcript.parse(transcript_path, session_id)
        project = folder.parent.name  # the folder may have been promoted out of _no-repo
        artifacts.reconcile(folder, parsed, repo_root(cwd) if cwd else None)
        records = artifacts.load_versions(folder)
        atomic_write_text(folder / "chat.md", render.render(parsed, folder, records, project))
        meta = index.build_meta(parsed, project, transcript_path, records)
        index.save_meta(folder, meta)

    with file_lock(_lock(root, "index")):
        index.rebuild_index(root)

    total = meta["messages"]
    return SyncResult(folder, total, max(total - previous.get("messages", 0), 0), True)


def find_transcript(session_id: str) -> Path | None:
    """Newest transcript file for a session id under Claude's projects folder."""
    matches = list((claude_home() / "projects").glob(f"*/{session_id}.jsonl"))
    return max(matches, key=lambda p: p.stat().st_mtime) if matches else None


def transcripts_in(source: Path) -> list[tuple[str, Path, Path]]:
    """
    (session_id, transcript, claude_dir) for every transcript under a folder laid
    out like Claude's data dir: a backup of ~/.claude, another machine's copy, or
    just its projects/ folder.

    Example: /Volumes/backup/.claude -> [("abc...", .../projects/-Users-me-app/abc....jsonl, /Volumes/backup/.claude)]
    """
    found = []
    for path in Path(source).expanduser().rglob("*.jsonl"):
        if path.parent.parent.name == "projects" and _UUID.fullmatch(path.stem):
            found.append((path.stem, path, path.parent.parent.parent))
    return sorted(found, key=lambda item: item[1].stat().st_mtime)


def all_transcripts() -> list[tuple[str, Path]]:
    """Every (session_id, transcript) Claude Code still has on disk, oldest first."""
    found = [(p.stem, p) for p in (claude_home() / "projects").glob("*/*.jsonl")]
    return sorted(found, key=lambda item: item[1].stat().st_mtime)
