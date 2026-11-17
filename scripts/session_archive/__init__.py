"""
Session Archive: a permanent, readable copy of every Claude Code session.

Modules:
    config     where things live (archive root, Claude's data dir, limits)
    locate     which project a session belongs to, its folder, file locks
    mirror     byte-exact incremental copy of Claude's raw session files
    transcript parse a transcript .jsonl into turns, tool calls, and images
    artifacts  versioned snapshots of files Claude writes, plus artifact links
    render     the readable chat.md
    index      per-session meta.json and the archive-wide README.md
    sync       orchestration used by hooks and the CLI
    restore    put archived sessions back into Claude Code (the import command)

Only the Python standard library is used, and Python 3.9+ is supported
(macOS ships 3.9 as /usr/bin/python3).
"""

__version__ = "1.6.0"
