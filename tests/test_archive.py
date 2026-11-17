"""
Behavior tests for session-archive. Standard library only.

Run:  python3 -m unittest discover -s tests -v
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import shlex
import shutil
import subprocess
import sys
import unittest
from datetime import datetime
from pathlib import Path

from helpers import FakeClaude, edit_call, git_init, write_call

SID_A = "aaaaaaaa-1111-2222-3333-444444444444"
SID_B = "bbbbbbbb-1111-2222-3333-444444444444"


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def versions(folder: Path) -> list[dict]:
    lines = (folder / "artifacts" / "versions.jsonl").read_text().splitlines()
    return [json.loads(line) for line in lines]


class ArchiveTestCase(unittest.TestCase):
    def setUp(self):
        self.env = FakeClaude()
        self.addCleanup(self.env.cleanup)

    def save(self, sid: str, cwd: Path) -> subprocess.CompletedProcess:
        result = self.env.cli("save", "--session", sid, "--cwd", str(cwd))
        self.assertEqual(result.returncode, 0, result.stderr)
        return result


# -----------------------------------------------------------------------------
# Raw mirror: exactness and incremental copying
# -----------------------------------------------------------------------------

class MirrorTests(ArchiveTestCase):
    def test_transcript_copy_is_byte_exact_and_grows_by_appending(self):
        repo = git_init(self.env.work / "app")
        t = self.env.transcript(SID_A, repo)
        t.user("first").assistant("one").flush()
        self.save(SID_A, repo)
        t.user("second").assistant("two").flush()
        out = self.save(SID_A, repo)

        folder, = self.env.session_folders()
        raw = folder / "raw" / f"{SID_A}.jsonl"
        self.assertEqual(sha(raw), sha(t.path))
        self.assertIn("saved 2 new messages (4 total)", out.stdout)

    def test_mirror_appends_only_the_new_tail(self):
        from session_archive.mirror import sync_file
        src, dst = self.env.tmp / "src.jsonl", self.env.tmp / "dst.jsonl"
        src.write_bytes(b"line1\n")
        self.assertEqual(sync_file(src, dst), "copied")
        with src.open("ab") as fh:
            fh.write(b"line2\n")
        self.assertEqual(sync_file(src, dst), "appended")
        self.assertEqual(dst.read_bytes(), b"line1\nline2\n")
        self.assertEqual(sync_file(src, dst), "unchanged")

    def test_rewritten_source_is_copied_whole_and_old_copy_is_kept(self):
        from session_archive.mirror import sync_file
        src, dst = self.env.tmp / "src.jsonl", self.env.tmp / "dst.jsonl"
        src.write_bytes(b"original content that is long\n")
        sync_file(src, dst)
        src.write_bytes(b"rewritten\n")
        self.assertEqual(sync_file(src, dst), "copied")
        self.assertEqual(dst.read_bytes(), b"rewritten\n")
        kept = list(dst.parent.glob("dst.jsonl.replaced-*"))
        self.assertEqual(len(kept), 1)
        self.assertEqual(kept[0].read_bytes(), b"original content that is long\n")

    def test_subagents_tool_results_and_file_history_are_mirrored(self):
        repo = git_init(self.env.work / "app")
        t = self.env.transcript(SID_A, repo)
        t.user("go").assistant("done").flush()
        (t.path.parent / SID_A / "subagents").mkdir(parents=True)
        (t.path.parent / SID_A / "subagents" / "agent-1.jsonl").write_text("{}\n")
        (t.path.parent / SID_A / "tool-results").mkdir()
        (t.path.parent / SID_A / "tool-results" / "big.txt").write_text("output")
        (self.env.claude / "file-history" / SID_A).mkdir(parents=True)
        (self.env.claude / "file-history" / SID_A / "abc@v1").write_text("before edit")
        self.save(SID_A, repo)

        raw = self.env.session_folders()[0] / "raw"
        self.assertEqual((raw / SID_A / "subagents" / "agent-1.jsonl").read_text(), "{}\n")
        self.assertEqual((raw / SID_A / "tool-results" / "big.txt").read_text(), "output")
        self.assertEqual((raw / "file-history" / "abc@v1").read_text(), "before edit")

    def test_second_save_without_changes_does_not_rewrite(self):
        repo = git_init(self.env.work / "app")
        t = self.env.transcript(SID_A, repo)
        t.user("hi").assistant("hello").flush()
        self.save(SID_A, repo)
        chat = self.env.session_folders()[0] / "chat.md"
        before = chat.stat().st_mtime_ns
        out = self.save(SID_A, repo)
        self.assertEqual(chat.stat().st_mtime_ns, before)
        self.assertIn("saved 0 new messages", out.stdout)


# -----------------------------------------------------------------------------
# One folder per session, filed under the right project
# -----------------------------------------------------------------------------

class FolderTests(ArchiveTestCase):
    def test_session_moves_from_no_repo_into_repo_once_and_then_stays(self):
        scratch = self.env.work / "scratch"
        scratch.mkdir()
        repo = git_init(self.env.work / "dotfiles")
        other = git_init(self.env.work / "other")
        t = self.env.transcript(SID_A, scratch)
        t.user("start").assistant("ok").flush()
        self.save(SID_A, scratch)
        self.assertEqual(self.env.session_folders()[0].parent.name, "_no-repo")

        t.user("moved").assistant("ok").flush()
        self.save(SID_A, repo)
        self.save(SID_A, other)

        folders = self.env.session_folders()
        self.assertEqual(len(folders), 1)
        self.assertEqual(folders[0].parent.name, "dotfiles")

    def test_worktree_session_is_filed_under_main_repo(self):
        repo = git_init(self.env.work / "mainrepo")
        subprocess.run(["git", "-C", str(repo), "commit", "-q", "--allow-empty", "-m", "init"],
                       check=True, env={**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t",
                                        "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t"})
        wt = self.env.work / "wt"
        subprocess.run(["git", "-C", str(repo), "worktree", "add", "-q", str(wt)], check=True)
        t = self.env.transcript(SID_A, wt)
        t.user("in worktree").assistant("ok").flush()
        self.save(SID_A, wt)
        self.assertEqual(self.env.session_folders()[0].parent.name, "mainrepo")

    def test_backfill_files_deleted_repo_folders_by_name_using_branch_hint(self):
        gone = self.env.work / "deleted-repo"  # never created on disk
        t = self.env.transcript(SID_A, gone, branch="main")
        t.user("old work").assistant("ok").flush()
        result = self.env.cli("backfill")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.env.session_folders()[0].parent.name, "deleted-repo")

    def test_backfill_files_deleted_subfolder_under_its_repo(self):
        repo = git_init(self.env.work / "datamover")
        t = self.env.transcript(SID_A, repo / "docs")  # docs/ was deleted later
        t.user("write docs").assistant("ok").flush()
        result = self.env.cli("backfill")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.env.session_folders()[0].parent.name, "datamover")

    def test_backfill_files_session_under_first_repo_like_live_hooks(self):
        first = git_init(self.env.work / "cadence")
        later = git_init(self.env.work / "op-sqlite")
        t = self.env.transcript(SID_A, first)
        t.user("start").assistant("ok")
        t.cwd = str(later)
        t.user("moved on").assistant("ok").flush()
        result = self.env.cli("backfill")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.env.session_folders()[0].parent.name, "cadence")

    def test_session_without_timestamps_keeps_one_folder_name(self):
        path = self.env.claude / "projects" / "-tmp-x" / f"{SID_A}.jsonl"
        path.parent.mkdir(parents=True)
        path.write_text(json.dumps({"type": "custom-title", "customTitle": "empty"}) + "\n")
        old = datetime(2026, 1, 2, 3, 4).timestamp()
        os.utime(path, (old, old))  # on macOS this also moves the creation time back
        info = path.stat()
        created = datetime.fromtimestamp(getattr(info, "st_birthtime", info.st_mtime))
        result = self.env.cli("backfill")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.env.session_folders()[0].name, f"{created:%Y-%m-%d_%H%M}_{SID_A[:8]}")

    def test_parallel_syncs_leave_one_consistent_folder(self):
        repo = git_init(self.env.work / "app")
        t = self.env.transcript(SID_A, repo)
        for i in range(50):
            t.user(f"q{i}").assistant(f"a{i}")
        t.flush()
        procs = [subprocess.Popen([sys.executable, str(Path(__file__).parents[1] / "scripts" / "archive.py"),
                                   "save", "--session", SID_A, "--cwd", str(repo)],
                                  env=self.env.env, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
                 for _ in range(8)]
        for p in procs:
            _, err = p.communicate(timeout=120)
            self.assertEqual(p.returncode, 0, err)
        folders = self.env.session_folders()
        self.assertEqual(len(folders), 1)
        meta = json.loads((folders[0] / "meta.json").read_text())
        self.assertEqual(meta["messages"], 100)
        self.assertEqual(sha(folders[0] / "raw" / f"{SID_A}.jsonl"), sha(t.path))


# -----------------------------------------------------------------------------
# Artifacts: versions captured by hooks and recovered from transcripts
# -----------------------------------------------------------------------------

class ArtifactTests(ArchiveTestCase):
    def post_tool_use(self, sid, cwd, transcript, tool_id, path, tool="Edit"):
        result = self.env.hook({"hook_event_name": "PostToolUse", "session_id": sid,
                                "transcript_path": str(transcript), "cwd": str(cwd),
                                "tool_name": tool, "tool_use_id": tool_id,
                                "tool_input": {"file_path": str(path)}})
        self.assertEqual(result.returncode, 0)

    def test_each_distinct_version_is_stored_once_and_linked_to_its_edit(self):
        repo = git_init(self.env.work / "app")
        target = repo / "src" / "app.py"
        target.parent.mkdir()
        t = self.env.transcript(SID_A, repo)
        t.user("edit it").flush()
        for tool_id, content in (("toolu_1", "v1"), ("toolu_2", "v2"), ("toolu_3", "v2")):
            target.write_text(content)
            self.post_tool_use(SID_A, repo, t.path, tool_id, target)
            t.assistant(tools=[edit_call(tool_id, str(target))]).flush()
        target.write_text("changed later, outside Claude")
        self.save(SID_A, repo)

        folder = self.env.session_folders()[0]
        recs = {r["tool_use_id"]: r for r in versions(folder)}
        self.assertEqual((folder / recs["toolu_1"]["snapshot"]).read_text(), "v1")
        self.assertEqual((folder / recs["toolu_2"]["snapshot"]).read_text(), "v2")
        self.assertEqual(recs["toolu_2"]["snapshot"], recs["toolu_3"]["snapshot"])
        self.assertEqual(len(list((folder / "artifacts" / "src" / "app.py").iterdir())), 2)
        chat = (folder / "chat.md").read_text()
        self.assertIn(recs["toolu_1"]["snapshot"], chat)

    def test_write_content_is_recovered_exactly_without_the_hook(self):
        repo = git_init(self.env.work / "app")
        target = repo / "notes.md"
        t = self.env.transcript(SID_A, repo)
        t.user("write notes").assistant(tools=[write_call("toolu_w", str(target), "exact\ncontent\n")]).flush()
        result = self.env.cli("backfill")  # target never existed on disk
        self.assertEqual(result.returncode, 0, result.stderr)

        folder = self.env.session_folders()[0]
        rec, = versions(folder)
        self.assertEqual(rec["source"], "transcript")
        self.assertEqual((folder / rec["snapshot"]).read_text(), "exact\ncontent\n")

    def test_published_artifact_keeps_page_and_url_after_tmp_file_is_deleted(self):
        repo = git_init(self.env.work / "app")
        page = self.env.tmp / "scratchpad" / "page.html"
        page.parent.mkdir()
        page.write_text("<h1>hi</h1>")
        url = "https://claude.ai/code/artifact/7ab8d561-e2a6-4fc8-acae-451a93505185"
        t = self.env.transcript(SID_A, repo)
        call = {"type": "tool_use", "id": "toolu_art", "name": "Artifact", "input": {"file_path": str(page)}}
        t.user("publish").assistant(tools=[call]).flush()
        self.post_tool_use(SID_A, repo, t.path, "toolu_art", page, tool="Artifact")
        t.tool_result("toolu_art", f"Published {page} at {url}").flush()
        page.unlink()
        self.save(SID_A, repo)

        folder = self.env.session_folders()[0]
        rec, = versions(folder)
        self.assertEqual((folder / rec["snapshot"]).read_text(), "<h1>hi</h1>")
        self.assertIn(url, (folder / "chat.md").read_text())

    def test_pasted_image_is_extracted_byte_exact(self):
        repo = git_init(self.env.work / "app")
        png = b"\x89PNG\r\n\x1a\n" + bytes(range(64))
        t = self.env.transcript(SID_A, repo)
        t.user("see this", images=[("image/png", base64.b64encode(png).decode())]).assistant("seen").flush()
        self.save(SID_A, repo)

        folder = self.env.session_folders()[0]
        image, = (folder / "attachments").iterdir()
        self.assertEqual(image.read_bytes(), png)
        self.assertIn(f"attachments/{image.name}", (folder / "chat.md").read_text())

    def test_oversized_file_is_recorded_but_not_copied(self):
        from session_archive import artifacts
        folder = self.env.tmp / "session"
        big = self.env.tmp / "big.bin"
        big.write_bytes(b"x" * 64)
        original = artifacts.MAX_SNAPSHOT_BYTES
        artifacts.MAX_SNAPSHOT_BYTES = 10
        self.addCleanup(setattr, artifacts, "MAX_SNAPSHOT_BYTES", original)
        rec = artifacts.snapshot(folder, file_path=str(big), tool="Write", tool_use_id="t1",
                                 ts=None, project_root=None, source="hook")
        self.assertIn("skipped", rec)
        self.assertNotIn("snapshot", rec)


# -----------------------------------------------------------------------------
# Hook safety, index, and CLI errors
# -----------------------------------------------------------------------------

class HookAndIndexTests(ArchiveTestCase):
    def test_stop_hook_archives_session(self):
        repo = git_init(self.env.work / "app")
        t = self.env.transcript(SID_A, repo)
        t.user("hi").assistant("hello").flush()
        result = self.env.hook({"hook_event_name": "Stop", "session_id": SID_A,
                                "transcript_path": str(t.path), "cwd": str(repo)})
        self.assertEqual(result.returncode, 0)
        self.assertEqual(len(self.env.session_folders()), 1)

    def test_bad_hook_input_never_fails_and_is_logged(self):
        result = self.env.cli("hook", stdin="not json")
        self.assertEqual(result.returncode, 0)
        self.assertIn("hook unknown", (self.env.archive / "_logs" / "errors.log").read_text())

    def test_save_for_unknown_session_fails_visibly(self):
        result = self.env.cli("save", "--session", "does-not-exist")
        self.assertEqual(result.returncode, 1)
        self.assertIn("no transcript found", result.stderr)

    def test_index_lists_every_session_grouped_by_project(self):
        app = git_init(self.env.work / "app")
        web = git_init(self.env.work / "web")
        self.env.transcript(SID_A, app).user("a").assistant("a").flush()
        self.env.transcript(SID_B, web).user("b").assistant("b").flush()
        self.assertEqual(self.env.cli("backfill").returncode, 0)

        readme = (self.env.archive / "README.md").read_text()
        self.assertIn("## app (1)", readme)
        self.assertIn("## web (1)", readme)
        for folder in self.env.session_folders():
            self.assertIn(f"{folder.parent.name}/{folder.name}/chat.md", readme)

    def test_save_uses_the_folder_configured_for_hooks(self):
        repo = git_init(self.env.work / "app")
        custom = self.env.tmp / "custom-archive"
        t = self.env.transcript(SID_A, repo)
        t.user("hi").assistant("hello").flush()
        del self.env.env["SESSION_ARCHIVE_DIR"]
        hook_env = {**self.env.env, "CLAUDE_PLUGIN_OPTION_ARCHIVE_DIR": str(custom)}
        subprocess.run([sys.executable, str(Path(__file__).parents[1] / "scripts" / "archive.py"), "hook"],
                       input=json.dumps({"hook_event_name": "Stop", "session_id": SID_A,
                                         "transcript_path": str(t.path), "cwd": str(repo)}),
                       env=hook_env, text=True, check=True)
        t.user("more").assistant("ok").flush()
        result = self.env.cli("save", "--session", SID_A, "--cwd", str(repo))  # no plugin option here

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn(str(custom.resolve()), result.stdout)
        self.assertIn("saved 2 new messages", result.stdout)

    def test_archive_root_is_private(self):
        repo = git_init(self.env.work / "app")
        self.env.transcript(SID_A, repo).user("a").assistant("a").flush()
        self.save(SID_A, repo)
        self.assertEqual(self.env.archive.stat().st_mode & 0o777, 0o700)


# -----------------------------------------------------------------------------
# Import: putting archived sessions back into Claude Code
# -----------------------------------------------------------------------------

class ImportTests(ArchiveTestCase):
    def archived_then_deleted(self, sid=SID_A, name="app"):
        """Archive a session with subagents and file history, then delete Claude's copies."""
        repo = git_init(self.env.work / name)
        t = self.env.transcript(sid, repo)
        t.user(f"work in {name}").assistant("done").flush()
        (t.path.parent / sid / "subagents").mkdir(parents=True)
        (t.path.parent / sid / "subagents" / "agent-1.jsonl").write_text("{}\n")
        (self.env.claude / "file-history" / sid).mkdir(parents=True)
        (self.env.claude / "file-history" / sid / "f@v1").write_text("before")
        self.save(sid, repo)
        original = t.path.read_bytes()
        t.path.unlink()
        shutil.rmtree(t.path.parent / sid)
        shutil.rmtree(self.env.claude / "file-history" / sid)
        return t, original

    def test_deleted_session_is_restored_exactly_where_claude_expects_it(self):
        t, original = self.archived_then_deleted()
        out = self.env.cli("import")
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertEqual(t.path.read_bytes(), original)
        self.assertEqual((t.path.parent / SID_A / "subagents" / "agent-1.jsonl").read_text(), "{}\n")
        self.assertEqual((self.env.claude / "file-history" / SID_A / "f@v1").read_text(), "before")
        self.assertIn("1 restored", out.stdout)
        self.assertIn(f"claude --resume {SID_A}", out.stdout)

    def test_second_import_changes_nothing(self):
        self.archived_then_deleted()
        self.env.cli("import")
        out = self.env.cli("import")
        self.assertIn("0 restored, 0 extended, 1 already in Claude Code", out.stdout)

    def test_a_different_live_transcript_is_never_overwritten(self):
        t, _ = self.archived_then_deleted()
        t.path.write_text('{"type": "user", "note": "live copy that diverged"}\n')
        out = self.env.cli("import")
        self.assertIn("1 conflicts", out.stdout)
        self.assertIn("live copy that diverged", t.path.read_text())

    def test_live_copy_that_is_an_older_prefix_is_extended(self):
        t, original = self.archived_then_deleted()
        t.path.write_bytes(original[: original.index(b"\n") + 1])  # just the first record
        out = self.env.cli("import")
        self.assertIn("1 extended", out.stdout)
        self.assertEqual(t.path.read_bytes(), original)

    def test_import_by_id_prefix_restores_only_that_session(self):
        a, _ = self.archived_then_deleted(SID_A, "app")
        b, _ = self.archived_then_deleted(SID_B, "web")
        self.env.cli("import", SID_B[:8])
        self.assertFalse(a.path.exists())
        self.assertTrue(b.path.exists())

    def test_list_only_reports_missing_sessions(self):
        t, _ = self.archived_then_deleted()
        out = self.env.cli("import", "--list")
        self.assertIn("1 of 1 archived sessions are missing", out.stdout)
        self.assertFalse(t.path.exists())

    def test_sessions_from_a_backup_folder_are_archived_and_restored(self):
        backup = self.env.tmp / "backup" / ".claude"
        sid = "cccccccc-1111-2222-3333-444444444444"
        transcript = backup / "projects" / "-Users-me-old-app" / f"{sid}.jsonl"
        transcript.parent.mkdir(parents=True)
        transcript.write_text(json.dumps({"type": "user", "timestamp": "2026-05-01T10:00:00Z",
                                          "cwd": "/Users/me/old-app", "gitBranch": "main",
                                          "message": {"role": "user", "content": "old question"}}) + "\n")
        (backup / "file-history" / sid).mkdir(parents=True)
        (backup / "file-history" / sid / "x@v1").write_text("old file")

        out = self.env.cli("import", "--from", str(backup))
        self.assertEqual(out.returncode, 0, out.stderr)
        live = self.env.claude / "projects" / "-Users-me-old-app" / f"{sid}.jsonl"
        self.assertEqual(live.read_bytes(), transcript.read_bytes())
        self.assertEqual((self.env.claude / "file-history" / sid / "x@v1").read_text(), "old file")
        self.assertEqual(self.env.session_folders()[0].parent.name, "old-app")

    def test_import_command_is_answered_by_the_hook_without_the_model(self):
        self.archived_then_deleted()
        result = self.env.hook({"hook_event_name": "UserPromptExpansion", "session_id": SID_B,
                                "command_name": "session-archive:import", "command_args": "--list"})
        decision = json.loads(result.stdout)
        self.assertEqual(decision["decision"], "block")
        self.assertIn("archived sessions are missing", decision["reason"])

    def test_other_commands_pass_through_the_hook_untouched(self):
        result = self.env.hook({"hook_event_name": "UserPromptExpansion", "session_id": SID_B,
                                "command_name": "commit-commands:commit", "command_args": ""})
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stdout.strip(), "")

    def test_bad_arguments_get_usage_instead_of_a_crash(self):
        result = self.env.hook({"hook_event_name": "UserPromptExpansion", "session_id": SID_B,
                                "command_name": "session-archive:import", "command_args": "--bogus"})
        self.assertIn("usage: /session-archive:import", json.loads(result.stdout)["reason"])


