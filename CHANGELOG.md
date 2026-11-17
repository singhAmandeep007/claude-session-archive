# Changelog

## 1.6.0 (2026-11-17)

- `/session-archive:save` and `/session-archive:import` are answered by a mod (`hooks/register.ts`) on Claude Code 2.1.287 and newer: the report shows as normal command output instead of "A hook blocked your prompt", still at zero tokens. Older versions keep the hook path.
- The mod falls back to `python` when `python3` is missing; file locking works on Windows (no `fcntl` there).
- Import: a live transcript that kept growing after its last save now counts as "already in Claude Code" instead of a conflict; the `cd ... && claude --resume` hint is shell-quoted for any folder name.
- Archiving no longer stops on a transcript with invalid UTF-8 bytes or record shapes it doesn't know; it skips what it can't read and keeps the rest.
- Tests: 81 behavior tests (import edge cases, damaged transcripts, odd paths, parallel saves, the mod's command path) plus 6 mod tests; CI runs them on Linux and macOS.
- README rewritten for first-time users: requirements and supported systems, the two command output styles, troubleshooting.

## 1.5.0 (2026-11-16)

- A backfill files each session where the live hooks would: under the first repo it ran in, and a deleted subfolder under the repo around it (before: the last folder, or the deleted folder's own name). Archiving into a second folder no longer names the same session differently.
- A session with no timestamped messages gets its folder name from the transcript's creation time instead of the current time, so it keeps one name.
- Each archived chat records the computer it ran on (macOS LocalHostName) and the signed-in Claude account email, in `meta.json` and the `chat.md` header. Point the Archive folder option at a per-machine folder to keep machines apart.

## 1.4.0 (2026-11-16)

- After-reply saves (Stop, SubagentStop) run at most every 10 minutes per session (option `min_sync_minutes`, 0 = every reply). Cloud-synced folders such as Google Drive re-upload whole files, and transcripts reach 100 MB. Session end, compaction, `/session-archive:save` and file snapshots still save immediately.

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
