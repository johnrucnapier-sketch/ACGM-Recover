from __future__ import annotations

import hashlib
import os
import subprocess
import sys
import tempfile
import unicodedata
import unittest
from pathlib import Path
from unittest.mock import patch

from claude_code_recover.analysis import analyze_project
from claude_code_recover.bundle import build_bundle
from claude_code_recover.util import RecoverError
from claude_code_recover.util import atomic_rename_noreplace, has_extra_acl
from claude_code_recover.verify import verify_bundle

from helpers import create_git_project, create_sources, git


def snapshot_files(roots: list[Path]) -> dict[str, tuple[int, int, int, str]]:
    result: dict[str, tuple[int, int, int, str]] = {}
    for root in roots:
        for current, dirs, names in os.walk(root, followlinks=False):
            dirs[:] = [name for name in dirs if not (Path(current) / name).is_symlink()]
            for name in names:
                path = Path(current) / name
                if path.is_symlink() or not path.is_file():
                    continue
                source_stat = path.stat(follow_symlinks=False)
                result[str(path)] = (
                    source_stat.st_size,
                    source_stat.st_mode,
                    source_stat.st_mtime_ns,
                    hashlib.sha256(path.read_bytes()).hexdigest(),
                )
    return result


class SourceSafetyTests(unittest.TestCase):
    def test_analyze_and_build_do_not_modify_any_source_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            project, linked = create_git_project(root, with_worktree=True)
            claude_root, metadata_root, auxiliary_root, _ = create_sources(root, project, linked)
            source_roots = [project, linked, claude_root, metadata_root, auxiliary_root]
            before = snapshot_files([path for path in source_roots if path is not None])
            analysis = analyze_project(
                project,
                claude_projects_roots=[claude_root],
                metadata_roots=[metadata_root],
                auxiliary_roots=[auxiliary_root],
            )
            build_bundle(analysis, root / "outside-bundle")
            after = snapshot_files([path for path in source_roots if path is not None])
            self.assertEqual(before, after)

    def test_output_cannot_be_claude_data_sibling(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            project, _ = create_git_project(root)
            claude_root, metadata_root, auxiliary_root, _ = create_sources(root, project)
            analysis = analyze_project(
                project,
                claude_projects_roots=[claude_root],
                metadata_roots=[metadata_root],
                auxiliary_roots=[auxiliary_root],
            )
            with self.assertRaisesRegex(RecoverError, "source_output_overlap"):
                build_bundle(analysis, claude_root.parent / "RECOVERY-BUNDLE")
            with self.assertRaisesRegex(RecoverError, "source_output_overlap"):
                build_bundle(analysis, metadata_root.parent / "RECOVERY-BUNDLE")

    def test_output_cannot_enter_separate_git_directory(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            project = root / "project"
            project.mkdir()
            git_data = root / "git-data"
            subprocess.run(
                ["git", "init", "--initial-branch=main", "--separate-git-dir", str(git_data), str(project)],
                check=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            (project / "README.md").write_text("fixture\n", encoding="utf-8")
            git(project, "add", "README.md")
            git(
                project,
                "-c",
                "user.name=Fixture",
                "-c",
                "user.email=fixture@example.invalid",
                "commit",
                "-m",
                "fixture",
            )
            analysis = analyze_project(
                project,
                claude_projects_roots=[],
                metadata_roots=[],
                auxiliary_roots=[],
            )
            with self.assertRaisesRegex(RecoverError, "source_output_overlap"):
                build_bundle(analysis, git_data / "RECOVERY-BUNDLE")

    def test_output_cannot_enter_shared_clone_alternate_object_store(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            donor, _ = create_git_project(root / "donor")
            shared = root / "shared"
            subprocess.run(
                ["git", "clone", "--shared", str(donor), str(shared)],
                check=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            analysis = analyze_project(
                shared,
                claude_projects_roots=[],
                metadata_roots=[],
                auxiliary_roots=[],
            )
            alternate = Path(
                (shared / ".git" / "objects" / "info" / "alternates").read_text(encoding="utf-8").strip()
            ).resolve(strict=True)
            self.assertIn(alternate, analysis["source_roots"])
            with self.assertRaisesRegex(RecoverError, "source_output_overlap"):
                build_bundle(analysis, alternate / "RECOVERY-BUNDLE")

    def test_case_and_unicode_aliases_cannot_enter_project(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            project, _ = create_git_project(root / "main-repo")
            analysis = analyze_project(
                project,
                claude_projects_roots=[],
                metadata_roots=[],
                auxiliary_roots=[],
            )
            case_alias = project.parent / project.name.upper()
            if case_alias.exists() and case_alias.stat().st_ino == project.stat().st_ino:
                with self.assertRaisesRegex(RecoverError, "source_output_overlap"):
                    build_bundle(analysis, case_alias / "CASE-BUNDLE")

            unicode_project = root / "Caf\u00e9"
            unicode_project.mkdir()
            unicode_analysis = analyze_project(
                unicode_project,
                claude_projects_roots=[],
                metadata_roots=[],
                auxiliary_roots=[],
            )
            unicode_alias = root / unicodedata.normalize("NFD", unicode_project.name)
            if unicode_alias.exists() and unicode_alias.stat().st_ino == unicode_project.stat().st_ino:
                with self.assertRaisesRegex(RecoverError, "source_output_overlap"):
                    build_bundle(unicode_analysis, unicode_alias / "UNICODE-BUNDLE")

    @unittest.skipUnless(sys.platform == "darwin", "macOS extended ACL fixture")
    def test_inherited_acl_is_removed_and_verifier_rejects_acl(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            project, _ = create_git_project(root / "project")
            output_parent = root / "output-parent"
            output_parent.mkdir()
            subprocess.run(
                [
                    "/bin/chmod",
                    "+a",
                    "everyone allow read,execute,file_inherit,directory_inherit",
                    str(output_parent),
                ],
                check=True,
            )
            analysis = analyze_project(
                project,
                claude_projects_roots=[],
                metadata_roots=[],
                auxiliary_roots=[],
            )
            bundle = build_bundle(analysis, output_parent / "bundle")
            self.assertFalse(any(has_extra_acl(path) for path in [bundle, *bundle.rglob("*")]))
            self.assertTrue(verify_bundle(bundle)["ok"])

            subprocess.run(
                ["/bin/chmod", "+a", "everyone allow read", str(bundle / "private" / "SOURCE_MAP.json")],
                check=True,
            )
            result = verify_bundle(bundle)
            self.assertFalse(result["ok"])
            self.assertIn("extended_acl_present", result["errors"])

    def test_atomic_publish_never_replaces_racing_output(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            project, _ = create_git_project(root)
            claude_root, metadata_root, auxiliary_root, _ = create_sources(root, project)
            analysis = analyze_project(
                project,
                claude_projects_roots=[claude_root],
                metadata_roots=[metadata_root],
                auxiliary_roots=[auxiliary_root],
            )
            output = root / "racing-output"

            def race(source: Path, destination: Path) -> None:
                destination.mkdir(mode=0o700)
                atomic_rename_noreplace(source, destination)

            with patch("claude_code_recover.bundle.atomic_rename_noreplace", side_effect=race):
                with self.assertRaisesRegex(RecoverError, "output_race_detected"):
                    build_bundle(analysis, output)
            self.assertTrue(output.is_dir())
            self.assertEqual(list(output.iterdir()), [])

    def test_lock_is_removed_when_acl_hardening_fails(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            project, _ = create_git_project(root)
            analysis = analyze_project(
                project,
                claude_projects_roots=[],
                metadata_roots=[],
                auxiliary_roots=[],
            )
            output = root / "bundle"
            lock = root / ".bundle.claude-code-recover.lock"
            with patch("claude_code_recover.bundle.clear_extra_acl", side_effect=RecoverError("acl_control_failed")):
                with self.assertRaisesRegex(RecoverError, "acl_control_failed"):
                    build_bundle(analysis, output)
            self.assertFalse(lock.exists())
            self.assertFalse(output.exists())


if __name__ == "__main__":
    unittest.main()
