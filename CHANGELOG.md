# Changelog

## 1.3.0 (2026-09-26)

- Everything shown in the chat is archived: images returned by tools (browser and device screenshots, Read of an image, any MCP tool) go to `attachments/` and appear inline under the tool call in `chat.md`.
- Media Claude opens with Read (images, PDF, video) and files it sends with SendUserFile are snapshotted, so screenshots kept in `/tmp` survive cleanup.
- Inline widgets are saved to `widgets/`.
- Each tool call's output is shown under it in `chat.md` as a collapsed section (first 2000 characters; the full text stays in `raw/`).
- Sessions archived by an older version are re-rendered once on their next sync or `/session-archive:save all`.

## 1.2.0 (2026-09-25)

- `/session-archive:save` is back, now at zero token cost: the UserPromptExpansion hook saves the live session and reports new messages. `/session-archive:save all` archives every chat still on disk.
- Saving before a chat has any messages reports "nothing to save yet" instead of an error.

## 1.1.1 (2026-09-25)

- Clearer "Archive folder" setting: plain description with an example, and a step-by-step install section showing `--config archive_dir=...` and `/plugin configure`.

## 1.1.0 (2026-09-25)

- Added `/session-archive:import [id] [--list] [--from <folder>]`: restores archived sessions byte-exact into Claude Code so `claude --resume` works, including subagents and file history. Never overwrites a live transcript that differs.
- The command costs zero tokens: a `UserPromptExpansion` hook answers it and blocks it before it reaches the model.
- Removed the `/session-archive:save` skill (hooks already save every turn); `archive.py save` and `backfill` remain for the terminal.

## 1.0.0 (2026-09-24)

- First release.
- Hooks: PostToolUse snapshots for Write, Edit, MultiEdit, NotebookEdit, and Artifact; async Stop, SubagentStop, and PreCompact syncs; sync SessionEnd flush.
- Per-project session folders with `chat.md`, `meta.json`, `raw/`, `artifacts/`, `attachments/`, and a root `README.md` index.
- Incremental, byte-exact mirroring with rewrite protection.
- `/session-archive:save` and `/session-archive:save all` (backfill).
