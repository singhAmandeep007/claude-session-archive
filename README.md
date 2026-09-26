# session-archive

A Claude Code plugin that keeps a permanent, readable copy of every session.
Claude Code deletes CLI and VS Code transcripts after `cleanupPeriodDays` (default 30), files them by folder path, and stores them as raw JSON.
This plugin copies everything into one folder per chat that you can read, search, and back up, and it costs zero model tokens because hooks do the work.

## What you get

```
~/claude-archive/
  README.md                                  index of every chat, grouped by project
  <project>/<YYYY-MM-DD_HHMM>_<id>/
    chat.md        the conversation as you saw it: text, tool calls with their output
                   (collapsed, first 2000 characters), and every image inline
    meta.json      title, folders, branch, app, model, dates, message and file counts
    raw/           byte-exact copy: transcript, subagents, tool results, pre-edit file history
    artifacts/     every version of every file Claude wrote or edited, plus media it opened
                   (screenshots in /tmp) and files it sent to the chat, with versions.jsonl
    attachments/   every image shown in the chat: pasted images, browser and device
                   screenshots, images returned by any tool
    widgets/       charts and diagrams Claude drew inline, as .svg or .html
```

A chat that starts outside a repo is filed under `_no-repo` and moves into the repo's folder the first time it runs inside one.
Each session only ever has one folder, so nothing is duplicated.

## How it works

| Hook | Mode | Captures |
| --- | --- | --- |
| `PostToolUse` on Write, Edit, MultiEdit, NotebookEdit, Artifact | sync, ~40 ms | The file right after the edit, before anything can change it. Identical content is stored once. |
| `PostToolUse` on Read (images, PDF, video only) and SendUserFile | async | The file shown in the chat, before `/tmp` cleanup removes it |
| `Stop` | async | New transcript bytes (appended, not recopied), `chat.md`, `meta.json`, the index |
| `SubagentStop`, `PreCompact` | async | Same as Stop |
| `SessionEnd` | sync | Final flush before the process exits |

The transcript is append-only, so each sync copies only the new tail.
If Claude Code ever rewrites a file instead, it is copied whole and the previous copy is kept as `<name>.replaced-<timestamp>`.
Files larger than 25 MB are recorded in `versions.jsonl` but not copied.
`Write` calls carry the full file content, so older sessions and missed hooks still get exact versions.

Problems never block a turn: hook errors are appended to `<archive>/_logs/errors.log`.

## Install

The repo is private, so Claude Code needs your GitHub login first (one time):

```bash
gh auth login
gh auth setup-git
```

**Step 1. Add the marketplace** (one time per machine):

```bash
claude plugin marketplace add singhAmandeep007/claude-session-archive
```

**Step 2. Install, choosing where chats are saved:**

```bash
claude plugin install session-archive@claude-session-archive --config archive_dir=~/claude-archive
```

`archive_dir` is the only setting.
Replace `~/claude-archive` with any folder you like, for example:

```bash
claude plugin install session-archive@claude-session-archive --config archive_dir=~/Documents/claude-archive
```

If you install without `--config`, Claude Code prints a note that 1 userConfig option is not yet set.
That is harmless: the plugin then uses `~/claude-archive`.
To choose or change the folder later, do either of these:

- inside Claude Code, run `/plugin configure session-archive@claude-session-archive` and fill in "Archive folder"
- in a terminal, re-run the Step 2 command with a different `--config archive_dir=...` (it updates the setting even when the plugin is already installed)

**Step 3. Start a new session.**
Hooks load when a session starts, so sessions that were already open are not archived.
After your first reply, check that `<archive folder>/README.md` lists the session.

**Step 4 (optional). Archive the chats Claude Code still has on disk** by typing this in any session:

```
/session-archive:save all
```

## Use

| Command | Does |
| --- | --- |
| (nothing) | Hooks archive every turn automatically |
| `/session-archive:save` | Save this chat right now and report how many messages were new |
| `/session-archive:save all` | Archive every chat Claude Code still has on disk |
| `/session-archive:import` | Put every archived session Claude Code no longer has back where it expects it |
| `/session-archive:import <id>` | Restore one session (the first characters of its id are enough) |
| `/session-archive:import --list` | Show which archived sessions are missing, without changing anything |
| `/session-archive:import --from <folder>` | Archive and restore sessions from a backup or another machine's `.claude` folder |
| `archive.py index` | Rebuild `README.md` (terminal) |

Both slash commands cost **zero tokens** (see below).

## Zero-token commands

`/session-archive:save` and `/session-archive:import` never reach the model.
A `UserPromptExpansion` hook runs the command itself and blocks it, and Claude Code shows the hook's report instead (0 turns, 0 tokens).
Claude Code labels that report "blocked by hook"; that is expected and means the command ran.

Example:

```
/session-archive:save
session-archive saved 12 new messages (148 total) to ~/claude-archive/my-app/2026-09-25_1204_b2d6cc87
```

## Importing sessions back into Claude Code

If the hook ever can't run, the command falls back to asking Claude to run the same script.

Restored files are byte-exact copies placed at the path Claude Code originally used (`~/.claude/projects/<folder>/<id>.jsonl`), together with subagent transcripts and file history, so `claude --resume <id>` continues the conversation with full context and rewind works.
A live transcript is never overwritten: an older prefix is extended, and a copy that differs is left alone and reported as a conflict.
Run `claude --resume <id>` from the session's folder, which the report prints.

Limits: the Claude desktop app's sidebar lists only sessions from its own private registry, so restored sessions open from the CLI and VS Code, not the desktop sidebar.
Only sessions that were archived (or are in a folder you pass to `--from`) can be restored; transcripts Claude Code deleted before you installed this plugin are gone unless a backup still has them.

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
