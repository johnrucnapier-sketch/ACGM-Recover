from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path

from claude_code_recover.analysis import analyze_project
from claude_code_recover.bundle import build_bundle
from claude_code_recover.util import RecoverError, sha256_file
from claude_code_recover.verify import verify_bundle

from helpers import SECRET_SENTINEL, create_git_project, create_sources


class AnalysisBundleTests(unittest.TestCase):
    def _analysis(self, root: Path, *, worktree: bool = True, annotations: Path | None = None):
        project, linked = create_git_project(root, with_worktree=worktree)
        claude_root, metadata_root, auxiliary_root, main_path = create_sources(root, project, linked)
        analysis = analyze_project(
            project,
            claude_projects_roots=[claude_root],
            metadata_roots=[metadata_root],
            auxiliary_roots=[auxiliary_root],
            annotations_path=annotations,
        )
        return project, linked, main_path, analysis

    def test_analysis_keeps_worktrees_and_lineage_separate(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project, linked, _, analysis = self._analysis(Path(tmp))
            self.assertIsNotNone(linked)
            self.assertEqual(analysis["project"]["worktree_count"], 2)
            self.assertEqual(analysis["summary"]["transcript_records"], 3)
            main = next(row for row in analysis["transcripts"] if row["category"] == "main_transcript")
            self.assertEqual(len(main["structural_worktrees"]), 2)
            children = [row for row in analysis["transcripts"] if row["category"] == "subagent_transcript"]
            self.assertTrue(all(row["parent_transcripts"] for row in children))
            self.assertTrue(all(row["content_project"] == "unknown" for row in analysis["transcripts"]))

    def test_human_correction_models_misopened_cwd(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            project, _ = create_git_project(root)
            claude_root, metadata_root, auxiliary_root, main_path = create_sources(root, project)
            annotations = root / "annotations.json"
            annotations.write_text(
                json.dumps(
                    {
                        "schema_version": "1.0",
                        "transcripts_by_sha256": {
                            sha256_file(main_path): {
                                "content_project": "external-project",
                                "mapping_status": "misopened",
                                "confidence": "verified",
                                "evidence_codes": ["human-confirmation"],
                                "private_content_label": "different project",
                                "human_reviewed": True,
                                "share_approved": True,
                            }
                        },
                        "sessions_by_id": {},
                        "known_gaps": [{"code": "earliest-session-not-found"}],
                    }
                ),
                encoding="utf-8",
            )
            analysis = analyze_project(
                project,
                claude_projects_roots=[claude_root],
                metadata_roots=[metadata_root],
                auxiliary_roots=[auxiliary_root],
                annotations_path=annotations,
            )
            main = next(row for row in analysis["transcripts"] if row["category"] == "main_transcript")
            self.assertEqual(main["structural_project"], "this-project")
            self.assertEqual(main["content_project"], "external-project")
            self.assertEqual(main["mapping_status"], "misopened")
            self.assertIn("structural_and_content_project_differ", [row["code"] for row in analysis["conflicts"]])
            self.assertIn("earliest-session-not-found", [row["code"] for row in analysis["gaps"]])

    def test_bundle_is_atomic_private_and_verifiable(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            project, _, _, analysis = self._analysis(root, worktree=False)
            output = root / "recovery-bundle"
            built = build_bundle(analysis, output)
            self.assertEqual(built, output.resolve())
            self.assertTrue(verify_bundle(output)["ok"])
            source_check = verify_bundle(output, check_sources=True)
            self.assertTrue(source_check["ok"])
            self.assertTrue(all(row["status"] == "unchanged" for row in source_check["sources"]))
            self.assertEqual(oct(output.stat().st_mode & 0o777), "0o700")
            for path in output.rglob("*"):
                if path.is_file():
                    self.assertEqual(oct(path.stat().st_mode & 0o777), "0o600")
            all_text = "".join(path.read_text(encoding="utf-8") for path in output.rglob("*") if path.is_file())
            self.assertNotIn(SECRET_SENTINEL, all_text)
            share_text = "".join(
                path.read_text(encoding="utf-8") for path in (output / "share").rglob("*") if path.is_file()
            )
            self.assertNotIn(str(project), share_text)
            self.assertNotIn("Claude Opus", share_text)
            compatible = json.loads((output / "share/claude-compatible-api/ROUTE.json").read_text())
            self.assertEqual(compatible["identity_assessment"], "not_performed")
            self.assertFalse(compatible["display_label_is_model_identity"])

    def test_existing_or_overlapping_output_is_refused(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            project, _, _, analysis = self._analysis(root, worktree=False)
            existing = root / "existing"
            existing.mkdir()
            with self.assertRaisesRegex(RecoverError, "output_exists"):
                build_bundle(analysis, existing)
            with self.assertRaisesRegex(RecoverError, "source_output_overlap"):
                build_bundle(analysis, project / "bundle")

    def test_verify_detects_tampering_and_extra_files(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _, _, _, analysis = self._analysis(root, worktree=False)
            output = build_bundle(analysis, root / "bundle")
            report = output / "reports" / "RECOVERY_REPORT.md"
            report.write_text(report.read_text(encoding="utf-8") + "tamper\n", encoding="utf-8")
            self.assertFalse(verify_bundle(output)["ok"])
            report.write_text(report.read_text(encoding="utf-8")[:-7], encoding="utf-8")
            extra = output / "extra.txt"
            extra.write_text("extra\n", encoding="utf-8")
            os.chmod(extra, 0o600)
            result = verify_bundle(output)
            self.assertFalse(result["ok"])
            self.assertIn("unexpected_bundle_file", result["errors"])


if __name__ == "__main__":
    unittest.main()