# -----------------------------------------------------------------------------
# Manual save: /session-archive:save answered by the hook
# -----------------------------------------------------------------------------

class SaveCommandTests(ArchiveTestCase):
    def save_command(self, args: str, sid: str, transcript: Path, cwd: Path) -> str:
        result = self.env.hook({"hook_event_name": "UserPromptExpansion", "session_id": sid,
                                "transcript_path": str(transcript), "cwd": str(cwd),
                                "command_name": "session-archive:save", "command_args": args})
        decision = json.loads(result.stdout)
        self.assertEqual(decision["decision"], "block")
        return decision["reason"]

    def test_save_archives_the_live_session_and_reports_only_new_messages(self):
        repo = git_init(self.env.work / "app")
        t = self.env.transcript(SID_A, repo)
        t.user("first").assistant("one").flush()
        self.assertIn("saved 2 new messages (2 total)", self.save_command("", SID_A, t.path, repo))
        t.user("second").assistant("two").flush()
        self.assertIn("saved 2 new messages (4 total)", self.save_command("", SID_A, t.path, repo))
        self.assertEqual(len(self.env.session_folders()), 1)

    def test_save_all_archives_every_transcript_on_disk(self):
        app = git_init(self.env.work / "app")
        web = git_init(self.env.work / "web")
        a = self.env.transcript(SID_A, app)
        a.user("a").assistant("a").flush()
        self.env.transcript(SID_B, web).user("b").assistant("b").flush()
        self.assertIn("backfilled 2 sessions", self.save_command("all", SID_A, a.path, app))
        self.assertEqual(len(self.env.session_folders()), 2)

    def test_save_as_the_first_message_says_there_is_nothing_yet(self):
        repo = git_init(self.env.work / "app")
        missing = self.env.claude / "projects" / "-app" / f"{SID_A}.jsonl"
        self.assertIn("nothing to save yet", self.save_command("", SID_A, missing, repo))

    def test_save_with_unknown_arguments_shows_usage(self):
        repo = git_init(self.env.work / "app")
        t = self.env.transcript(SID_A, repo)
        t.user("hi").assistant("hello").flush()
        self.assertIn("usage: /session-archive:save [all]", self.save_command("everything", SID_A, t.path, repo))
        self.assertEqual(self.env.session_folders(), [])


