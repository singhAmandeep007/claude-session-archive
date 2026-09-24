"""
Render: the human-readable chat.md for one session.

Everything is linked relatively, so the folder can be opened in VS Code, Obsidian,
or on GitHub and every link keeps working:

    ### 22:31 Claude
    Done. The config now validates.
    - `Edit` [settings.json](<artifacts/settings.json/2026-09-24_223044_ab12cd34.json>)
    - `Artifact` [page.html](<artifacts/_root/.../page.html>) published at https://claude.ai/...
    - `Bash` Run tests: go test ./...

Pasted images are decoded into attachments/ and embedded inline.
"""

from __future__ import annotations

import base64
import hashlib
import json
import re
from pathlib import Path

from .artifacts import tool_file_path
from .config import SNAPSHOT_TOOLS
from .transcript import Session, ToolCall

TOOL_INPUT_PREVIEW = 240
ARTIFACT_URL_RE = re.compile(r"https://claude\.ai/\S*artifact/[A-Za-z0-9-]+")
IMAGE_EXT = {"image/png": ".png", "image/jpeg": ".jpg", "image/gif": ".gif", "image/webp": ".webp"}


# -----------------------------------------------------------------------------
# Utilities
# -----------------------------------------------------------------------------

def link(label: str, target: str) -> str:
    """Markdown link whose target may contain spaces. Example: link('a', 'x y.md')."""
    return f"[{label}](<{target}>)"


def artifact_url(session: Session, call: ToolCall) -> str | None:
    """The claude.ai URL an Artifact call published, read from its tool result."""
    found = ARTIFACT_URL_RE.search(session.tool_results.get(call.id, ""))
    return found.group(0) if found else None


def summarize(call: ToolCall) -> str:
    """
    One-line summary of a non-file tool call, preferring its human description.

    Example: Bash {"description": "Run tests", "command": "go test ./..."}
          -> "`Bash` Run tests: go test ./..."
    """
    args = call.input
    desc = args.get("description", "")
    detail = args.get("command") or args.get("file_path") or args.get("url") or args.get("pattern")
    if detail is None:
        detail = json.dumps(args, ensure_ascii=False)
    detail = " ".join(str(detail).split())[:TOOL_INPUT_PREVIEW]
    return f"`{call.name}` {desc + ': ' if desc else ''}{detail}"


def save_image(session_dir: Path, media_type: str, data: str) -> str:
    """Decode a base64 image into attachments/ once; return its relative path."""
    raw = base64.b64decode(data)
    name = f"{hashlib.sha256(raw).hexdigest()[:12]}{IMAGE_EXT.get(media_type, '.bin')}"
    target = session_dir / "attachments" / name
    if not target.exists():
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(raw)
    return f"attachments/{name}"


# -----------------------------------------------------------------------------
# Business logic: document sections
# -----------------------------------------------------------------------------

def _file_line(session: Session, call: ToolCall, versions: dict[str, dict]) -> str:
    """A file tool call, linked to the exact version it produced when captured."""
    path = tool_file_path(call.input) or "?"
    record = versions.get(call.id, {})
    label = Path(path).name
    text = f"`{call.name}` " + (link(label, record["snapshot"]) if record.get("snapshot") else f"`{path}`")
    if record.get("source") == "archive":
        text += " (state at archive time; this edit was not captured live)"
    elif record.get("missing"):
        text += " (file was already gone when archived)"
    elif record.get("skipped"):
        text += f" ({record['skipped']})"
    elif not record:
        text += " (not captured)"
    if call.name == "Artifact":
        url = artifact_url(session, call)
        if url:
            text += f" published at {url}"
    return text


def _files_section(session: Session, records: list[dict]) -> list[str]:
    """Summary of every file touched, with its latest version and version count."""
    latest: dict[str, dict] = {}
    counts: dict[str, set] = {}
    for rec in records:
        if rec.get("snapshot"):
            latest[rec["path"]] = rec
            counts.setdefault(rec["path"], set()).add(rec["snapshot"])
    urls = [(c, artifact_url(session, c)) for t in session.turns for c in t.tools if c.name == "Artifact"]
    urls = [(c, u) for c, u in urls if u]
    if not latest and not urls:
        return []

    lines = ["## Files", ""]
    for path, rec in sorted(latest.items()):
        n = len(counts[path])
        lines.append(f"- `{path}`: {link('latest', rec['snapshot'])}, {n} version{'s' if n != 1 else ''}")
    for call, url in urls:
        lines.append(f"- Published artifact: {url} (from `{tool_file_path(call.input)}`)")
    return lines + [""]


def render(session: Session, session_dir: Path, records: list[dict], project: str) -> str:
    """Build the full chat.md text for a session."""
    versions = {r["tool_use_id"]: r for r in records if r.get("tool_use_id")}
    fmt = "%Y-%m-%d %H:%M %Z"
    started = session.started.strftime(fmt) if session.started else "unknown"
    updated = session.updated.strftime(fmt) if session.updated else "unknown"
    raw_name = f"{session.session_id}.jsonl"

    out = [
        f"# {session.display_title}",
        "",
        "| | |",
        "| --- | --- |",
        f"| Session | `{session.session_id}` |",
        f"| Project | {project} |",
        f"| Folder(s) | {', '.join(f'`{c}`' for c in session.cwds) or 'n/a'} |",
        f"| Started | {started} |",
        f"| Last updated | {updated} |",
        f"| Branch / app / model | `{session.branch or 'n/a'}` / {session.entry or 'n/a'} / {session.model or 'n/a'} |",
        f"| Messages | {len(session.turns)} |",
        f"| Exact transcript | {link(raw_name, 'raw/' + raw_name)} |",
        "",
    ]
    out += _files_section(session, records)
    out += ["---", ""]

    current_day = None
    for turn in session.turns:
        day = turn.ts.strftime("%Y-%m-%d") if turn.ts else None
        if day and day != current_day:
            out += [f"## {day}", ""]
            current_day = day
        who = "You" if turn.role == "user" else "Claude"
        out += [f"### {turn.ts.strftime('%H:%M') if turn.ts else '--:--'} {who}", ""]
        for text in turn.texts:
            out += [text, ""]
        for i, (media_type, data) in enumerate(turn.images, 1):
            out += [f"![image {i}]({save_image(session_dir, media_type, data)})", ""]
        tool_lines = [
            _file_line(session, c, versions) if c.name in SNAPSHOT_TOOLS and tool_file_path(c.input)
            else summarize(c)
            for c in turn.tools
        ]
        if tool_lines:
            out += [f"- {line}" for line in tool_lines] + [""]
    return "\n".join(out)
