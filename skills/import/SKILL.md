---
name: import
description: Put archived sessions back into Claude Code so `claude --resume` can open them. Handled by a hook at zero token cost. Optional args - a session id prefix, --list, or --from <backup folder>.
disable-model-invocation: true
argument-hint: "[session-id] [--list] [--from <folder>]"
allowed-tools: Bash(python3 "${CLAUDE_PLUGIN_ROOT}/scripts/archive.py" import *)
---

This command normally never reaches you: the plugin's UserPromptExpansion hook runs it and shows the result.
If you are reading this, that hook did not run, so run this once and reply with its output only:

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/archive.py" import $ARGUMENTS
```