# -----------------------------------------------------------------------------
# Everything shown in the chat: screenshots, media, sent files, widgets
# -----------------------------------------------------------------------------

JPEG = b"\xff\xd8\xff\xe0" + bytes(range(48))
PNG = b"\x89PNG\r\n\x1a\n" + bytes(range(64))


class ShownInChatTests(ArchiveTestCase):
    def call(self, tool_id: str, name: str, **tool_input) -> dict:
        return {"type": "tool_use", "id": tool_id, "name": name, "input": tool_input}

    def post_tool_use(self, sid, cwd, transcript, tool_id, name, tool_input):
        result = self.env.hook({"hook_event_name": "PostToolUse", "session_id": sid,
                                "transcript_path": str(transcript), "cwd": str(cwd),
                                "tool_name": name, "tool_use_id": tool_id, "tool_input": tool_input})
        self.assertEqual(result.returncode, 0)

    def test_browser_screenshot_is_saved_and_shown_under_its_tool_call(self):
        repo = git_init(self.env.work / "app")
        t = self.env.transcript(SID_A, repo)
        t.user("check the page").assistant(tools=[self.call("toolu_s", "mcp__Claude_Browser__computer", action="screenshot")])
        t.tool_result_image("toolu_s", "image/jpeg", base64.b64encode(JPEG).decode()).flush()
        self.save(SID_A, repo)

        folder = self.env.session_folders()[0]
        shot, = (folder / "attachments").iterdir()
        self.assertEqual(shot.read_bytes(), JPEG)
        chat = (folder / "chat.md").read_text()
        self.assertLess(chat.index("mcp__Claude_Browser__computer"), chat.index(f"attachments/{shot.name}"))

    def test_device_screenshot_read_from_tmp_survives_tmp_cleanup(self):
        repo = git_init(self.env.work / "app")
        shot = self.env.tmp / "scratchpad" / "pixel-dark.png"
        shot.parent.mkdir()
        shot.write_bytes(PNG)
        t = self.env.transcript(SID_A, repo)
        t.user("how does it look?").assistant(tools=[self.call("toolu_r", "Read", file_path=str(shot))]).flush()
        self.post_tool_use(SID_A, repo, t.path, "toolu_r", "Read", {"file_path": str(shot)})
        t.tool_result_image("toolu_r", "image/png", base64.b64encode(PNG).decode()).flush()
        shot.unlink()  # /tmp gets cleaned
        self.save(SID_A, repo)

        folder = self.env.session_folders()[0]
        rec, = versions(folder)
        self.assertEqual((folder / rec["snapshot"]).read_bytes(), PNG)
        self.assertIn(rec["snapshot"], (folder / "chat.md").read_text())

    def test_reading_source_code_is_not_copied(self):
        repo = git_init(self.env.work / "app")
        (repo / "main.py").write_text("print(1)")
        t = self.env.transcript(SID_A, repo)
        t.user("read it").assistant(tools=[self.call("toolu_c", "Read", file_path=str(repo / "main.py"))]).flush()
        self.post_tool_use(SID_A, repo, t.path, "toolu_c", "Read", {"file_path": str(repo / "main.py")})
        self.save(SID_A, repo)
        self.assertFalse((self.env.session_folders()[0] / "artifacts" / "versions.jsonl").exists())

    def test_every_file_sent_to_the_chat_is_kept_with_its_caption(self):
        repo = git_init(self.env.work / "app")
        png, pdf = self.env.tmp / "shot.png", self.env.tmp / "report.pdf"
        png.write_bytes(PNG)
        pdf.write_bytes(b"%PDF-1.4 test")
        files = [str(png), str(pdf)]
        t = self.env.transcript(SID_A, repo)
        t.user("send me both").assistant(tools=[self.call("toolu_f", "SendUserFile", files=files, caption="v2 on the Pixel")]).flush()
        self.post_tool_use(SID_A, repo, t.path, "toolu_f", "SendUserFile", {"files": files})
        png.unlink()
        pdf.unlink()
        self.save(SID_A, repo)

        folder = self.env.session_folders()[0]
        recs = {Path(r["path"]).name: r for r in versions(folder)}
        self.assertEqual((folder / recs["shot.png"]["snapshot"]).read_bytes(), PNG)
        self.assertEqual((folder / recs["report.pdf"]["snapshot"]).read_bytes(), b"%PDF-1.4 test")
        chat = (folder / "chat.md").read_text()
        self.assertIn(f"![shot.png](<{recs['shot.png']['snapshot']}>)", chat)
        self.assertIn('"v2 on the Pixel"', chat)

    def test_inline_widget_is_saved_as_a_file(self):
        repo = git_init(self.env.work / "app")
        svg = '<svg viewBox="0 0 10 10"><circle r="4"/></svg>'
        t = self.env.transcript(SID_A, repo)
        t.user("draw it").assistant(tools=[self.call("toolu_w", "mcp__visualize__show_widget", widget_code=svg)]).flush()
        self.save(SID_A, repo)

        widget = self.env.session_folders()[0] / "widgets" / "toolu_w.svg"
        self.assertEqual(widget.read_text(), svg)

    def test_tool_output_is_shown_and_long_output_is_cut_with_a_pointer_to_raw(self):
        repo = git_init(self.env.work / "app")
        t = self.env.transcript(SID_A, repo)
        t.user("run it").assistant(tools=[self.call("toolu_a", "Bash", command="make"),
                                          self.call("toolu_b", "Bash", command="cat big.log")])
        t.tool_result("toolu_a", "build ok\n```\nnested fence\n```")
        t.tool_result("toolu_b", "x" * 5000).flush()
        self.save(SID_A, repo)

        chat = (self.env.session_folders()[0] / "chat.md").read_text()
        self.assertIn("nested fence", chat)
        self.assertIn("  ````text", chat)  # fence longer than any backtick run in the output
        self.assertIn("first 2000 of 5000 characters; the full output is in raw/", chat)
        self.assertNotIn("x" * 2001, chat)

    def test_sessions_archived_by_an_older_version_are_rerendered_once(self):
        repo = git_init(self.env.work / "app")
        t = self.env.transcript(SID_A, repo)
        t.user("check").assistant(tools=[self.call("toolu_s", "mcp__Claude_Browser__computer", action="screenshot")])
        t.tool_result_image("toolu_s", "image/jpeg", base64.b64encode(JPEG).decode()).flush()
        self.save(SID_A, repo)
        folder = self.env.session_folders()[0]
        shutil.rmtree(folder / "attachments")  # what an older version left behind
        meta = json.loads((folder / "meta.json").read_text())
        meta["archiver_version"] = "1.0.0"
        (folder / "meta.json").write_text(json.dumps(meta))

        self.save(SID_A, repo)  # transcript unchanged, but the archiver is newer
        self.assertEqual(len(list((folder / "attachments").iterdir())), 1)



