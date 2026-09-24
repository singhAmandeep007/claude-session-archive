---
name: save
description: Save this chat to the session archive right now (hooks already do this after every turn). Pass "all" to archive every session Claude Code still has on disk. Only new content is added; each session keeps a single folder.
disable-model-invocation: true
argument-hint: "[all]"
allowed-tools: Bash(python3 "${CLAUDE_PLUGIN_ROOT}/scripts/archive.py" *)
---

If the arguments are exactly `all`, run:

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/archive.py" backfill
```

Otherwise run:

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/archive.py" save --session "${CLAUDE_SESSION_ID}"
```

Arguments: $ARGUMENTS

Reply with the command's output lines and nothing else.
If it fails, show the error and stop. Don't retry with other arguments and don't write any archive files yourself.
