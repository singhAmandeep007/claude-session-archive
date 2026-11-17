"""
Render: the human-readable chat.md for one session.

The goal is that chat.md shows everything the chat showed. Everything is linked
relatively, so the folder opens in VS Code, Obsidian, or on GitHub:

    ### 22:31 Claude
    Checked the dark theme on the phone.
    - `Edit` [theme.ts](<artifacts/src/theme.ts/2026-09-25_223044_ab12cd34.ts>)
    - `mcp__Claude_Browser__computer` screenshot
      ![image 1](<attachments/9f2c41aa01bd.jpg>)
    - `Read` [pixel-dark.png](<artifacts/_root/private/tmp/.../pixel-dark.png/2026-..png>)
      ![image 1](<attachments/5d1e0b7c9a31.png>)
    - `SendUserFile` [report.pdf](<artifacts/...>) "v2 on the Pixel"
    - `Artifact` [page.html](<artifacts/...>) published at https://claude.ai/...
    - `mcp__visualize__show_widget` [widget](<widgets/toolu_01abc.html>)
    - `Bash` Run tests: go test ./...
      <details><summary>output</summary> ... </details>

Images come from the transcript itself (pasted images and every image a tool
returned, such as browser or device screenshots) and are decoded into
attachments/ once. Files come from artifacts/versions.jsonl.
"""

from __future__ import annotations

import base64
import hashlib
import json
import re
from pathlib import Path

from .artifacts import captured_paths
from .config import WRITE_TOOLS, claude_account, machine_name
from .transcript import Session, ToolCall

TOOL_INPUT_PREVIEW = 240
TOOL_OUTPUT_PREVIEW = 2000  # chars of each tool output shown in chat.md; raw/ keeps it all
ARTIFACT_URL_RE = re.compile(r"https://claude\.ai/\S*artifact/[A-Za-z0-9-]+")
IMAGE_EXT = {"image/png": ".png", "image/jpeg": ".jpg", "image/gif": ".gif", "image/webp": ".webp"}
IMAGE_FILE_EXT = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg"}


# -----------------------------------------------------------------------------
# Utilities
# -----------------------------------------------------------------------------

def link(label: str, target: str) -> str:
    """Markdown link whose target may contain spaces. Example: link('a', 'x y.md')."""
    return f"[{label}](<{target}>)"


def image(label: str, target: str) -> str:
    """Markdown image. Example: image('image 1', 'attachments/ab.png')."""
    return f"![{label}](<{target}>)"


def artifact_url(session: Session, call: ToolCall) -> str | None:
    """The claude.ai URL an Artifact call published, read from its tool result."""
    found = ARTIFACT_URL_RE.search(session.tool_results.get(call.id, ""))
    return found.group(0) if found else None


def summarize(call: ToolCall) -> str:
    """
    One-line summary of a tool call, preferring its human description.

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


def save_widget(session_dir: Path, call: ToolCall) -> str | None:
    """
    Save an inline widget (chart, diagram, or small UI Claude drew in the chat) once.

    Example: {"widget_code": "<svg ...>"} -> "widgets/toolu_01abc.svg"
    """
    code = call.input.get("widget_code")
    if not isinstance(code, str) or not code.strip():
        return None
    suffix = ".svg" if code.lstrip().startswith("<svg") else ".html"
    name = f"{re.sub(r'[^A-Za-z0-9_-]', '-', call.id) or 'widget'}{suffix}"
    target = session_dir / "widgets" / name
    if not target.exists():
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(code, encoding="utf-8")
    return f"widgets/{name}"


# -----------------------------------------------------------------------------
# Business logic: one line per tool call, with what it produced underneath
# -----------------------------------------------------------------------------

def output_block(text: str) -> list[str]:
    """
    A tool's text output as a collapsed section, indented to sit inside its list item.

    Long output is cut at TOOL_OUTPUT_PREVIEW characters; the exact full text stays in raw/.
    Example: output_block("ok") -> ["  <details><summary>output</summary>", "", "  ```text", "  ok", "  ```", "", "  </details>"]
    """
    text = text.strip()
    if not text:
        return []
    note = ""
    if len(text) > TOOL_OUTPUT_PREVIEW:
        note = f"(first {TOOL_OUTPUT_PREVIEW} of {len(text)} characters; the full output is in raw/)"
        text = text[:TOOL_OUTPUT_PREVIEW]
    longest = max((len(run) for run in re.findall(r"`+", text)), default=0)
    fence = "`" * max(3, longest + 1)  # never let the output close the fence early
    body = [f"  {line}" if line else "" for line in text.splitlines()]
    return ["  <details><summary>output</summary>", "", f"  {fence}text", *body, f"  {fence}",
            *([f"  {note}"] if note else []), "", "  </details>"]


def _file_note(record: dict, tool: str) -> str:
    """Why a captured file has no exact version, if it doesn't."""
    if record.get("source") == "archive" and tool in WRITE_TOOLS:
        return " (state at archive time; this edit was not captured live)"
    if record.get("missing"):
        return " (file was already gone when archived)"
    if record.get("skipped"):
        return f" ({record['skipped']})"
    return "" if record else " (not captured)"


