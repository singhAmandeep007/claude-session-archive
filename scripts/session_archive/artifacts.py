"""
Artifacts: a versioned copy of every file Claude creates or edits in a session.

Layout inside a session folder:

    artifacts/
      src/app.py/2026-09-24_223044_ab12cd34.py      one file per distinct version
      _home/.claude/settings.json/2026-...json      paths outside the project
      versions.jsonl                                 one record per tool call

A versions.jsonl record:

    {"tool_use_id": "toolu_01...", "tool": "Edit", "path": "/abs/src/app.py",
     "ts": "2026-09-24T22:30:44+05:30", "sha256": "ab12...", "size": 1234,
     "snapshot": "artifacts/src/app.py/2026-09-24_223044_ab12cd34.py", "source": "hook"}

Captured files (see captured_paths): everything Claude wrote or published, plus
media it opened with Read and files it sent to the chat with SendUserFile.

Sources, best first:
    "hook"       PostToolUse fired right after the edit: the exact version.
    "transcript" Write calls carry the full content, so it is exact even without the hook.
    "archive"    Edits the hook missed (e.g. old sessions): the file as it is at archive
                 time, taken once per path and flagged as such.

Identical content is never stored twice: a record whose hash matches the path's
previous version points at the existing snapshot.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime
from pathlib import Path, PurePosixPath

from .config import MAX_SNAPSHOT_BYTES, MEDIA_EXTENSIONS, WRITE_TOOLS
from .locate import safe_name
from .transcript import Session

VERSIONS_FILE = "versions.jsonl"


# -----------------------------------------------------------------------------
# Utilities
# -----------------------------------------------------------------------------

def captured_paths(tool: str, tool_input: dict) -> list[str]:
    """
    Files a tool call made or showed in the chat, which the archive keeps.

    Examples:
        Edit {"file_path": "/p/a.py"}                 -> ["/p/a.py"]
        Read {"file_path": "/tmp/shot.png"}           -> ["/tmp/shot.png"]  (media only)
        Read {"file_path": "/p/a.py"}                 -> []
        SendUserFile {"files": ["/tmp/a.png", "b.pdf"]} -> ["/tmp/a.png", "b.pdf"]
    """
    if tool in WRITE_TOOLS:
        path = tool_input.get("file_path") or tool_input.get("notebook_path")
        return [path] if path else []
    if tool == "Read":
        path = tool_input.get("file_path") or ""
        return [path] if Path(path).suffix.lower() in MEDIA_EXTENSIONS else []
    if tool == "SendUserFile":
        return [f for f in tool_input.get("files") or [] if isinstance(f, str) and f]
    return []


def display_path(file_path: str, project_root: Path | None) -> PurePosixPath:
    """
    Where a file's versions live under artifacts/.

    Examples (project_root=/Users/me/app):
        /Users/me/app/src/x.py   -> src/x.py
        /Users/me/.zshrc         -> _home/.zshrc
        /private/tmp/page.html   -> _root/private/tmp/page.html
    """
    path = Path(file_path)
    for base, prefix in ((project_root, None), (Path.home(), "_home"), (Path("/"), "_root")):
        if base is None:
            continue
        try:
            rel = path.relative_to(base)
        except ValueError:
            continue
        parts = [safe_name(p) for p in rel.parts]
        return PurePosixPath(*(([prefix] if prefix else []) + parts))
    return PurePosixPath("_other", safe_name(file_path))


def load_versions(session_dir: Path) -> list[dict]:
    """All version records for a session, oldest first."""
    path = session_dir / "artifacts" / VERSIONS_FILE
    if not path.is_file():
        return []
    records = []
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        try:
            records.append(json.loads(line))
        except ValueError:
            continue
    return records


def _append_record(session_dir: Path, record: dict) -> None:
    path = session_dir / "artifacts" / VERSIONS_FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(record, ensure_ascii=False) + "\n")


# -----------------------------------------------------------------------------
# Business logic: snapshots
# -----------------------------------------------------------------------------

def snapshot(session_dir: Path, *, file_path: str, tool: str, tool_use_id: str,
             ts: datetime | None, project_root: Path | None, source: str,
             content: bytes | None = None) -> dict:
    """
    Record one version of `file_path` for a tool call (idempotent per tool call and path).

    `content` is used when given (Write input from the transcript); otherwise the
    file is read from disk. Caller must hold the session lock.

    Example:
        snapshot(sdir, file_path="/p/a.py", tool="Edit", tool_use_id="toolu_1",
                 ts=None, project_root=Path("/p"), source="hook")
        -> {"snapshot": "artifacts/a.py/2026-..._1a2b3c4d.py", ...}
    """
    versions = load_versions(session_dir)
    for existing in versions:
        if existing.get("tool_use_id") == tool_use_id and existing.get("path") == file_path:
            return existing

    when = ts or datetime.now().astimezone()
    record = {"tool_use_id": tool_use_id, "tool": tool, "path": file_path,
              "ts": when.isoformat(timespec="seconds"), "source": source}

    if content is None:
        try:
            content = Path(file_path).read_bytes() if Path(file_path).stat().st_size <= MAX_SNAPSHOT_BYTES else None
            if content is None:
                record["skipped"] = f"larger than {MAX_SNAPSHOT_BYTES} bytes"
        except OSError:
            record["missing"] = True
    elif len(content) > MAX_SNAPSHOT_BYTES:
        content, record["skipped"] = None, f"larger than {MAX_SNAPSHOT_BYTES} bytes"

    if content is not None:
        digest = hashlib.sha256(content).hexdigest()
        record.update(sha256=digest, size=len(content))
        previous = next((v for v in reversed(versions)
                         if v.get("path") == file_path and v.get("snapshot")), None)
        if previous and previous.get("sha256") == digest:
            record["snapshot"] = previous["snapshot"]  # unchanged content: reuse, don't copy
        else:
            rel_dir = PurePosixPath("artifacts") / display_path(file_path, project_root)
            name = f"{when.strftime('%Y-%m-%d_%H%M%S')}_{digest[:8]}{Path(file_path).suffix}"
            target = session_dir / rel_dir / name
            target.parent.mkdir(parents=True, exist_ok=True)
            if not target.exists():
                target.write_bytes(content)
            record["snapshot"] = str(rel_dir / name)

    _append_record(session_dir, record)
    return record


def reconcile(session_dir: Path, session: Session, project_root: Path | None) -> int:
    """
    Fill in versions for tool calls the PostToolUse hook didn't capture.

    Returns the number of records added. Caller must hold the session lock.
    """
    recorded = {(v.get("tool_use_id"), v.get("path")) for v in load_versions(session_dir)}
    added = 0
    pending_edits: dict[str, tuple] = {}

    for turn in session.turns:
        for call in turn.tools:
            for path in captured_paths(call.name, call.input):
                if (call.id, path) in recorded:
                    continue
                if call.name == "Write" and isinstance(call.input.get("content"), str):
                    snapshot(session_dir, file_path=path, tool="Write", tool_use_id=call.id,
                             ts=turn.ts, project_root=project_root, source="transcript",
                             content=call.input["content"].encode("utf-8"))
                    added += 1
                else:
                    pending_edits[path] = (call, turn.ts)  # keep the latest missed call per path

    # One "as of archive time" copy per path that has no version at all yet.
    have_snapshot = {v.get("path") for v in load_versions(session_dir) if v.get("snapshot")}
    for path, (call, ts) in pending_edits.items():
        if path not in have_snapshot:
            snapshot(session_dir, file_path=path, tool=call.name, tool_use_id=call.id,
                     ts=ts, project_root=project_root, source="archive")
            added += 1
    return added
