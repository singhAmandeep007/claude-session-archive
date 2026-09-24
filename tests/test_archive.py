"""
Behavior tests for session-archive. Standard library only.

Run:  python3 -m unittest discover -s tests -v
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import subprocess
import sys
import unittest
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


if __name__ == "__main__":
    unittest.main()
