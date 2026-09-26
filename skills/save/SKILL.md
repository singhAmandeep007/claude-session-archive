---
name: save
description: Save this chat to the session archive right now (hooks already save after every reply). Pass "all" to archive every chat Claude Code still has on disk. Handled by a hook at zero token cost.
disable-model-invocation: true
argument-hint: "[all]"
allowed-tools: Bash(python3 "${CLAUDE_PLUGIN_ROOT}/scripts/archive.py" *)
---

This command normally never reaches you: the plugin's UserPromptExpansion hook runs it and shows the result.
If you are reading this, that hook did not run. Run exactly one command and reply with its output only.

If the arguments are exactly `all`:

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/archive.py" backfill
```

Otherwise:

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/archive.py" save --session "${CLAUDE_SESSION_ID}"
```

Arguments: $ARGUMENTS
