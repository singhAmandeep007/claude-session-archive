"""
Mirror: a byte-exact, incremental copy of Claude Code's raw files for a session.

Sources under Claude's data dir (see config.claude_home) and where they land:

    projects/<project>/<id>.jsonl          -> raw/<id>.jsonl         (transcript)
    projects/<project>/<id>/subagents/     -> raw/<id>/subagents/    (subagent transcripts)
    projects/<project>/<id>/tool-results/  -> raw/<id>/tool-results/ (large tool outputs)
    file-history/<id>/                     -> raw/file-history/      (pre-edit file snapshots)
    uploads/<id>/, image-cache/<id>/       -> raw/uploads/, raw/image-cache/

Efficiency: a transcript only grows, so when the copy is a prefix of the source
only the new tail is appended. If the source was rewritten instead, the file is
copied whole. Files whose size and mtime match are skipped. Nothing is ever
deleted from the archive, so Claude Code's own cleanup can't reach it.
"""

from __future__ import annotations

import os
import shutil
from datetime import datetime
from pathlib import Path

from .fsutil import atomic_write_bytes

ANCHOR_BYTES = 4096  # tail compared before trusting an append


# -----------------------------------------------------------------------------
# Single files
# -----------------------------------------------------------------------------

def sync_file(src: Path, dst: Path) -> str:
    """
    Make `dst` byte-identical to `src` with the least I/O.

    Returns "unchanged", "appended", or "copied". When a non-empty copy would be
    overwritten by different content, the old copy is kept beside it as
    "<name>.replaced-<timestamp>" so the archive never loses bytes.

    Example: sync_file(Path("a.jsonl"), Path("raw/a.jsonl")) -> "appended"
    """
    src_stat = src.stat()
    if dst.exists():
        dst_stat = dst.stat()
        if dst_stat.st_size == src_stat.st_size and int(dst_stat.st_mtime) == int(src_stat.st_mtime):
            return "unchanged"
        if 0 < dst_stat.st_size < src_stat.st_size and _is_prefix(src, dst, dst_stat.st_size):
            with src.open("rb") as fin, dst.open("ab") as fout:
                fin.seek(dst_stat.st_size)
                shutil.copyfileobj(fin, fout)
            os.utime(dst, (src_stat.st_atime, src_stat.st_mtime))
            return "appended"

    data = src.read_bytes()
    if dst.exists() and dst.stat().st_size > 0 and dst.read_bytes() != data:
        stamp = datetime.now().strftime("%Y%m%d%H%M%S%f")
        dst.rename(dst.with_name(f"{dst.name}.replaced-{stamp}"))
    atomic_write_bytes(dst, data)
    os.utime(dst, (src_stat.st_atime, src_stat.st_mtime))
    return "copied"


def _is_prefix(src: Path, dst: Path, length: int) -> bool:
    """True when the last ANCHOR_BYTES of dst match src at the same offset."""
    start = max(0, length - ANCHOR_BYTES)
    with src.open("rb") as a, dst.open("rb") as b:
        a.seek(start)
        b.seek(start)
        return a.read(length - start) == b.read(length - start)


# -----------------------------------------------------------------------------
# Directory trees
# -----------------------------------------------------------------------------

def sync_tree(src_dir: Path, dst_dir: Path) -> int:
    """Sync every file under src_dir into dst_dir; return how many changed."""
    if not src_dir.is_dir():
        return 0
    changed = 0
    for path in src_dir.rglob("*"):
        if path.is_file() and not path.is_symlink():
            if sync_file(path, dst_dir / path.relative_to(src_dir)) != "unchanged":
                changed += 1
    return changed


def mirror_session(claude_dir: Path, transcript: Path, session_id: str, raw_dir: Path) -> int:
    """
    Mirror everything Claude Code stores for one session into raw_dir.

    Returns the number of files that changed (0 means nothing new).
    """
    changed = 0
    # The live transcript plus copies Claude Code set aside instead of overwriting
    # (<id>.jsonl.superseded-<ts>, <id>.orphaned-<ts>-<suffix>.jsonl).
    siblings = [transcript, *transcript.parent.glob(f"{session_id}.jsonl.superseded-*"),
                *transcript.parent.glob(f"{session_id}.orphaned-*.jsonl")]
    for path in siblings:
        if path.is_file() and sync_file(path, raw_dir / path.name) != "unchanged":
            changed += 1
    changed += sync_tree(transcript.parent / session_id, raw_dir / session_id)
    changed += sync_tree(claude_dir / "file-history" / session_id, raw_dir / "file-history")
    changed += sync_tree(claude_dir / "uploads" / session_id, raw_dir / "uploads")
    changed += sync_tree(claude_dir / "image-cache" / session_id, raw_dir / "image-cache")
    return changed
