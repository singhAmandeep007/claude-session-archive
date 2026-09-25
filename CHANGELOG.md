# Changelog

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