# -----------------------------------------------------------------------------
# Throttling: after-reply saves at most every N minutes
# -----------------------------------------------------------------------------

class ThrottleTests(ArchiveTestCase):
    def stop(self, event: str, t, repo) -> None:
        result = self.env.hook({"hook_event_name": event, "session_id": SID_A,
                                "transcript_path": str(t.path), "cwd": str(repo)})
        self.assertEqual(result.returncode, 0)

    def messages(self) -> int:
        return json.loads((self.env.session_folders()[0] / "meta.json").read_text())["messages"]

    def test_second_reply_within_the_window_is_not_saved_but_session_end_is(self):
        repo = git_init(self.env.work / "app")
        t = self.env.transcript(SID_A, repo)
        t.user("one").assistant("1").flush()
        self.stop("Stop", t, repo)
        t.user("two").assistant("2").flush()
        self.stop("Stop", t, repo)
        self.stop("SubagentStop", t, repo)
        self.assertEqual(self.messages(), 2)
        self.stop("SessionEnd", t, repo)
        self.assertEqual(self.messages(), 4)

    def test_zero_minutes_saves_after_every_reply(self):
        self.env.env["SESSION_ARCHIVE_MIN_SYNC_MINUTES"] = "0"
        repo = git_init(self.env.work / "app")
        t = self.env.transcript(SID_A, repo)
        t.user("one").assistant("1").flush()
        self.stop("Stop", t, repo)
        t.user("two").assistant("2").flush()
        self.stop("Stop", t, repo)
        self.assertEqual(self.messages(), 4)

    def test_reply_after_the_window_is_saved(self):
        repo = git_init(self.env.work / "app")
        t = self.env.transcript(SID_A, repo)
        t.user("one").assistant("1").flush()
        self.stop("Stop", t, repo)
        meta = self.env.session_folders()[0] / "meta.json"
        old = meta.stat().st_mtime - 11 * 60
        os.utime(meta, (old, old))
        t.user("two").assistant("2").flush()
        self.stop("Stop", t, repo)
        self.assertEqual(self.messages(), 4)


