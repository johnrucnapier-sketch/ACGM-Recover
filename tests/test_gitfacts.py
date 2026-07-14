from __future__ import annotations

import tempfile
import unittest
from unittest.mock import patch
from pathlib import Path

from acgm_recover.gitfacts import _run_git_bytes, _status_summary, inspect_git
from acgm_recover.util import RecoverError, stat_snapshot

from helpers import create_git_project, git


class GitFactsTests(unittest.TestCase):
    def test_git_read_does_not_change_index_and_disables_fsmonitor(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            project, _ = create_git_project(root)
            marker = root / "fsmonitor-ran"
            helper = root / "malicious-fsmonitor.sh"
            helper.write_text(f"#!/bin/sh\ntouch '{marker}'\n", encoding="utf-8")
            helper.chmod(0o755)
            git(project, "config", "core.fsmonitor", str(helper))
            index = Path(git(project, "rev-parse", "--git-path", "index"))
            if not index.is_absolute():
                index = project / index
            before = stat_snapshot(index)
            facts = inspect_git(project)
            after = stat_snapshot(index)
            self.assertEqual(before, after)
            self.assertFalse(marker.exists())
            self.assertTrue(facts["source_stable_during_read"])

    def test_remote_credentials_are_not_returned(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            project, _ = create_git_project(root)
            git(project, "remote", "add", "origin", "https://user:pass@example.invalid/org/repo.git?token=secret")
            facts = inspect_git(project)
            value = facts["remotes"][0]["url"]
            self.assertNotIn("user", value)
            self.assertNotIn("pass", value)
            self.assertNotIn("token", value)
            self.assertEqual(value, "https://example.invalid/org/repo.git")

    def test_git_status_does_not_execute_clean_or_process_filter(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            project, _ = create_git_project(root)
            marker = root / "filter-ran"
            git(project, "config", "filter.evil.clean", f"touch '{marker}'; cat")
            git(project, "config", "filter.evil.process", f"touch '{marker}'; cat")
            git(project, "config", "filter.evil.required", "true")
            (project / ".gitattributes").write_text("README.md filter=evil\n", encoding="utf-8")
            facts = inspect_git(project)
            self.assertFalse(marker.exists())
            self.assertTrue(facts["status"]["readable"])

    def test_long_filter_driver_name_is_also_disabled(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            project, _ = create_git_project(root)
            marker = root / "long-filter-ran"
            driver = "a" * 201
            git(project, "config", f"filter.{driver}.clean", f"touch '{marker}'; cat")
            (project / ".gitattributes").write_text(f"README.md filter={driver}\n", encoding="utf-8")
            facts = inspect_git(project)
            self.assertFalse(marker.exists())
            self.assertTrue(facts["status"]["readable"])

    def test_filter_driver_name_with_equals_cannot_escape_override(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            project, _ = create_git_project(root)
            marker = root / "equals-filter-ran"
            driver = "foo=bar"
            git(project, "config", f"filter.{driver}.clean", f"touch '{marker}'; cat")
            git(project, "config", f"filter.{driver}.process", f"touch '{marker}'; cat")
            git(project, "config", f"filter.{driver}.required", "true")
            (project / ".gitattributes").write_text(f"README.md filter={driver}\n", encoding="utf-8")
            facts = inspect_git(project)
            self.assertFalse(marker.exists())
            self.assertTrue(facts["status"]["readable"])

    def test_inherited_git_repository_environment_is_ignored(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            project_a, _ = create_git_project(root / "a")
            project_b, _ = create_git_project(root / "b")
            (project_b / "other.txt").write_text("different\n", encoding="utf-8")
            git(project_b, "add", "other.txt")
            git(
                project_b,
                "-c",
                "user.name=Fixture",
                "-c",
                "user.email=fixture@example.invalid",
                "commit",
                "-m",
                "different",
            )
            head_a = git(project_a, "rev-parse", "HEAD")
            with patch.dict(
                "os.environ",
                {"GIT_DIR": str(project_b / ".git"), "GIT_WORK_TREE": str(project_b)},
                clear=False,
            ):
                facts = inspect_git(project_a)
            self.assertEqual(facts["head"], head_a)

    def test_inherited_git_trace_cannot_write_into_source(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            project, _ = create_git_project(root)
            marker = project / "git-trace-written.log"
            with patch.dict("os.environ", {"GIT_TRACE": str(marker)}, clear=False):
                inspect_git(project)
            self.assertFalse(marker.exists())

    def test_git_output_is_bounded_before_accumulation(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            project, _ = create_git_project(root)
            for index in range(20):
                (project / f"untracked-{index:03d}-{'x' * 30}.txt").write_text("x\n", encoding="utf-8")
            with self.assertRaisesRegex(RecoverError, "git_output_budget_exceeded"):
                _run_git_bytes(
                    project,
                    ["status", "--porcelain=v2", "-z", "--untracked-files=all"],
                    max_output_bytes=64,
                )

    def test_filter_config_read_failure_does_not_fall_through_to_status(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project, _ = create_git_project(Path(tmp))
            with patch(
                "acgm_recover.gitfacts._run_git_text",
                side_effect=RecoverError("git_output_budget_exceeded"),
            ), patch("acgm_recover.gitfacts._run_git_bytes") as status_runner:
                result = _status_summary(project)
            self.assertFalse(result["readable"])
            status_runner.assert_not_called()


if __name__ == "__main__":
    unittest.main()
