"""
Test helpers: a fake Claude data dir, transcript builder, and CLI runner.

Example:
    env = FakeClaude()                      # temp CLAUDE_CONFIG_DIR + archive dir
    t = env.transcript("abc12345-...", cwd="/tmp/x")
    t.user("hello").assistant("hi", tools=[write_call("toolu_1", "/tmp/x/a.txt", "A")])
    t.flush()
    result = env.cli("save", "--session", t.session_id, "--cwd", "/tmp/x")
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
SCRIPT = REPO / "scripts" / "archive.py"
sys.path.insert(0, str(REPO / "scripts"))

BASE_TIME = datetime(2026, 9, 24, 12, 0, tzinfo=timezone.utc)


def write_call(tool_id: str, path: str, content: str) -> dict:
    return {"type": "tool_use", "id": tool_id, "name": "Write", "input": {"file_path": path, "content": content}}


def edit_call(tool_id: str, path: str) -> dict:
    return {"type": "tool_use", "id": tool_id, "name": "Edit",
            "input": {"file_path": path, "old_string": "a", "new_string": "b"}}


def git_init(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "-q", str(path)], check=True)
    return path


class Transcript:
    """Builds a transcript .jsonl the way Claude Code writes it."""

    def __init__(self, path: Path, session_id: str, cwd: str, branch: str | None):
        self.path, self.session_id, self.cwd, self.branch = path, session_id, cwd, branch
        self.records: list[dict] = []
        self.clock = BASE_TIME

    def _rec(self, kind: str, content, **extra) -> "Transcript":
        self.clock += timedelta(minutes=1)
        rec = {"type": kind, "timestamp": self.clock.isoformat().replace("+00:00", "Z"),
               "cwd": self.cwd, "gitBranch": self.branch, "entrypoint": "cli",
               "sessionId": self.session_id, "message": {"role": kind, "content": content}}
        if kind == "assistant":
            rec["message"]["model"] = "claude-test"
        rec.update(extra)
        self.records.append(rec)
        return self

    def user(self, text: str, images: list[tuple[str, str]] = ()) -> "Transcript":
        blocks = [{"type": "text", "text": text}]
        blocks += [{"type": "image", "source": {"type": "base64", "media_type": m, "data": d}} for m, d in images]
        return self._rec("user", blocks)

    def assistant(self, text: str = "", tools: list[dict] = ()) -> "Transcript":
        blocks = ([{"type": "text", "text": text}] if text else []) + list(tools)
        return self._rec("assistant", blocks)

    def tool_result(self, tool_id: str, text: str) -> "Transcript":
        return self._rec("user", [{"type": "tool_result", "tool_use_id": tool_id, "content": text}])

    def tool_result_image(self, tool_id: str, media_type: str, data_b64: str) -> "Transcript":
        """A tool result carrying an image, as screenshots and Read of an image come back."""
        content = [{"type": "text", "text": "screenshot"},
                   {"type": "image", "source": {"type": "base64", "media_type": media_type, "data": data_b64}}]
        return self._rec("user", [{"type": "tool_result", "tool_use_id": tool_id, "content": content}])

    def flush(self) -> Path:
        """Append records not yet written (transcripts only ever grow)."""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as fh:
            for rec in self.records:
                fh.write(json.dumps(rec) + "\n")
        self.records = []
        return self.path


class FakeClaude:
    """Isolated CLAUDE_CONFIG_DIR, archive dir, and workspace for one test."""

    def __init__(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="sa-test-")).resolve()
        self.claude = self.tmp / "claude"
        self.archive = self.tmp / "archive"
        self.work = self.tmp / "work"
        self.work.mkdir()
        self.env = {**os.environ, "CLAUDE_CONFIG_DIR": str(self.claude),
                    "SESSION_ARCHIVE_DIR": str(self.archive),
                    "XDG_CONFIG_HOME": str(self.tmp / "xdg")}
        self.env.pop("CLAUDE_PLUGIN_OPTION_ARCHIVE_DIR", None)

    def cleanup(self) -> None:
        shutil.rmtree(self.tmp, ignore_errors=True)

    def transcript(self, session_id: str, cwd: Path, branch: str | None = "main") -> Transcript:
        project = str(cwd).replace("/", "-")
        return Transcript(self.claude / "projects" / project / f"{session_id}.jsonl", session_id, str(cwd), branch)

    def cli(self, *args: str, stdin: str | None = None, python: str = sys.executable) -> subprocess.CompletedProcess:
        return subprocess.run([python, str(SCRIPT), *args], input=stdin, env=self.env,
                              capture_output=True, text=True, timeout=120)

    def hook(self, payload: dict) -> subprocess.CompletedProcess:
        return self.cli("hook", stdin=json.dumps(payload))

    def session_folders(self) -> list[Path]:
        return sorted(p.parent for p in self.archive.glob("*/*/chat.md"))
