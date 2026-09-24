# session-archive

A Claude Code plugin that keeps a permanent, readable copy of every session.
Claude Code deletes CLI and VS Code transcripts after `cleanupPeriodDays` (default 30), files them by folder path, and stores them as raw JSON.
This plugin copies everything into one folder per chat that you can read, search, and back up, and it costs zero model tokens because hooks do the work.

## What you get

```
~/claude-archive/
  README.md                                  index of every chat, grouped by project
  <project>/<YYYY-MM-DD_HHMM>_<id>/
    chat.md        the conversation, with every file edit linked to the exact version it produced
    meta.json      title, folders, branch, app, model, dates, message and file counts
    raw/           byte-exact copy: transcript, subagents, tool results, pre-edit file history
    artifacts/     every version of every file Claude wrote or edited, plus versions.jsonl
    attachments/   images pasted into the chat
```

A chat that starts outside a repo is filed under `_no-repo` and moves into the repo's folder the first time it runs inside one.
Each session only ever has one folder, so nothing is duplicated.

## How it works

| Hook | Mode | Captures |
| --- | --- | --- |
| `PostToolUse` on Write, Edit, MultiEdit, NotebookEdit, Artifact | sync, ~40 ms | The file right after the edit, before anything can change it. Identical content is stored once. |
| `Stop` | async | New transcript bytes (appended, not recopied), `chat.md`, `meta.json`, the index |
| `SubagentStop`, `PreCompact` | async | Same as Stop |
| `SessionEnd` | sync | Final flush before the process exits |

The transcript is append-only, so each sync copies only the new tail.
If Claude Code ever rewrites a file instead, it is copied whole and the previous copy is kept as `<name>.replaced-<timestamp>`.
Files larger than 25 MB are recorded in `versions.jsonl` but not copied.
`Write` calls carry the full file content, so older sessions and missed hooks still get exact versions.

Problems never block a turn: hook errors are appended to `<archive>/_logs/errors.log`, and `/session-archive:save` reports them.

## Install (private repo)

Claude Code uses your git credentials, so run `gh auth login` (and `gh auth setup-git` for background updates) first.

```bash
claude plugin marketplace add singhAmandeep007/claude-session-archive
claude plugin install session-archive@claude-session-archive
```

Then archive everything Claude Code still has on disk:

```
/session-archive:save all
```

## Use

| Command | Does |
| --- | --- |
| (nothing) | Hooks archive every turn automatically |
| `/session-archive:save` | Save this chat now and report how many messages were new |
| `/session-archive:save all` | Archive every transcript still on disk |
| `python3 scripts/archive.py index` | Rebuild `README.md` |

Change the folder with the plugin's **Archive folder** option (default `~/claude-archive`).
`SESSION_ARCHIVE_DIR` overrides everything, which is useful for tests.

## Privacy

Transcripts contain whatever passed through a tool: file contents, command output, pasted secrets.
The archive root is created with mode `700`.
Don't put it in a synced or shared folder unless you are comfortable with that.

## Requirements

Python 3.9 or newer (macOS's `/usr/bin/python3` works), standard library only, and `git` for project detection.

## Development

```bash
python3 -m unittest discover -s tests -v                  # behavior tests, isolated temp dirs
claude plugin validate .claude-plugin/plugin.json --strict
claude -p "..." --plugin-dir .                            # try it live without installing
```

## License

MIT