class MachineTests(ArchiveTestCase):
    def test_chat_records_machine_and_signed_in_account(self):
        (self.env.claude / ".claude.json").parent.mkdir(parents=True, exist_ok=True)
        (self.env.claude / ".claude.json").write_text(json.dumps({"oauthAccount": {"emailAddress": "me@example.com"}}))
        repo = git_init(self.env.work / "app")
        self.env.transcript(SID_A, repo).user("hi").assistant("hello").flush()
        self.save(SID_A, repo)

        folder = self.env.session_folders()[0]
        meta = json.loads((folder / "meta.json").read_text())
        self.assertEqual(meta["account"], "me@example.com")
        self.assertTrue(meta["machine"])
        self.assertIn("me@example.com", (folder / "chat.md").read_text())

if __name__ == "__main__":
    unittest.main()


# -----------------------------------------------------------------------------
# Import edge cases a real user runs into
# -----------------------------------------------------------------------------

class ImportEdgeCaseTests(ImportTests):
    def test_live_copy_that_kept_growing_after_the_last_save_is_not_a_conflict(self):
        repo = git_init(self.env.work / "app")
        t = self.env.transcript(SID_A, repo)
        t.user("first").assistant("one").flush()
        self.save(SID_A, repo)
        t.user("after the last save").assistant("two").flush()  # the session went on
        before = t.path.read_bytes()

        out = self.env.cli("import")

        self.assertIn("0 restored, 0 extended, 1 already in Claude Code, 0 conflicts", out.stdout)
        self.assertEqual(t.path.read_bytes(), before)

    def test_unknown_session_id_says_so(self):
        self.archived_then_deleted()
        out = self.env.cli("import", "ffffffff")
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertIn("no archived session starts with 'ffffffff'", out.stdout)

    def test_restores_even_when_the_project_folder_is_gone(self):
        t, original = self.archived_then_deleted()
        shutil.rmtree(self.env.work / "app")
        out = self.env.cli("import")
        self.assertIn("1 restored", out.stdout)
        self.assertEqual(t.path.read_bytes(), original)

    def test_restores_into_a_claude_folder_that_has_no_projects_yet(self):
        t, original = self.archived_then_deleted()
        shutil.rmtree(self.env.claude / "projects")
        out = self.env.cli("import")
        self.assertIn("1 restored", out.stdout)
        self.assertEqual(t.path.read_bytes(), original)

    def test_resume_hint_is_a_shell_safe_command_for_odd_folder_names(self):
        t, _ = self.archived_then_deleted(name="my app's (v2)")
        out = self.env.cli("import")
        line = next(l for l in out.stdout.splitlines() if "--resume" in l)
        hint = line.split('"  ', 1)[1]
        parts = shlex.split(hint)  # a user can paste it as is
        self.assertEqual(parts[0], "cd")
        self.assertEqual(parts[1], str(self.env.work / "my app's (v2)"))
        self.assertEqual(parts[-1], SID_A)

    def test_archive_without_its_raw_transcript_is_reported_not_crashed(self):
        self.archived_then_deleted()
        folder = self.env.session_folders()[0]
        (folder / "raw" / f"{SID_A}.jsonl").unlink()
        out = self.env.cli("import")
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertIn("1 without a raw transcript", out.stdout)

    def test_unreadable_meta_is_skipped_and_the_rest_still_import(self):
        a, _ = self.archived_then_deleted(SID_A, "app")
        b, original_b = self.archived_then_deleted(SID_B, "web")
        broken = next(f for f in self.env.session_folders() if f.name.endswith(SID_A[:8]))
        (broken / "meta.json").write_text("{ not json")
        out = self.env.cli("import")
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertEqual(b.path.read_bytes(), original_b)
        self.assertFalse(a.path.exists())

    def test_session_archived_on_another_computer_lands_under_this_claude_folder(self):
        t, original = self.archived_then_deleted()
        folder = self.env.session_folders()[0]
        meta = json.loads((folder / "meta.json").read_text())
        meta["source_transcript"] = f"/Users/someone-else/.claude/projects/{t.path.parent.name}/{SID_A}.jsonl"
        (folder / "meta.json").write_text(json.dumps(meta))
        self.env.cli("import")
        self.assertEqual(t.path.read_bytes(), original)

    def test_list_changes_nothing_on_disk(self):
        t, _ = self.archived_then_deleted()
        self.env.cli("import", "--list")
        self.assertFalse(t.path.exists())
        self.assertFalse((self.env.claude / "file-history" / SID_A).exists())


