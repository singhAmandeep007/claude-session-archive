"""
Transcript parsing: turn Claude Code's .jsonl transcript into a small model.

A transcript is one JSON record per line. The records used here:

    {"type": "user"|"assistant", "timestamp": "...Z", "cwd": "...", "gitBranch": "main",
     "entrypoint": "claude-desktop", "isMeta": false, "isSidechain": false,
     "message": {"model": "...", "content": "text" | [blocks]}}
    {"type": "custom-title", "customTitle": "..."}

Content blocks: text, image (base64 source), tool_use (id, name, input),
tool_result (tool_use_id, content: text and images, e.g. browser or device
screenshots). Thinking blocks are skipped.

Example:
    session = parse(Path("~/.claude/projects/p/abc.jsonl").expanduser(), "abc")
    session.title, len(session.turns), session.tool_results["toolu_01..."]
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

SYSTEM_TAG_RE = re.compile(r"<system-reminder>.*?</system-reminder>", re.DOTALL)
TITLE_MAX = 70


# -----------------------------------------------------------------------------
# Types
# -----------------------------------------------------------------------------

@dataclass
class ToolCall:
    id: str
    name: str
    input: dict


@dataclass
class Turn:
    role: str                      # "user" or "assistant"
    ts: datetime | None
    texts: list[str] = field(default_factory=list)
    images: list[tuple[str, str]] = field(default_factory=list)  # (media_type, base64)
    tools: list[ToolCall] = field(default_factory=list)


@dataclass
class Session:
    session_id: str
    title: str | None = None
    first_prompt: str | None = None
    started: datetime | None = None
    updated: datetime | None = None
    branch: str | None = None
    entry: str | None = None
    model: str | None = None
    cwds: list[str] = field(default_factory=list)
    turns: list[Turn] = field(default_factory=list)
    tool_results: dict[str, str] = field(default_factory=dict)
    tool_images: dict[str, list[tuple[str, str]]] = field(default_factory=dict)  # tool_use_id -> images

    @property
    def display_title(self) -> str:
        """Custom title, else the first prompt, else the short id."""
        return self.title or self.first_prompt or f"Session {self.session_id[:8]}"


# -----------------------------------------------------------------------------
# Utilities
# -----------------------------------------------------------------------------

def local_time(iso: str | None) -> datetime | None:
    """ISO-8601 UTC ("2026-09-23T04:52:00.000Z") to an aware local datetime."""
    if not iso:
        return None
    try:
        return datetime.fromisoformat(iso.replace("Z", "+00:00")).astimezone()
    except ValueError:
        return None


def clean(text: str) -> str:
    """Strip harness-injected <system-reminder> blocks."""
    return SYSTEM_TAG_RE.sub("", text).strip()


def _result_text(content) -> str:
    """Flatten a tool_result's content (string or list of text blocks) to text."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(b.get("text", "") for b in content if isinstance(b, dict))
    return ""


def _result_images(content) -> list[tuple[str, str]]:
    """(media_type, base64) for every image inside a tool_result (screenshots, Read of an image)."""
    if not isinstance(content, list):
        return []
    found = []
    for block in content:
        if not isinstance(block, dict) or block.get("type") != "image":
            continue
        source = block.get("source") or {}
        if source.get("type") == "base64" and source.get("data"):
            found.append((source.get("media_type", "image/png"), source["data"]))
    return found


def first_timestamp(transcript: Path) -> datetime | None:
    """
    Start time from the first timestamped record, reading as little as possible.

    A transcript with no timestamped record (an empty or metadata-only session)
    falls back to the file's creation time, so its folder name stays the same on
    every save instead of taking the current time. None only if the file is gone.
    Example: first_timestamp(Path(".../abc.jsonl")) -> datetime(2026, 9, 25, 10, 33, ...)
    """
    try:
        with transcript.open(encoding="utf-8", errors="replace") as fh:
            for line in fh:
                try:
                    ts = local_time(json.loads(line).get("timestamp"))
                except (ValueError, AttributeError, TypeError):  # not a record, or an odd one
                    continue
                if ts:
                    return ts
        info = transcript.stat()
        return datetime.fromtimestamp(getattr(info, "st_birthtime", info.st_mtime)).astimezone()
    except OSError:
        return None


# -----------------------------------------------------------------------------
# Business logic: parsing
# -----------------------------------------------------------------------------

def parse(transcript: Path, session_id: str) -> Session:
    """Parse a whole transcript into a Session (main thread only, no sidechains)."""
    session = Session(session_id=session_id)
    with transcript.open(encoding="utf-8", errors="replace") as fh:  # one bad byte must not stop archiving
        for line in fh:
            try:
                rec = json.loads(line)
            except ValueError:
                continue
            if isinstance(rec, dict):
                try:
                    _apply(session, rec)
                except (AttributeError, TypeError, ValueError):
                    continue  # a record shape this version doesn't know: skip it, keep the rest
    return session


def _apply(session: Session, rec: dict) -> None:
    """Fold one transcript record into the session."""
    kind = rec.get("type")
    if kind == "custom-title":
        session.title = rec.get("customTitle") or session.title
        return
    if kind not in ("user", "assistant") or rec.get("isMeta") or rec.get("isSidechain"):
        return

    ts = local_time(rec.get("timestamp"))
    session.started = session.started or ts
    session.updated = ts or session.updated
    session.branch = rec.get("gitBranch") or session.branch
    session.entry = rec.get("entrypoint") or session.entry
    cwd = rec.get("cwd")
    if cwd and cwd not in session.cwds:
        session.cwds.append(cwd)

    message = rec.get("message")
    message = message if isinstance(message, dict) else {}
    if kind == "assistant":
        session.model = message.get("model") or session.model

    turn = Turn(role=kind, ts=ts)
    content = message.get("content")
    blocks = [{"type": "text", "text": content}] if isinstance(content, str) else (content or [])
    for block in blocks if isinstance(blocks, list) else []:
        if not isinstance(block, dict):
            continue
        btype = block.get("type")
        if btype == "text":
            text = clean(block.get("text", ""))
            if text:
                turn.texts.append(text)
        elif btype == "image":
            source = block.get("source") or {}
            if source.get("type") == "base64" and source.get("data"):
                turn.images.append((source.get("media_type", "image/png"), source["data"]))
        elif btype == "tool_use":
            turn.tools.append(ToolCall(block.get("id", ""), block.get("name", "tool"), block.get("input") or {}))
        elif btype == "tool_result":
            tool_id = block.get("tool_use_id", "")
            session.tool_results[tool_id] = _result_text(block.get("content"))
            images = _result_images(block.get("content"))
            if images:
                session.tool_images.setdefault(tool_id, []).extend(images)

    if kind == "user" and turn.texts and session.first_prompt is None and not turn.texts[0].startswith("<"):
        first_line = turn.texts[0].splitlines()[0].strip()  # "<command-...>" wrappers aren't titles
        session.first_prompt = first_line[:TITLE_MAX] + ("..." if len(first_line) > TITLE_MAX else "")
    if turn.texts or turn.images or turn.tools:
        session.turns.append(turn)
