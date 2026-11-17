# session-archive

A Claude Code plugin that keeps a permanent, readable copy of every session.

Claude Code deletes CLI and VS Code transcripts after `cleanupPeriodDays` (default 30 days), files them by folder path, and stores them as raw JSON.
This plugin copies every chat into one folder per chat that you can read, search, and back up: the conversation as Markdown, every file version Claude wrote, every screenshot, and a byte-exact transcript you can restore and resume later.
It runs from hooks, so it costs zero model tokens.

Works in the Claude Code CLI, the VS Code extension, and the Code tab of the Claude desktop app.

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
| `Stop` | async, at most every 10 min (`min_sync_minutes`) | New transcript bytes (appended, not recopied), `chat.md`, `meta.json`, the index |
| `SubagentStop`, `PreCompact` | async | Same as Stop |
| `SessionEnd` | sync | Final flush before the process exits |

The transcript is append-only, so each sync copies only the new tail.
If Claude Code ever rewrites a file instead, it is copied whole and the previous copy is kept as `<name>.replaced-<timestamp>`.
Files larger than 25 MB are recorded in `versions.jsonl` but not copied.
`Write` calls carry the full file content, so older sessions and missed hooks still get exact versions.

Problems never block a turn: hook errors are appended to `<archive>/_logs/errors.log`.

## Requirements

- Claude Code. Version 2.1.287 or newer gives the cleanest command output (see [Zero-token commands](#zero-token-commands)); older versions work too.
- Python 3.9 or newer on `PATH` as `python3` (macOS's `/usr/bin/python3` works). Standard library only, nothing to `pip install`.
- `git`, to file chats under their repository's name.
- macOS and Linux: tested on every push (Python 3.9 and 3.13). Windows: best effort, untested; the hooks call `python3`, so a Windows install needs `python3` on `PATH`.

## Install

**Step 1. Add the marketplace** (one time per machine):

```bash
claude plugin marketplace add singhAmandeep007/claude-session-archive
```

**Step 2. Install, choosing where chats are saved:**

```bash
claude plugin install session-archive@claude-session-archive --config archive_dir=~/claude-archive --config min_sync_minutes=10
```

`archive_dir` is where chats are saved; `min_sync_minutes` is how often replies are saved (0 = after every reply).
Replace `~/claude-archive` with any folder you like, for example:

```bash
claude plugin install session-archive@claude-session-archive --config archive_dir=~/Documents/claude-archive --config min_sync_minutes=10
```

If you leave out a `--config`, Claude Code prints a note that a userConfig option is not yet set.
That is harmless: the plugin then uses the defaults (`~/claude-archive`, every 10 minutes).
To change either setting later, run `/plugin configure session-archive@claude-session-archive` inside Claude Code, then start a new session.

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

`/session-archive:save` and `/session-archive:import` never reach the model: 0 turns, 0 tokens.

- **Claude Code 2.1.287 or newer:** the plugin's mod (`hooks/register.ts`) answers the command and its report shows as normal command output:

  ```
  /session-archive:save
  session-archive: saved 12 new messages (148 total) to ~/claude-archive/my-app/2026-09-25_1204_b2d6cc87
  ```

- **Older versions:** a `UserPromptExpansion` hook answers it instead, and Claude Code shows the report under **"A hook blocked your prompt"**.
  That label is how Claude Code shows every zero-token hook command: the command ran, and the text under it is the result.
  Update Claude Code to get the plain output.

## Importing sessions back into Claude Code

Restored files are byte-exact copies placed at the path Claude Code originally used (`~/.claude/projects/<folder>/<id>.jsonl`), together with subagent transcripts and file history, so `claude --resume <id>` continues the conversation with full context and rewind works.
A live transcript is never overwritten: an older prefix is extended, and a copy that differs is left alone and reported as a conflict.
Run `claude --resume <id>` from the session's folder, which the report prints.

Limits: the Claude desktop app's sidebar lists only sessions from its own private registry, so restored sessions open from the CLI and VS Code, not the desktop sidebar.
Only sessions that were archived (or are in a folder you pass to `--from`) can be restored; transcripts Claude Code deleted before you installed this plugin are gone unless a backup still has them.

Change the folder with the plugin's **Archive folder** option (default `~/claude-archive`).
Change how often replies are saved with **Minutes between automatic saves** (`min_sync_minutes`, default 10, 0 = every reply); a cloud-synced archive folder re-uploads whole files, so 10 keeps Google Drive or iCloud traffic low.
`SESSION_ARCHIVE_DIR` overrides everything, which is useful for tests.

## Privacy

Transcripts contain whatever passed through a tool: file contents, command output, pasted secrets.
The archive root is created with mode `700`.
Don't put it in a synced or shared folder unless you are comfortable with that.

## Troubleshooting

| You see | Meaning and fix |
| --- | --- |
| "A hook blocked your prompt" after `/session-archive:save` | Normal on Claude Code before 2.1.287; the text under it is the result. Update Claude Code for plain output. |
| `failed: ... It needs Python 3 on PATH` | Install Python 3 or put it on `PATH` as `python3`, then start a new session. |
| A new chat is missing from the archive | Sessions opened before the install aren't hooked: start a new one. Errors are logged in `<archive>/_logs/errors.log`. |
| "1 userConfig option is not yet set" while installing | Harmless: the archive goes to `~/claude-archive`. Set it with `/plugin configure session-archive@claude-session-archive`. |
| Saves land in an old folder after changing the Archive folder option | Open sessions keep the setting they started with: restart Claude Code (quit the desktop app fully). |

## Development

```bash
python3 -m unittest discover -s tests -v                  # behavior tests, isolated temp dirs
claude plugin test .                                      # the mod's tests (tests/mod.test.ts)
claude plugin validate .                                  # manifest, hooks and the mod
claude -p "..." --plugin-dir .                            # try it live without installing
```

## License

MIT
