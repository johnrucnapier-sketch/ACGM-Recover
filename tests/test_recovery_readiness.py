from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from acgm_recover.analysis import analyze_project
from acgm_recover.bundle import build_bundle
from acgm_recover.util import sha256_file

from helpers import MAIN_SESSION, create_git_project, create_sources


class RecoveryReadinessTests(unittest.TestCase):
    def _fixture(self, root: Path):
        project, _ = create_git_project(root)
        claude_root, metadata_root, auxiliary_root, main_path = create_sources(root, project)
        return project, claude_root, metadata_root, auxiliary_root, main_path

    def test_reviewed_share_approved_annotations_can_reach_handoff_ready(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            project, claude_root, metadata_root, auxiliary_root, main_path = self._fixture(root)
            digest = sha256_file(main_path)
            annotations = root / "annotations.json"
            annotations.write_text(
                json.dumps(
                    {
                        "schema_version": "1.0",
                        "transcripts_by_sha256": {
                            digest: {
                                "content_project": "this-project",
                                "content_project_ref": "project-001",
                                "mapping_status": "confirmed",
                                "confidence": "verified",
                                "evidence_codes": ["human-confirmation"],
                                "human_reviewed": True,
                                "share_approved": True,
                            }
                        },
                        "sessions_by_id": {},
                        "known_gaps": [],
                        "decisions": [
                            {
                                "decision_id": "decision-001",
                                "summary": "Continue /Users/alice/SecretProject after review.",
                                "status": "implemented",
                                "confidence": "verified",
                                "evidence_transcript_sha256": [digest],
                                "current_artifact_corroborated": True,
                                "human_reviewed": True,
                                "share_approved": True,
                            }
                        ],
                        "continuation": {
                            "objective": "Review current state before any implementation.",
                            "next_steps": ["Ask for fresh authority."],
                            "blocked_by": [],
                            "human_reviewed": True,
                            "share_approved": True,
                        },
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
            self.assertEqual(analysis["recovery_readiness"], "HANDOFF_READY")
            self.assertIn("[LOCAL_PATH_REDACTED]", analysis["decisions"][0]["summary"])
            bundle = build_bundle(analysis, root / "bundle")
            share = "".join(
                path.read_text(encoding="utf-8") for path in (bundle / "share").rglob("*") if path.is_file()
            )
            self.assertNotIn("/Users/alice", share)

    def test_string_false_cannot_satisfy_corroboration_or_readiness(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            project, claude_root, metadata_root, auxiliary_root, main_path = self._fixture(root)
            digest = sha256_file(main_path)
            annotations = root / "annotations.json"
            annotations.write_text(
                json.dumps(
                    {
                        "schema_version": "1.0",
                        "transcripts_by_sha256": {
                            digest: {
                                "content_project": "this-project",
                                "mapping_status": "confirmed",
                                "confidence": "verified",
                                "evidence_codes": [],
                                "human_reviewed": True,
                                "share_approved": True,
                            }
                        },
                        "sessions_by_id": {},
                        "known_gaps": [],
                        "decisions": [
                            {
                                "decision_id": "decision-001",
                                "summary": "A draft decision.",
                                "status": "implemented",
                                "confidence": "verified",
                                "evidence_transcript_sha256": [],
                                "current_artifact_corroborated": "false",
                                "human_reviewed": True,
                                "share_approved": True,
                            }
                        ],
                        "continuation": {
                            "objective": "Draft objective.",
                            "next_steps": [],
                            "blocked_by": [],
                            "human_reviewed": True,
                            "share_approved": True,
                        },
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
            self.assertEqual(analysis["recovery_readiness"], "REVIEW_REQUIRED")
            self.assertEqual(analysis["decisions"], [])

    def test_session_annotation_applies_to_main_not_subagents(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            project, claude_root, metadata_root, auxiliary_root, _ = self._fixture(root)
            annotations = root / "annotations.json"
            annotations.write_text(
                json.dumps(
                    {
                        "schema_version": "1.0",
                        "transcripts_by_sha256": {},
                        "sessions_by_id": {
                            MAIN_SESSION: {
                                "content_project": "this-project",
                                "mapping_status": "confirmed",
                                "confidence": "verified",
                                "evidence_codes": ["human-confirmation"],
                                "human_reviewed": True,
                                "share_approved": True,
                            }
                        },
                        "known_gaps": [],
                        "decisions": [],
                        "continuation": {},
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
            children = [row for row in analysis["transcripts"] if row["category"] == "subagent_transcript"]
            self.assertEqual(main["content_project"], "this-project")
            self.assertTrue(all(row["content_project"] == "unknown" for row in children))

    def test_metadata_can_link_main_that_lacks_internal_cwd(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            project, claude_root, metadata_root, auxiliary_root, main_path = self._fixture(root)
            rows = [json.loads(line) for line in main_path.read_text(encoding="utf-8").splitlines()]
            for row in rows:
                row.pop("cwd", None)
            main_path.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")
            analysis = analyze_project(
                project,
                claude_projects_roots=[claude_root],
                metadata_roots=[metadata_root],
                auxiliary_roots=[auxiliary_root],
            )
            main = next(row for row in analysis["transcripts"] if row["category"] == "main_transcript")
            self.assertEqual(main["structural_match_basis"], "session_metadata_link")

    def test_external_only_wrong_cwd_main_cannot_make_project_handoff_ready(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            project, claude_root, metadata_root, auxiliary_root, main_path = self._fixture(root)
            digest = sha256_file(main_path)
            annotations = root / "annotations.json"
            annotations.write_text(
                json.dumps(
                    {
                        "schema_version": "1.0",
                        "transcripts_by_sha256": {
                            digest: {
                                "content_project": "external-project",
                                "content_project_ref": "external-project-001",
                                "mapping_status": "misopened",
                                "confidence": "verified",
                                "evidence_codes": ["human-confirmation"],
                                "human_reviewed": True,
                                "share_approved": True,
                            }
                        },
                        "sessions_by_id": {},
                        "known_gaps": [],
                        "decisions": [
                            {
                                "decision_id": "decision-001",
                                "summary": "A current artifact observation.",
                                "status": "implemented",
                                "confidence": "verified",
                                "evidence_transcript_sha256": [],
                                "current_artifact_corroborated": True,
                                "human_reviewed": True,
                                "share_approved": True,
                            }
                        ],
                        "continuation": {
                            "objective": "Review the actual project main line.",
                            "next_steps": [],
                            "blocked_by": [],
                            "human_reviewed": True,
                            "share_approved": True,
                        },
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
            self.assertEqual(analysis["recovery_readiness"], "REVIEW_REQUIRED")


if __name__ == "__main__":
    unittest.main()