def _tool_block(session: Session, session_dir: Path, call: ToolCall, versions: dict[str, list[dict]]) -> list[str]:
    """Markdown lines for one tool call: a summary line, then its files and images."""
    paths = captured_paths(call.name, call.input)
    records = {r.get("path"): r for r in versions.get(call.id, [])}
    lines: list[str] = []

    if paths:
        parts = []
        for path in paths:
            rec = records.get(path, {})
            label = Path(path).name
            parts.append((link(label, rec["snapshot"]) if rec.get("snapshot") else f"`{path}`") + _file_note(rec, call.name))
        head = f"`{call.name}` " + ", ".join(parts)
        if call.name == "SendUserFile" and call.input.get("caption"):
            head += f' "{call.input["caption"]}"'
        if call.name == "Artifact" and artifact_url(session, call):
            head += f" published at {artifact_url(session, call)}"
        lines.append(head)
        # Files sent to the chat were displayed there, so show image files inline too.
        if call.name == "SendUserFile":
            for path in paths:
                rec = records.get(path, {})
                if rec.get("snapshot") and Path(path).suffix.lower() in IMAGE_FILE_EXT:
                    lines.append("  " + image(Path(path).name, rec["snapshot"]))
    else:
        widget = save_widget(session_dir, call)
        lines.append(f"`{call.name}` " + link("widget", widget) if widget else summarize(call))

    for i, (media_type, data) in enumerate(session.tool_images.get(call.id, []), 1):
        lines.append("  " + image(f"image {i}", save_image(session_dir, media_type, data)))
    lines += output_block(session.tool_results.get(call.id, ""))
    return lines


def _files_section(session: Session, records: list[dict]) -> list[str]:
    """Summary of every file written or shown, with its latest version and version count."""
    latest: dict[str, dict] = {}
    counts: dict[str, set] = {}
    for rec in records:
        if rec.get("snapshot"):
            latest[rec["path"]] = rec
            counts.setdefault(rec["path"], set()).add(rec["snapshot"])
    urls = [(c, artifact_url(session, c)) for t in session.turns for c in t.tools if c.name == "Artifact"]
    urls = [(c, u) for c, u in urls if u]
    shots = sum(len(v) for v in session.tool_images.values()) + sum(len(t.images) for t in session.turns)
    if not latest and not urls and not shots:
        return []

    lines = ["## Files", ""]
    for path, rec in sorted(latest.items()):
        n = len(counts[path])
        lines.append(f"- `{path}`: {link('latest', rec['snapshot'])}, {n} version{'s' if n != 1 else ''}")
    for call, url in urls:
        lines.append(f"- Published artifact: {url} (from `{call.input.get('file_path')}`)")
    if shots:
        lines.append(f"- {shots} image{'s' if shots != 1 else ''} (screenshots, pasted images) in `attachments/`, shown inline below")
    return lines + [""]


def render(session: Session, session_dir: Path, records: list[dict], project: str) -> str:
    """Build the full chat.md text for a session."""
    versions: dict[str, list[dict]] = {}
    for rec in records:
        if rec.get("tool_use_id"):
            versions.setdefault(rec["tool_use_id"], []).append(rec)
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
        f"| Machine / account | `{machine_name()}` / {claude_account() or 'n/a'} |",
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
            out += [image(f"image {i}", save_image(session_dir, media_type, data)), ""]
        tool_lines: list[str] = []
        for call in turn.tools:
            block = _tool_block(session, session_dir, call, versions)
            tool_lines += [f"- {block[0]}", *block[1:]]
        if tool_lines:
            out += tool_lines + [""]
    return "\n".join(out)