# -----------------------------------------------------------------------------
# Archiving never breaks, whatever the transcript or folder looks like
# -----------------------------------------------------------------------------

class RobustnessTests(ArchiveTestCase):
    def test_half_written_last_line_is_kept_exactly_and_completed_later(self):
        repo = git_init(self.env.work / "app")
        t = self.env.transcript(SID_A, repo)
        t.user("hi").assistant("hello").flush()
        record = json.dumps({"type": "user", "timestamp": "2026-11-17T10:00:00Z", "cwd": str(repo),
                             "message": {"role": "user", "content": "next question"}})
        with t.path.open("a", encoding="utf-8") as fh:
            fh.write(record[:25])  # Claude Code is mid-write
        self.save(SID_A, repo)
        with t.path.open("a", encoding="utf-8") as fh:
            fh.write(record[25:] + "\n")
        self.save(SID_A, repo)

        folder = self.env.session_folders()[0]
        self.assertEqual(sha(folder / "raw" / f"{SID_A}.jsonl"), sha(t.path))
        self.assertIn("next question", (folder / "chat.md").read_text())

    def test_unicode_emoji_and_invalid_bytes_do_not_break_the_chat(self):
        repo = git_init(self.env.work / "app")
        t = self.env.transcript(SID_A, repo)
        t.user("नमस्ते 👋 ñ").assistant("こんにちは").flush()
        with t.path.open("ab") as fh:
            fh.write(b'{"type": "user", "message": "\xff\xfe broken"}\n')
        self.save(SID_A, repo)
        folder = self.env.session_folders()[0]
        chat = (folder / "chat.md").read_text(encoding="utf-8")
        self.assertIn("नमस्ते 👋 ñ", chat)
        self.assertIn("こんにちは", chat)
        self.assertEqual(sha(folder / "raw" / f"{SID_A}.jsonl"), sha(t.path))

    def test_record_shapes_this_version_does_not_know_are_skipped(self):
        repo = git_init(self.env.work / "app")
        t = self.env.transcript(SID_A, repo)
        t.user("before").flush()
        odd = [[1, 2], "just a string", {"type": "user", "timestamp": 12345, "message": {"content": {"x": 1}}},
               {"type": "assistant", "message": {"content": ["plain string block", None, 7]}},
               {"type": "user", "message": {"content": [{"type": "tool_result", "content": 42}]}}]
        with t.path.open("a", encoding="utf-8") as fh:
            fh.writelines(json.dumps(rec) + "\n" for rec in odd)
        t.user("after").assistant("still fine").flush()
        self.save(SID_A, repo)
        chat = (self.env.session_folders()[0] / "chat.md").read_text()
        self.assertIn("before", chat)
        self.assertIn("still fine", chat)

    def test_archive_folder_with_spaces_and_tilde(self):
        repo = git_init(self.env.work / "app")
        self.env.transcript(SID_A, repo).user("hi").assistant("hello").flush()
        self.env.env["HOME"] = str(self.env.tmp)
        self.env.env["SESSION_ARCHIVE_DIR"] = "~/My Drive/AI archive"
        self.save(SID_A, repo)
        self.assertTrue(list((self.env.tmp / "My Drive" / "AI archive").glob("*/*/chat.md")))

    def test_stop_hook_for_a_transcript_that_does_not_exist_is_a_no_op(self):
        result = self.env.hook({"hook_event_name": "Stop", "session_id": SID_A,
                                "transcript_path": str(self.env.tmp / "gone.jsonl"), "cwd": str(self.env.work)})
        self.assertEqual(result.returncode, 0)
        self.assertEqual(self.env.session_folders(), [])

    def test_unusable_archive_folder_never_blocks_a_turn(self):
        repo = git_init(self.env.work / "app")
        t = self.env.transcript(SID_A, repo)
        t.user("hi").assistant("hello").flush()
        blocker = self.env.tmp / "not-a-folder"
        blocker.write_text("a file where the archive should be")
        self.env.env["SESSION_ARCHIVE_DIR"] = str(blocker)
        result = self.env.hook({"hook_event_name": "Stop", "session_id": SID_A,
                                "transcript_path": str(t.path), "cwd": str(repo)})
        self.assertEqual(result.returncode, 0)

    def test_index_survives_another_session_with_broken_meta(self):
        app = git_init(self.env.work / "app")
        self.env.transcript(SID_A, app).user("a").assistant("a").flush()
        self.save(SID_A, app)
        (self.env.session_folders()[0] / "meta.json").write_text("garbage")
        web = git_init(self.env.work / "web")
        self.env.transcript(SID_B, web).user("b").assistant("b").flush()
        self.save(SID_B, web)
        self.assertIn("web/", (self.env.archive / "README.md").read_text())

    def test_saving_the_same_session_twice_in_parallel_with_backfill_keeps_one_folder(self):
        repo = git_init(self.env.work / "app")
        t = self.env.transcript(SID_A, repo)
        for i in range(20):
            t.user(f"q{i}").assistant(f"a{i}")
        t.flush()
        script = Path(__file__).parents[1] / "scripts" / "archive.py"
        procs = [subprocess.Popen([sys.executable, str(script), *args], env=self.env.env,
                                  stdout=subprocess.PIPE, stderr=subprocess.PIPE)
                 for args in (["backfill"], ["save", "--session", SID_A, "--cwd", str(repo)]) * 3]
        for p in procs:
            _, err = p.communicate(timeout=120)
            self.assertEqual(p.returncode, 0, err)
        self.assertEqual(len(self.env.session_folders()), 1)


