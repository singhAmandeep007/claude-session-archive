"""
Filesystem utilities: atomic writes and cross-process locks.

Several hooks can run at once (an async Stop from one turn overlapping a
PostToolUse from the next, or two sessions updating the index), so every
shared file is written atomically and guarded by an flock-based lock.

Example:
    with file_lock(session_dir / ".lock"):
        atomic_write_text(session_dir / "chat.md", markdown)
"""

from __future__ import annotations

import contextlib
import errno
import fcntl
import json
import os
import tempfile
import time
from pathlib import Path
from typing import Iterator

from .config import LOCK_TIMEOUT_SECONDS


def atomic_write_bytes(path: Path, data: bytes) -> None:
    """Write via temp file + rename so readers never see a half-written file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".tmp-")
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(data)
        os.replace(tmp, path)
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(tmp)
        raise


def atomic_write_text(path: Path, text: str) -> None:
    """UTF-8 text variant of atomic_write_bytes."""
    atomic_write_bytes(path, text.encode("utf-8"))


def write_json(path: Path, data: dict) -> None:
    """Atomically write pretty-printed JSON. Example: write_json(p, {"a": 1})."""
    atomic_write_text(path, json.dumps(data, indent=2, ensure_ascii=False, default=str) + "\n")


def read_json(path: Path) -> dict:
    """Read a JSON object, returning {} when the file is missing or unreadable."""
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


@contextlib.contextmanager
def file_lock(path: Path, timeout: float = LOCK_TIMEOUT_SECONDS) -> Iterator[None]:
    """
    Hold an exclusive advisory lock on `path` for the duration of the block.

    Polls instead of blocking forever so a stuck process can't hang a hook;
    raises TimeoutError after `timeout` seconds.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_RDWR | os.O_CREAT, 0o600)
    deadline = time.monotonic() + timeout
    try:
        while True:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except OSError as exc:
                if exc.errno not in (errno.EAGAIN, errno.EACCES) or time.monotonic() > deadline:
                    raise TimeoutError(f"could not lock {path}") from exc
                time.sleep(0.05)
        yield
    finally:
        os.close(fd)  # closing the descriptor releases the lock
