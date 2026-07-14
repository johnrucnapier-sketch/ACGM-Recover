from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from acgm_recover.analysis import _propagate_lineage_structural, discover_candidates
from acgm_recover.analysis import analyze_project
from acgm_recover.util import RecoverError


def record(category: str, session: str, tool_ids: list[str], parent_ids: list[str], roots: list[str]):
    return {
        "category": category,
        "duplicate_index": None,
        "structural_worktrees": list(roots),
        "structural_match_basis": "transcript_internal_cwd" if roots else None,
        "observed": {
            "session_ids": [session],
            "tool_use_ids": tool_ids,
            "parent_tool_use_ids": parent_ids,
        },
    }


class AnalysisInternalTests(unittest.TestCase):
    def test_lineage_tool_id_collision_does_not_cross_sessions(self) -> None:
        rows = [
            record("main_transcript", "session-a", ["same-tool-id"], [], ["W-001"]),
            record("main_transcript", "session-b", ["same-tool-id"], [], ["W-002"]),
            record("subagent_transcript", "session-a", [], ["same-tool-id"], []),
        ]
        parents = _propagate_lineage_structural(rows)
        self.assertEqual(parents[2], [0])
        self.assertEqual(rows[2]["structural_worktrees"], ["W-001"])

    def test_duplicate_transcript_is_not_a_second_lineage_owner(self) -> None:
        rows = [
            record("main_transcript", "session-a", ["tool"], [], ["W-001"]),
            record("main_transcript", "session-a", ["tool"], [], ["W-001"]),
            record("subagent_transcript", "session-a", [], ["tool"], []),
        ]
        rows[1]["duplicate_index"] = 0
        parents = _propagate_lineage_structural(rows)
        self.assertEqual(parents[2], [0])

    def test_discover_includes_local_agent_jsonl_source(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            project = root / "surviving-project"
            project.mkdir()
            local_agent = root / "local-agent-mode-sessions"
            local_agent.mkdir()
            (local_agent / "output.jsonl").write_text(
                json.dumps({"type": "user", "cwd": str(project), "message": {"role": "user"}}) + "\n",
                encoding="utf-8",
            )
            result = discover_candidates(
                claude_projects_roots=[],
                metadata_roots=[local_agent],
                auxiliary_roots=[],
                registry_path=None,
            )
            self.assertEqual(len(result["families"]), 1)
            self.assertEqual(result["families"][0]["evidence_counts"], {"local_agent_output": 1})

    def test_discover_reports_recommended_git_root_for_nested_cwd(self) -> None:
        from helpers import create_git_project, write_jsonl

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            project, _ = create_git_project(root)
            nested = project / "nested" / "child"
            nested.mkdir(parents=True)
            transcript_root = root / "claude-projects"
            session = "55555555-5555-4555-8555-555555555555"
            write_jsonl(
                transcript_root / "bucket" / f"{session}.jsonl",
                [{"type": "user", "sessionId": session, "isSidechain": False, "cwd": str(nested)}],
            )
            result = discover_candidates(
                claude_projects_roots=[transcript_root],
                metadata_roots=[],
                auxiliary_roots=[],
                registry_path=None,
            )
            self.assertEqual(result["families"][0]["recommended_project_roots"], [str(project.resolve())])
            with self.assertRaisesRegex(RecoverError, "project_must_be_git_root"):
                analyze_project(
                    nested,
                    claude_projects_roots=[transcript_root],
                    metadata_roots=[],
                    auxiliary_roots=[],
                )


if __name__ == "__main__":
    unittest.main()
