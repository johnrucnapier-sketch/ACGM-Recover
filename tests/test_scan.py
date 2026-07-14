from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from acgm_recover.scan import scan_jsonl_roots, scan_metadata_roots

from helpers import MAIN_SESSION, create_git_project, create_sources, write_jsonl


class ScanTests(unittest.TestCase):
    def test_main_subagent_secondary_and_quarantine_classification(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            project, _ = create_git_project(root)
            claude_root, _, _, _ = create_sources(root, project)
            rows = scan_jsonl_roots([(claude_root, "claude_projects")])
            categories = [row["category"] for row in rows]
            self.assertEqual(categories.count("main_transcript"), 1)
            self.assertEqual(categories.count("subagent_transcript"), 2)
            self.assertEqual(categories.count("unknown_jsonl"), 1)
            main = next(row for row in rows if row["category"] == "main_transcript")
            self.assertEqual(main["observed"]["session_ids"], [MAIN_SESSION])
            self.assertTrue(main["observed"]["sensitive_fields_present"])
            self.assertEqual(main["observed"]["counts"]["tool_use"], 1)

    def test_sidecar_extracts_structure_not_description(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            project, _ = create_git_project(root)
            claude_root, _, _, _ = create_sources(root, project)
            rows = scan_jsonl_roots([(claude_root, "claude_projects")])
            alpha = next(
                row
                for row in rows
                if row["category"] == "subagent_transcript" and row["sidecar"]["spawn_depth"] == 1
            )
            self.assertEqual(alpha["sidecar"]["tool_use_id"], "tool-main")
            self.assertTrue(alpha["sidecar"]["description_present"])
            self.assertNotIn("description", alpha["sidecar"])

    def test_metadata_only_extracts_allowlisted_fields(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            project, _ = create_git_project(root)
            _, metadata_root, _, _ = create_sources(root, project)
            rows = scan_metadata_roots([metadata_root])
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["storage_route_observed"], "claude-3p-storage")
            self.assertEqual(rows[0]["records"][0]["session_id"], MAIN_SESSION)

    def test_metadata_rejects_unbounded_or_wrongly_typed_allowlisted_values(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            metadata_root = root / "metadata"
            metadata_root.mkdir()
            oversized_marker = "DO-NOT-RETAIN-" + "x" * 5000
            (metadata_root / "local_hostile.json").write_text(
                json.dumps(
                    {
                        "sessionId": "s" * 513,
                        "title": {"value": oversized_marker},
                        "cwd": "/bounded/project",
                        "originCwd": "/bad\u0000path",
                        "model": [oversized_marker],
                        "createdAt": "t" * 129,
                        "lastActivityAt": 123,
                        "completedTurns": True,
                        "isArchived": 1,
                        "transcriptUnavailable": 0,
                    }
                ),
                encoding="utf-8",
            )

            row = scan_metadata_roots([metadata_root])[0]

            self.assertEqual(row["parse_status"], "structural_partial")
            self.assertEqual(row["records"], [{
                "session_id": None,
                "title": None,
                "cwd": "/bounded/project",
                "origin_cwd": None,
                "display_model": None,
                "created_at": None,
                "last_activity_at": None,
                "completed_turns": None,
                "archived": None,
                "transcript_unavailable": None,
            }])
            self.assertNotIn(oversized_marker, json.dumps(row["records"], ensure_ascii=False))

    def test_metadata_with_only_invalid_values_is_invalid_and_retains_nothing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            metadata_root = root / "metadata"
            metadata_root.mkdir()
            (metadata_root / "local_invalid.json").write_text(
                json.dumps({"sessionId": ["not", "a", "string"], "cwd": "bad\u0007path"}),
                encoding="utf-8",
            )

            row = scan_metadata_roots([metadata_root])[0]

            self.assertEqual(row["parse_status"], "invalid_schema")
            self.assertEqual(row["records"], [])

    def test_metadata_flags_invalid_duplicate_alias_without_retaining_it(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            metadata_root = root / "metadata"
            metadata_root.mkdir()
            oversized_alias = "x" * 513
            (metadata_root / "local_duplicate_alias.json").write_text(
                json.dumps(
                    {
                        "sessionId": MAIN_SESSION,
                        "session_id": oversized_alias,
                        "cwd": "/bounded/project",
                    }
                ),
                encoding="utf-8",
            )

            row = scan_metadata_roots([metadata_root])[0]

            self.assertEqual(row["parse_status"], "structural_partial")
            self.assertEqual(row["records"][0]["session_id"], MAIN_SESSION)
            self.assertNotIn(oversized_alias, json.dumps(row["records"], ensure_ascii=False))

    def test_metadata_accepts_exact_bounds_and_strict_bool_int_values(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            metadata_root = root / "metadata"
            metadata_root.mkdir()
            payload = {
                "sessionId": "s" * 512,
                "title": "t" * 512,
                "cwd": "/" + "c" * 4095,
                "originCwd": "/" + "o" * 4095,
                "model": "m" * 512,
                "createdAt": "1" * 128,
                "lastActivityAt": "2" * 128,
                "completedTurns": (1 << 63) - 1,
                "isArchived": False,
                "transcriptUnavailable": True,
            }
            (metadata_root / "local_bounds.json").write_text(
                json.dumps(payload),
                encoding="utf-8",
            )

            row = scan_metadata_roots([metadata_root])[0]

            self.assertEqual(row["parse_status"], "ok")
            record = row["records"][0]
            self.assertEqual(record["session_id"], payload["sessionId"])
            self.assertEqual(record["cwd"], payload["cwd"])
            self.assertEqual(record["origin_cwd"], payload["originCwd"])
            self.assertEqual(record["completed_turns"], payload["completedTurns"])
            self.assertIs(record["archived"], False)
            self.assertIs(record["transcript_unavailable"], True)

    def test_metadata_rejects_out_of_range_or_unparseably_large_integers(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            metadata_root = root / "metadata"
            metadata_root.mkdir()
            (metadata_root / "local_out_of_range.json").write_text(
                json.dumps(
                    {
                        "sessionId": MAIN_SESSION,
                        "cwd": "/bounded/project",
                        "completedTurns": 1 << 63,
                    }
                ),
                encoding="utf-8",
            )
            (metadata_root / "local_huge_integer.json").write_text(
                '{"sessionId":"safe","cwd":"/bounded","completedTurns":' + "1" * 5000 + "}",
                encoding="utf-8",
            )

            rows = scan_metadata_roots([metadata_root])
            out_of_range = next(
                row for row in rows if row["source_path"].endswith("local_out_of_range.json")
            )
            huge_integer = next(
                row for row in rows if row["source_path"].endswith("local_huge_integer.json")
            )

            self.assertEqual(out_of_range["parse_status"], "structural_partial")
            self.assertIsNone(out_of_range["records"][0]["completed_turns"])
            self.assertEqual(huge_integer["parse_status"], "invalid_json")
            self.assertEqual(huge_integer["records"], [])

    def test_malformed_and_truncated_jsonl_are_partial(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            bucket = root / "bucket"
            bucket.mkdir()
            malformed = bucket / f"{MAIN_SESSION}.jsonl"
            malformed.write_text(
                '{"type":"user","sessionId":"' + MAIN_SESSION + '","isSidechain":false}\nnot-json\n',
                encoding="utf-8",
            )
            row = scan_jsonl_roots([(root, "claude_projects")])[0]
            self.assertEqual(row["parse"]["status"], "malformed_partial")

            truncated = bucket / "33333333-3333-4333-8333-333333333333.jsonl"
            truncated.write_text(
                '{"type":"user","sessionId":"33333333-3333-4333-8333-333333333333","isSidechain":false}',
                encoding="utf-8",
            )
            rows = scan_jsonl_roots([(root, "claude_projects")])
            truncated_row = next(value for value in rows if value["source_path"] == str(truncated))
            self.assertEqual(truncated_row["parse"]["status"], "truncated_partial")

    def test_invalid_structural_types_and_main_shape_are_quarantined(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            bucket = root / "bucket"
            bad = bucket / f"{MAIN_SESSION}.jsonl"
            write_jsonl(
                bad,
                [
                    {
                        "type": [],
                        "sessionId": MAIN_SESSION,
                        "isSidechain": [],
                        "message": {"role": []},
                    }
                ],
            )
            rows = scan_jsonl_roots([(root, "claude_projects")])
            self.assertEqual(rows[0]["category"], "unknown_jsonl")
            self.assertEqual(rows[0]["parse"]["status"], "structural_partial")

            deep = bucket / "backup" / "44444444-4444-4444-8444-444444444444.jsonl"
            write_jsonl(
                deep,
                [{"type": "user", "sessionId": deep.stem, "isSidechain": False}],
            )
            rows = scan_jsonl_roots([(root, "claude_projects")])
            self.assertEqual(next(row for row in rows if row["source_path"] == str(deep))["category"], "unknown_jsonl")

    def test_single_long_line_obeys_total_byte_cap(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            bucket = root / "bucket"
            bucket.mkdir()
            path = bucket / f"{MAIN_SESSION}.jsonl"
            path.write_bytes(b"{" + b"x" * 1024)
            with patch("acgm_recover.scan.MAX_JSONL_BYTES", 64), patch(
                "acgm_recover.scan.MAX_JSONL_LINE_BYTES", 16
            ):
                row = scan_jsonl_roots([(root, "claude_projects")])[0]
            self.assertEqual(row["parse"]["status"], "bounded_partial")
            self.assertIsNone(row["sha256"])

    def test_tool_ids_beyond_generic_value_cap_remain_available(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            bucket = root / "bucket"
            path = bucket / f"{MAIN_SESSION}.jsonl"
            blocks = [{"type": "tool_use", "id": f"tool-{index}"} for index in range(140)]
            write_jsonl(
                path,
                [
                    {
                        "type": "assistant",
                        "sessionId": MAIN_SESSION,
                        "isSidechain": False,
                        "message": {"role": "assistant", "content": blocks},
                    }
                ],
            )
            row = scan_jsonl_roots([(root, "claude_projects")])[0]
            self.assertIn("tool-139", row["observed"]["tool_use_ids"])
            self.assertNotIn("tool_use_ids", row["observed"]["field_caps"])


if __name__ == "__main__":
    unittest.main()