# -----------------------------------------------------------------------------
# `archive.py command`: what the mod runs for /session-archive:save and :import
# -----------------------------------------------------------------------------

class ModCommandTests(ArchiveTestCase):
    def command(self, name: str, args: str = "", sid: str = SID_A, cwd: Path | None = None) -> str:
        payload = {"command_name": f"session-archive:{name}", "command_args": args,
                   "session_id": sid, "cwd": str(cwd or self.env.work)}
        result = self.env.cli("command", stdin=json.dumps(payload))
        self.assertEqual(result.returncode, 0, result.stderr)
        return result.stdout.strip()

    def test_save_finds_the_live_transcript_by_session_id(self):
        repo = git_init(self.env.work / "app")
        t = self.env.transcript(SID_A, repo)
        t.user("first").assistant("one").flush()
        self.assertIn("saved 2 new messages (2 total)", self.command("save", cwd=repo))
        self.assertEqual(self.env.session_folders()[0].parent.name, "app")

    def test_save_all_and_import_list(self):
        app = git_init(self.env.work / "app")
        self.env.transcript(SID_A, app).user("a").assistant("a").flush()
        self.assertIn("backfilled 1 sessions", self.command("save", "all"))
        self.assertIn("0 of 1 archived sessions are missing", self.command("import", "--list"))

    def test_import_restores_a_deleted_session(self):
        repo = git_init(self.env.work / "app")
        t = self.env.transcript(SID_A, repo)
        t.user("keep me").assistant("ok").flush()
        self.command("save", cwd=repo)
        original = t.path.read_bytes()
        t.path.unlink()
        self.assertIn("1 restored", self.command("import"))
        self.assertEqual(t.path.read_bytes(), original)

    def test_save_before_the_first_message_says_nothing_to_save(self):
        self.assertIn("nothing to save yet", self.command("save"))

    def test_bad_arguments_and_bad_input_answer_instead_of_crashing(self):
        self.assertIn("usage: /session-archive:save [all]", self.command("save", "everything"))
        self.assertIn("usage: /session-archive:import", self.command("import", "--bogus"))
        result = self.env.cli("command", stdin="not json")
        self.assertEqual(result.returncode, 0)
        self.assertIn("failed:", result.stdout)

    def test_archive_dir_from_the_plugin_option_is_used(self):
        repo = git_init(self.env.work / "app")
        self.env.transcript(SID_A, repo).user("a").assistant("a").flush()
        custom = self.env.tmp / "chosen folder"
        del self.env.env["SESSION_ARCHIVE_DIR"]
        payload = {"command_name": "session-archive:save", "command_args": "", "session_id": SID_A, "cwd": str(repo)}
        result = self.env.cli("--archive-dir", str(custom), "command", stdin=json.dumps(payload))
        self.assertIn(str(custom.resolve()), result.stdout)
        self.assertTrue(list(custom.glob("*/*/chat.md")))
