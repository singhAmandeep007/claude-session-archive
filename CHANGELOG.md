# Changelog

## 1.0.0 (2026-09-24)

- First release.
- Hooks: PostToolUse snapshots for Write, Edit, MultiEdit, NotebookEdit, and Artifact; async Stop, SubagentStop, and PreCompact syncs; sync SessionEnd flush.
- Per-project session folders with `chat.md`, `meta.json`, `raw/`, `artifacts/`, `attachments/`, and a root `README.md` index.
- Incremental, byte-exact mirroring with rewrite protection.
- `/session-archive:save` and `/session-archive:save all` (backfill).
