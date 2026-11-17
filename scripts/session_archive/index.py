"""
Index: per-session meta.json and the archive-wide README.md.

meta.json (machine-readable, one per session folder):

    {"session_id": "b9230413-...", "title": "...", "project": "dotfiles",
     "started": "2026-09-23T10:22:00+05:30", "messages": 148, "files": 6, ...}

README.md (human-readable, at the archive root) lists every session grouped by
project, newest first, each linking to its chat.md. It is rebuilt from the
meta.json files, so it can always be regenerated with `archive.py index`.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from . import __version__
from .config import claude_account, machine_name
from .fsutil import atomic_write_text, read_json, write_json
from .transcript import Session


def build_meta(session: Session, project: str, transcript: Path, records: list[dict]) -> dict:
    """Metadata describing one archived session."""
    iso = lambda d: d.isoformat(timespec="seconds") if d else None  # noqa: E731
    return {
        "session_id": session.session_id,
        "title": session.display_title,
        "project": project,
        "cwds": session.cwds,
        "branch": session.branch,
        "entry": session.entry,
        "model": session.model,
        "machine": machine_name(),
        "account": claude_account(),
        "started": iso(session.started),
        "updated": iso(session.updated),
        "messages": len(session.turns),
        "files": len({r["path"] for r in records if r.get("snapshot")}),
        "source_transcript": str(transcript),
        "archived_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "archiver_version": __version__,
    }


def save_meta(session_dir: Path, meta: dict) -> None:
    write_json(session_dir / "meta.json", meta)


def _cell(text) -> str:
    """Table-safe text. Example: 'a|b' -> 'a\\|b'."""
    return str(text if text is not None else "").replace("|", "\\|").replace("\n", " ")


def rebuild_index(root: Path) -> int:
    """
    Regenerate <root>/README.md from every session's meta.json.

    Returns the number of sessions indexed. Caller should hold the index lock.
    """
    metas = []
    for meta_path in root.glob("*/*/meta.json"):
        meta = read_json(meta_path)
        if meta.get("session_id"):
            meta["_folder"] = meta_path.parent.relative_to(root).as_posix()
            metas.append(meta)

    by_project: dict[str, list[dict]] = {}
    for meta in metas:
        by_project.setdefault(meta["_folder"].split("/")[0], []).append(meta)

    now = datetime.now().astimezone().strftime("%Y-%m-%d %H:%M %Z")
    lines = [
        "# Claude session archive",
        "",
        f"{len(metas)} session{'s' if len(metas) != 1 else ''} across {len(by_project)} project{'s' if len(by_project) != 1 else ''}. Index rebuilt {now}.",
        "Each session folder holds `chat.md` (readable), `raw/` (exact copy), `artifacts/`, and `attachments/`.",
        "",
    ]
    for project in sorted(by_project, key=lambda p: (p == "_no-repo", p.lower())):
        sessions = sorted(by_project[project], key=lambda m: m.get("started") or "", reverse=True)
        lines += [f"## {project} ({len(sessions)})", "",
                  "| Started | Title | Messages | Files | App |", "| --- | --- | --- | --- | --- |"]
        for m in sessions:
            started = (m.get("started") or "")[:16].replace("T", " ")
            title = f"[{_cell(m.get('title'))}](<{m['_folder']}/chat.md>)"
            lines.append(f"| {started} | {title} | {m.get('messages', 0)} | {m.get('files', 0)} | {_cell(m.get('entry'))} |")
        lines.append("")

    atomic_write_text(root / "README.md", "\n".join(lines))
    return len(metas)
