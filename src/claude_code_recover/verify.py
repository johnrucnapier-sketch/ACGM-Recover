"""Bundle integrity, schema, reference-closure, and optional source-drift checks."""

from __future__ import annotations

import json
import os
import re
import stat
import unicodedata
from collections import Counter
from pathlib import Path
from typing import Any

from .constants import MAX_JSONL_SOURCE_FILES, MAX_JSONL_TOTAL_BUDGET_BYTES, ROUTES, SCHEMA_VERSION
from .gitfacts import inspect_git
from .util import RecoverError, has_extra_acl, mode_string, sha256_file

CHECKSUM_CLAIM = "integrity_against_this_manifest_not_source_authenticity"
READINESS_VALUES = {"STRUCTURAL_ONLY", "REVIEW_REQUIRED", "HANDOFF_READY"}
CONFIDENCE_VALUES = {"verified", "corroborated", "probable", "candidate", "unresolved"}
CONTENT_PROJECT_VALUES = {"this-project", "external-project", "mixed", "unknown"}
TRANSCRIPT_CATEGORY_VALUES = {
    "main_transcript",
    "subagent_transcript",
    "local_agent_output",
    "homunculus_observations",
    "tool_result_or_task",
    "metrics_or_cache",
    "unknown_jsonl",
}
CORE_FILES = {
    "BUNDLE.json",
    "PRIVACY.md",
    "SCHEMA_VERSION",
    "evidence/manifest.jsonl",
    "evidence/claims.jsonl",
    "evidence/conflicts.jsonl",
    "evidence/gaps.jsonl",
    "evidence/source_scan.json",
    "project/current_state.json",
    "project/git_state.json",
    "project/worktrees.json",
    "project/file_inventory.jsonl",
    "sessions/metadata_index.jsonl",
    "sessions/transcript_index.jsonl",
    "sessions/lineage_candidates.jsonl",
    "sessions/corrections.jsonl",
    "sessions/decisions.jsonl",
    "sessions/continuation_state.json",
    "reports/RECOVERY_REPORT.md",
    "reports/CONTINUATION_BRIEF.md",
    "review/ANNOTATIONS.example.json",
    "review/REVIEW_QUEUE.json",
    "share/common/CONTINUATION_BRIEF.md",
    "share/common/EVIDENCE_INDEX.json",
    "share/common/EVIDENCE_MANIFEST.jsonl",
    "share/common/CLAIMS.jsonl",
    "share/common/CONFLICTS.jsonl",
    "share/common/GAPS.jsonl",
    "share/common/TRANSCRIPT_INDEX.jsonl",
    "share/common/DECISIONS.jsonl",
    "share/common/CONTINUATION_STATE.json",
    "share/common/CURRENT_STATE.json",
    "share/common/SOURCE_SCAN.json",
    "private/SOURCE_MAP.json",
    "private/FILE_PATHS.jsonl",
    "private/METADATA_SOURCE_MAP.jsonl",
    "private/PRIVATE_DO_NOT_SHARE.md",
}
ROUTE_FILES = {"ROUTE.json", "START_PROMPT.md", "CONTINUATION_CHECKLIST.md"}


def _load_json(path: Path) -> Any:
    try:
        if path.is_symlink() or path.stat().st_size > 64 * 1024 * 1024:
            return None
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, RecursionError, ValueError):
        return None


def _load_jsonl(path: Path, *, max_lines: int = 500_000) -> list[dict[str, Any]] | None:
    rows: list[dict[str, Any]] = []
    try:
        if path.is_symlink() or path.stat().st_size > 512 * 1024 * 1024:
            return None
        with path.open("r", encoding="utf-8") as handle:
            for line_number, line in enumerate(handle, start=1):
                if line_number > max_lines or len(line) > 4 * 1024 * 1024:
                    return None
                value = json.loads(line)
                if not isinstance(value, dict):
                    return None
                rows.append(value)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, RecursionError, ValueError):
        return None
    return rows


def _bundle_files(root: Path) -> tuple[dict[str, Path], set[str], list[str]]:
    files: dict[str, Path] = {}
    directories: set[str] = set()
    errors: list[str] = []
    normalized: dict[str, str] = {}
    try:
        if stat.S_IMODE(root.stat(follow_symlinks=False).st_mode) != 0o700:
            errors.append("root_mode_invalid")
        if has_extra_acl(root):
            errors.append("extended_acl_present")
    except RecoverError:
        errors.append("acl_check_failed")
    except OSError:
        errors.append("stat_failed")
    for current, dirs, names in os.walk(root, followlinks=False):
        current_path = Path(current)
        safe_dirs: list[str] = []
        for name in dirs:
            path = current_path / name
            relative_dir = path.relative_to(root).as_posix()
            if path.is_symlink():
                errors.append("symlink_entry")
                continue
            try:
                if stat.S_IMODE(path.stat(follow_symlinks=False).st_mode) != 0o700:
                    errors.append("directory_mode_invalid")
                if has_extra_acl(path):
                    errors.append("extended_acl_present")
            except RecoverError:
                errors.append("acl_check_failed")
                continue
            except OSError:
                errors.append("stat_failed")
                continue
            safe_dirs.append(name)
            collision_key = unicodedata.normalize("NFC", relative_dir).casefold()
            if collision_key in normalized and normalized[collision_key] != relative_dir:
                errors.append("path_collision")
            normalized[collision_key] = relative_dir
            directories.add(relative_dir)
        dirs[:] = safe_dirs
        for name in names:
            path = current_path / name
            relative = path.relative_to(root).as_posix()
            if path.is_symlink() or not path.is_file():
                errors.append("non_regular_entry")
                continue
            try:
                source_stat = path.stat(follow_symlinks=False)
                if source_stat.st_nlink != 1:
                    errors.append("hardlink_entry")
                if stat.S_IMODE(source_stat.st_mode) != 0o600:
                    errors.append("file_mode_invalid")
                if has_extra_acl(path):
                    errors.append("extended_acl_present")
            except RecoverError:
                errors.append("acl_check_failed")
                continue
            except OSError:
                errors.append("stat_failed")
                continue
            collision_key = unicodedata.normalize("NFC", relative).casefold()
            if collision_key in normalized and normalized[collision_key] != relative:
                errors.append("path_collision")
            normalized[collision_key] = relative
            files[relative] = path
    return files, directories, errors


def _allowed_files(routes: list[str]) -> set[str]:
    result = set(CORE_FILES)
    for route in routes:
        result.update(f"share/{route}/{name}" for name in ROUTE_FILES)
    return result


def _id_set(rows: list[dict[str, Any]], field: str) -> set[str] | None:
    values: set[str] = set()
    for row in rows:
        value = row.get(field)
        if not isinstance(value, str) or value in values:
            return None
        values.add(value)
    return values


def _derive_readiness(
    project: Any,
    git: Any,
    transcripts: list[dict[str, Any]],
    decisions: list[dict[str, Any]],
    continuation: Any,
    source_scan_wrapper: Any,
    gaps: list[dict[str, Any]],
) -> str | None:
    if not isinstance(project, dict) or not isinstance(git, dict) or not isinstance(continuation, dict):
        return None
    if not isinstance(source_scan_wrapper, dict):
        return None
    source_scan = source_scan_wrapper.get("source_scan")
    inventory_stats = source_scan_wrapper.get("inventory_stats")
    if not isinstance(source_scan, dict) or not isinstance(inventory_stats, dict):
        return None
    main_rows = [row for row in transcripts if row.get("category") == "main_transcript"]
    if not main_rows:
        return "STRUCTURAL_ONLY"
    decision_ok = bool(decisions) and all(
        row.get("human_reviewed") is True
        and row.get("share_approved") is True
        and isinstance(row.get("status"), str)
        and row.get("status") in {"implemented", "active", "superseded"}
        and isinstance(row.get("confidence"), str)
        and row.get("confidence") in {"verified", "corroborated", "probable"}
        and (
            (isinstance(row.get("evidence_transcript_ids"), list) and bool(row.get("evidence_transcript_ids")))
            or row.get("current_artifact_corroborated") is True
        )
        for row in decisions
    )
    continuation_ok = bool(continuation) and continuation.get("human_reviewed") is True and continuation.get(
        "share_approved"
    ) is True
    main_reviewed = all(row.get("content_reviewed") is True for row in main_rows)
    relevant_main_reviewed = any(
        row.get("content_reviewed") is True
        and isinstance(row.get("content_project"), str)
        and row.get("content_project") in {"this-project", "mixed"}
        for row in main_rows
    )
    transcript_quality = all(
        isinstance(row.get("parse"), dict)
        and row["parse"].get("status") == "ok"
        and row.get("source_stable_during_read") is True
        and row.get("category") != "unknown_jsonl"
        and (
            row.get("lineage_basis") is None
            or (
                isinstance(row.get("lineage_basis"), str)
                and row.get("lineage_basis")
                not in {"unmatched_tool_use_id", "ambiguous_tool_use_id", "ambiguous_parent_session_id"}
            )
        )
        and not (
            row.get("sidecar_present")
            and (row.get("sidecar_parse_status") != "ok" or row.get("sidecar_source_stable") is not True)
        )
        for row in transcripts
    )
    scan_quality = all(
        source_scan.get(key) == 0
        for key in (
            "metadata_parse_failures",
            "metadata_files_skipped_budget",
            "metadata_records_skipped_budget",
            "jsonl_parse_failures",
            "jsonl_files_skipped_budget",
            "unstable_sources",
            "quarantined_worktree_candidates",
        )
    )
    git_quality = bool(
        isinstance(git.get("observation_status"), str)
        and git.get("observation_status") in {"complete", "not_repository"}
        and git.get("object_store_observation_status") in {"complete", "not_applicable"}
        and isinstance(git.get("object_store_count"), int)
        and git.get("object_store_count") >= 0
        and git.get("source_stable_during_read") is True
        and isinstance(git.get("status"), dict)
        and git["status"].get("readable") is True
    )
    inventory_quality = project.get("file_inventory_capped") is False and inventory_stats.get("read_errors") == 0
    critical_gap_codes = {
        "continuity_handoff_not_ready",
        "decision_annotations_rejected",
        "subagent_parent_tool_use_unmatched",
        "subagent_sidecar_unusable",
        "metadata_sources_not_fully_parsed",
        "metadata_global_scan_budget_exhausted",
        "metadata_record_budget_exhausted",
        "jsonl_sources_not_fully_parsed",
        "jsonl_global_scan_budget_exhausted",
        "source_scan_observed_changes",
        "file_inventory_capped",
        "file_inventory_read_errors",
        "worktree_candidates_quarantined",
    }
    gaps_ok = not any(row.get("code") in critical_gap_codes for row in gaps)
    if all(
        (
            decision_ok,
            continuation_ok,
            main_reviewed,
            relevant_main_reviewed,
            transcript_quality,
            scan_quality,
            git_quality,
            inventory_quality,
            gaps_ok,
        )
    ):
        return "HANDOFF_READY"
    return "REVIEW_REQUIRED"


def _validate_core_schema(
    project: Any,
    git: Any,
    transcripts: list[dict[str, Any]],
    decisions: list[dict[str, Any]],
    continuation: Any,
    claims: list[dict[str, Any]],
    errors: list[str],
) -> None:
    if (
        not isinstance(project, dict)
        or project.get("recovery_readiness") not in READINESS_VALUES
        or not isinstance(project.get("file_inventory_capped"), bool)
        or not isinstance(project.get("selected_worktree_id"), str)
    ):
        errors.append("project_schema_invalid")
    if (
        not isinstance(git, dict)
        or not isinstance(git.get("observation_status"), str)
        or git.get("observation_status") not in {"complete", "partial", "not_repository", "read_failed"}
        or git.get("object_store_observation_status") not in {"complete", "not_applicable"}
        or not isinstance(git.get("object_store_count"), int)
        or git.get("object_store_count") < 0
        or not isinstance(git.get("source_stable_during_read"), bool)
        or not isinstance(git.get("status"), dict)
        or not isinstance(git.get("status", {}).get("readable"), bool)
    ):
        errors.append("git_schema_invalid")
    transcript_ids = [row.get("transcript_id") for row in transcripts]
    transcript_id_set = {value for value in transcript_ids if isinstance(value, str)}
    if len(transcript_id_set) != len(transcripts):
        errors.append("transcript_identifier_invalid")
    for row in transcripts:
        parse = row.get("parse")
        parents = row.get("parent_transcripts")
        duplicate = row.get("duplicate_of")
        if (
            not isinstance(row.get("transcript_id"), str)
            or not isinstance(row.get("category"), str)
            or row.get("category") not in TRANSCRIPT_CATEGORY_VALUES
            or not isinstance(row.get("content_project"), str)
            or row.get("content_project") not in CONTENT_PROJECT_VALUES
            or not isinstance(row.get("content_reviewed"), bool)
            or not isinstance(row.get("source_stable_during_read"), bool)
            or not isinstance(parse, dict)
            or not isinstance(parse.get("status"), str)
            or not isinstance(parents, list)
            or any(not isinstance(value, str) or value not in transcript_id_set for value in parents)
            or (duplicate is not None and (not isinstance(duplicate, str) or duplicate not in transcript_id_set))
        ):
            errors.append("transcript_schema_invalid")
            break
    for row in decisions:
        evidence_refs = row.get("evidence_transcript_ids")
        if (
            not isinstance(row.get("decision_id"), str)
            or not isinstance(row.get("summary"), str)
            or not isinstance(row.get("status"), str)
            or row.get("status") not in {"implemented", "active", "superseded", "proposed", "unverified"}
            or not isinstance(row.get("confidence"), str)
            or row.get("confidence") not in CONFIDENCE_VALUES
            or not isinstance(row.get("current_artifact_corroborated"), bool)
            or row.get("human_reviewed") is not True
            or row.get("share_approved") is not True
            or not isinstance(evidence_refs, list)
            or any(not isinstance(value, str) or value not in transcript_id_set for value in evidence_refs)
            or len(evidence_refs) != len(set(evidence_refs))
            or row.get("interpretation") != "historical_data_not_current_execution_authority"
        ):
            errors.append("decision_schema_invalid")
            break
    if continuation:
        if (
            not isinstance(continuation, dict)
            or not isinstance(continuation.get("objective"), str)
            or not isinstance(continuation.get("next_steps"), list)
            or not isinstance(continuation.get("blocked_by"), list)
            or continuation.get("human_reviewed") is not True
            or continuation.get("share_approved") is not True
            or continuation.get("interpretation") != "handoff_data_requires_fresh_runtime_authority"
        ):
            errors.append("continuation_schema_invalid")
    for row in claims:
        if (
            not isinstance(row.get("confidence"), str)
            or row.get("confidence") not in CONFIDENCE_VALUES
            or not isinstance(row.get("time_scope"), str)
            or row.get("time_scope") not in {"current", "historical"}
        ):
            errors.append("claim_schema_invalid")
            break


def _validate_schema_and_closure(root: Path, bundle_meta: dict[str, Any], errors: list[str]) -> None:
    if (root / "SCHEMA_VERSION").read_text(encoding="utf-8", errors="replace") != SCHEMA_VERSION + "\n":
        errors.append("schema_version_file_invalid")
    routes = bundle_meta.get("routes_generated")
    if (
        not isinstance(routes, list)
        or not routes
        or not all(isinstance(route, str) for route in routes)
        or len(routes) != len(set(routes))
        or any(route not in ROUTES for route in routes)
    ):
        errors.append("routes_invalid")
        return
    readiness = bundle_meta.get("recovery_readiness")
    if not isinstance(readiness, str) or readiness not in READINESS_VALUES:
        errors.append("readiness_invalid")

    manifest = _load_jsonl(root / "evidence/manifest.jsonl")
    claims = _load_jsonl(root / "evidence/claims.jsonl")
    conflicts = _load_jsonl(root / "evidence/conflicts.jsonl")
    gaps = _load_jsonl(root / "evidence/gaps.jsonl")
    transcripts = _load_jsonl(root / "sessions/transcript_index.jsonl")
    metadata = _load_jsonl(root / "sessions/metadata_index.jsonl")
    decisions = _load_jsonl(root / "sessions/decisions.jsonl")
    if any(value is None for value in (manifest, claims, conflicts, gaps, transcripts, metadata, decisions)):
        errors.append("jsonl_schema_invalid")
        return
    assert manifest is not None and claims is not None and conflicts is not None and gaps is not None
    assert transcripts is not None and metadata is not None and decisions is not None
    evidence_ids = _id_set(manifest, "evidence_id")
    claim_ids = _id_set(claims, "claim_id")
    conflict_ids = _id_set(conflicts, "conflict_id")
    gap_ids = _id_set(gaps, "gap_id")
    if any(value is None for value in (evidence_ids, claim_ids, conflict_ids, gap_ids)):
        errors.append("identifier_schema_invalid")
        return
    assert evidence_ids is not None and claim_ids is not None and conflict_ids is not None and gap_ids is not None
    for claim in claims:
        refs = claim.get("evidence_ids")
        if not isinstance(refs, list) or any(not isinstance(value, str) or value not in evidence_ids for value in refs):
            errors.append("claim_evidence_reference_invalid")
    if any(not isinstance(row.get("code"), str) for row in conflicts + gaps):
        errors.append("code_schema_invalid")
        return

    share_pairs = [
        (manifest, "share/common/EVIDENCE_MANIFEST.jsonl"),
        (claims, "share/common/CLAIMS.jsonl"),
        (conflicts, "share/common/CONFLICTS.jsonl"),
        (gaps, "share/common/GAPS.jsonl"),
        (transcripts, "share/common/TRANSCRIPT_INDEX.jsonl"),
        (decisions, "share/common/DECISIONS.jsonl"),
    ]
    for expected, relative in share_pairs:
        if _load_jsonl(root / relative) != expected:
            errors.append("share_evidence_closure_invalid")

    continuation_state = _load_json(root / "share/common/CONTINUATION_STATE.json")
    current_state = _load_json(root / "share/common/CURRENT_STATE.json")
    evidence_index = _load_json(root / "share/common/EVIDENCE_INDEX.json")
    root_continuation = _load_json(root / "sessions/continuation_state.json")
    root_project = _load_json(root / "project/current_state.json")
    root_git = _load_json(root / "project/git_state.json")
    root_worktrees = _load_json(root / "project/worktrees.json")
    source_scan = _load_json(root / "evidence/source_scan.json")
    share_source_scan = _load_json(root / "share/common/SOURCE_SCAN.json")
    expected_continuation_state = {
        "schema_version": SCHEMA_VERSION,
        "recovery_readiness": readiness,
        "continuation": root_continuation,
        "interpretation": "data_not_runtime_authority",
    }
    if continuation_state != expected_continuation_state:
        errors.append("share_continuation_state_invalid")
    expected_current = (
        {
            "project": root_project,
            "git": {
                "is_git_repository": root_git.get("is_git_repository"),
                "observation_status": root_git.get("observation_status"),
                "head": root_git.get("head"),
                "branch": root_git.get("branch"),
                "status": root_git.get("status"),
                "object_store_observation_status": root_git.get("object_store_observation_status"),
                "object_store_count": root_git.get("object_store_count"),
                "remote_count": len(root_git.get("remotes")) if isinstance(root_git.get("remotes"), list) else None,
                "source_stable_during_read": root_git.get("source_stable_during_read"),
            },
            "worktrees": root_worktrees,
        }
        if isinstance(root_project, dict)
        and isinstance(root_git, dict)
        and isinstance(root_git.get("remotes"), list)
        and isinstance(root_worktrees, list)
        else None
    )
    if not isinstance(current_state, dict) or current_state != expected_current:
        errors.append("share_current_state_invalid")
    if not isinstance(source_scan, dict) or share_source_scan != source_scan:
        errors.append("share_source_scan_invalid")
    _validate_core_schema(
        root_project,
        root_git,
        transcripts,
        decisions,
        root_continuation,
        claims,
        errors,
    )
    derived_readiness = _derive_readiness(
        root_project,
        root_git,
        transcripts,
        decisions,
        root_continuation,
        source_scan,
        gaps,
    )
    expected_summary = {
        "metadata_records": len(metadata),
        "transcript_records": len(transcripts),
        "transcript_categories": dict(
            sorted(Counter(row.get("category") for row in transcripts if isinstance(row.get("category"), str)).items())
        ),
        "content_reviewed": sum(1 for row in transcripts if row.get("content_reviewed") is True),
        "content_unreviewed": sum(1 for row in transcripts if row.get("content_project") == "unknown"),
        "human_reviewed_decisions": len(decisions),
        "continuation_reviewed": bool(root_continuation),
        "recovery_readiness": readiness,
        "conflicts": len(conflicts),
        "gaps": len(gaps),
    }
    if (
        derived_readiness is None
        or derived_readiness != readiness
        or not isinstance(root_project, dict)
        or root_project.get("recovery_readiness") != readiness
        or bundle_meta.get("summary") != expected_summary
    ):
        errors.append("readiness_derivation_invalid")

    if isinstance(root_project, dict) and isinstance(root_git, dict):
        canonical_analysis = {
            "summary": expected_summary,
            "recovery_readiness": readiness,
            "project": root_project,
            "git": root_git,
            "conflicts": conflicts,
            "gaps": gaps,
        }
        from .bundle import _continuation_brief, _privacy_document, _report_markdown

        try:
            expected_brief = _continuation_brief(canonical_analysis)
            expected_report = _report_markdown(canonical_analysis)
        except (KeyError, TypeError, ValueError):
            errors.append("canonical_document_schema_invalid")
            expected_brief = None
            expected_report = None
        try:
            report_brief = (root / "reports/CONTINUATION_BRIEF.md").read_text(encoding="utf-8")
            share_brief = (root / "share/common/CONTINUATION_BRIEF.md").read_text(encoding="utf-8")
            report = (root / "reports/RECOVERY_REPORT.md").read_text(encoding="utf-8")
            privacy = (root / "PRIVACY.md").read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            errors.append("canonical_document_unreadable")
        else:
            if expected_brief is None or report_brief != expected_brief or share_brief != expected_brief:
                errors.append("continuation_brief_invalid")
            if expected_report is None or report != expected_report:
                errors.append("recovery_report_invalid")
            if privacy != _privacy_document():
                errors.append("privacy_document_invalid")
    expected_index = {
        "schema_version": SCHEMA_VERSION,
        "claim_ids": [row["claim_id"] for row in claims],
        "conflict_codes": [row["code"] for row in conflicts],
        "gap_codes": [row["code"] for row in gaps],
        "transcript_text_included": False,
        "recovery_readiness": readiness,
    }
    if evidence_index != expected_index:
        errors.append("share_evidence_index_invalid")

    for route in routes:
        route_contract = _load_json(root / "share" / route / "ROUTE.json")
        if not isinstance(route_contract, dict):
            errors.append("route_schema_invalid")
            continue
        if (
            route_contract.get("schema_version") != SCHEMA_VERSION
            or route_contract.get("route") != route
            or route_contract.get("identity_assessment") != "not_performed"
            or route_contract.get("display_label_is_model_identity") is not False
            or route_contract.get("recovery_readiness") != readiness
            or route_contract.get("handoff_status")
            != ("ready" if readiness == "HANDOFF_READY" else "draft")
        ):
            errors.append("route_schema_invalid")
        references = {
            "current_supported_facts": claim_ids,
            "historical_claims": claim_ids,
            "conflicts": conflict_ids,
            "known_gaps": gap_ids,
            "evidence_refs": evidence_ids,
        }
        expected_references = {
            "current_supported_facts": [
                row["claim_id"]
                for row in claims
                if row.get("time_scope") == "current"
                and isinstance(row.get("confidence"), str)
                and row.get("confidence") in {"verified", "corroborated", "probable"}
            ],
            "historical_claims": [row["claim_id"] for row in claims if row.get("time_scope") == "historical"],
            "conflicts": [row["conflict_id"] for row in conflicts],
            "known_gaps": [row["gap_id"] for row in gaps],
            "evidence_refs": [row["evidence_id"] for row in manifest],
        }
        for field, allowed in references.items():
            values = route_contract.get(field)
            if (
                not isinstance(values, list)
                or any(not isinstance(value, str) or value not in allowed for value in values)
                or values != expected_references[field]
            ):
                errors.append("route_reference_invalid")
        from .bundle import _route_checklist, _start_prompt

        try:
            prompt = (root / "share" / route / "START_PROMPT.md").read_text(encoding="utf-8")
            checklist = (root / "share" / route / "CONTINUATION_CHECKLIST.md").read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            errors.append("route_template_invalid")
        else:
            if prompt != _start_prompt(route, readiness) or checklist != _route_checklist(route):
                errors.append("route_template_invalid")


def _check_sources(root: Path, warnings: list[str], errors: list[str]) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    source_map = _load_json(root / "private/SOURCE_MAP.json")
    saved_git = _load_json(root / "project/git_state.json")
    if not isinstance(source_map, dict) or not isinstance(saved_git, dict):
        errors.append("source_map_invalid")
        return results
    project_root = source_map.get("project_root")
    transcripts = source_map.get("transcripts")
    if not isinstance(project_root, str) or not Path(project_root).is_absolute() or not isinstance(transcripts, dict):
        errors.append("source_map_schema_invalid")
        return results
    try:
        project_path = Path(project_root)
        if project_path.is_symlink() or not project_path.is_dir():
            raise OSError
        facts = inspect_git(project_path)
        if facts.get("observation_status") not in {"complete", "not_repository"}:
            raise RecoverError("git_source_unverifiable")
        current_snapshot = (
            facts.get("is_git_repository"),
            facts.get("head"),
            facts.get("branch"),
            facts.get("status", {}).get("dirty") if isinstance(facts.get("status"), dict) else None,
        )
        saved_snapshot = (
            saved_git.get("is_git_repository"),
            saved_git.get("head"),
            saved_git.get("branch"),
            saved_git.get("status", {}).get("dirty") if isinstance(saved_git.get("status"), dict) else None,
        )
        status = "unchanged" if current_snapshot == saved_snapshot else "drifted"
        results.append({"source": "project_git", "status": status})
        if status == "drifted":
            warnings.append("source_git_drift_detected")
    except (OSError, ValueError, RuntimeError, RecoverError):
        results.append({"source": "project_git", "status": "unavailable"})
        warnings.append("source_project_unavailable")

    bytes_hashed = 0
    files_hashed = 0
    for transcript_id, record in sorted(transcripts.items(), key=lambda item: str(item[0])):
        if not isinstance(transcript_id, str) or not isinstance(record, dict):
            errors.append("source_map_schema_invalid")
            continue
        path_text = record.get("source_path")
        expected_hash = record.get("sha256")
        if (
            not isinstance(path_text, str)
            or not Path(path_text).is_absolute()
            or not isinstance(expected_hash, str)
            or re.fullmatch(r"[0-9a-f]{64}", expected_hash) is None
        ):
            results.append({"source": transcript_id, "status": "unverifiable"})
            continue
        try:
            path = Path(path_text)
            if path.is_symlink() or not path.is_file():
                raise OSError
            current_size = path.stat(follow_symlinks=False).st_size
            saved_stat = record.get("stat") if isinstance(record.get("stat"), dict) else {}
            saved_size = saved_stat.get("size")
            if not isinstance(saved_size, int) or current_size != saved_size:
                results.append({"source": transcript_id, "status": "drifted"})
                warnings.append("source_transcript_drift_detected")
                continue
            if (
                files_hashed >= MAX_JSONL_SOURCE_FILES
                or bytes_hashed + current_size > MAX_JSONL_TOTAL_BUDGET_BYTES
            ):
                results.append({"source": transcript_id, "status": "unverifiable_budget"})
                warnings.append("source_check_budget_exhausted")
                continue
            files_hashed += 1
            bytes_hashed += current_size
            status = "unchanged" if sha256_file(path) == expected_hash else "drifted"
            results.append({"source": transcript_id, "status": status})
            if status == "drifted":
                warnings.append("source_transcript_drift_detected")
            sidecar_path_text = record.get("sidecar_source_path")
            sidecar_hash = record.get("sidecar_sha256")
            sidecar_stat = record.get("sidecar_stat")
            if sidecar_path_text and sidecar_hash:
                if (
                    not isinstance(sidecar_path_text, str)
                    or not Path(sidecar_path_text).is_absolute()
                    or not isinstance(sidecar_hash, str)
                    or re.fullmatch(r"[0-9a-f]{64}", sidecar_hash) is None
                    or not isinstance(sidecar_stat, dict)
                    or not isinstance(sidecar_stat.get("size"), int)
                ):
                    results.append({"source": transcript_id + ":sidecar", "status": "unverifiable"})
                else:
                    sidecar_path = Path(sidecar_path_text)
                    sidecar_size = sidecar_path.stat(follow_symlinks=False).st_size
                    if sidecar_path.is_symlink() or not sidecar_path.is_file():
                        raise OSError
                    if sidecar_size != sidecar_stat["size"]:
                        sidecar_status = "drifted"
                    elif bytes_hashed + sidecar_size > MAX_JSONL_TOTAL_BUDGET_BYTES:
                        sidecar_status = "unverifiable_budget"
                    else:
                        bytes_hashed += sidecar_size
                        sidecar_status = "unchanged" if sha256_file(sidecar_path) == sidecar_hash else "drifted"
                    results.append({"source": transcript_id + ":sidecar", "status": sidecar_status})
                    if sidecar_status == "drifted":
                        warnings.append("source_sidecar_drift_detected")
        except (OSError, ValueError, RuntimeError):
            results.append({"source": transcript_id, "status": "missing_or_unreadable"})
            warnings.append("source_transcript_unavailable")
    return results


def verify_bundle(bundle: Path, *, check_sources: bool = False) -> dict[str, Any]:
    root = bundle.expanduser()
    errors: list[str] = []
    warnings: list[str] = []
    if root.is_symlink() or not root.is_dir():
        return {"ok": False, "errors": ["bundle_root_invalid"], "warnings": [], "sources": []}
    root = root.resolve(strict=True)
    files, directories, entry_errors = _bundle_files(root)
    errors.extend(entry_errors)
    checksums = _load_json(root / "CHECKSUMS.json")
    if (
        not isinstance(checksums, dict)
        or checksums.get("schema_version") != SCHEMA_VERSION
        or checksums.get("algorithm") != "sha256"
        or checksums.get("claim") != CHECKSUM_CLAIM
        or not isinstance(checksums.get("files"), list)
    ):
        errors.append("checksums_invalid")
        return {"ok": False, "errors": sorted(set(errors)), "warnings": warnings, "sources": []}

    expected: dict[str, dict[str, Any]] = {}
    for row in checksums["files"]:
        if not isinstance(row, dict) or not isinstance(row.get("path"), str):
            errors.append("checksum_row_invalid")
            continue
        relative = row["path"]
        if relative.startswith("/") or ".." in Path(relative).parts or relative in expected or relative == "CHECKSUMS.json":
            errors.append("checksum_path_invalid")
            continue
        expected[relative] = row
    actual_names = set(files) - {"CHECKSUMS.json"}
    expected_names = set(expected)
    if actual_names - expected_names:
        errors.append("unexpected_bundle_file")
    if expected_names - actual_names:
        errors.append("missing_bundle_file")
    for relative in sorted(actual_names & expected_names):
        path = files[relative]
        row = expected[relative]
        try:
            if path.stat().st_size != row.get("size"):
                errors.append("size_mismatch")
            if mode_string(path) != row.get("mode"):
                errors.append("mode_mismatch")
            if sha256_file(path) != row.get("sha256"):
                errors.append("checksum_mismatch")
        except OSError:
            errors.append("bundle_file_unreadable")

    bundle_meta = _load_json(root / "BUNDLE.json")
    if not isinstance(bundle_meta, dict) or bundle_meta.get("schema_version") != SCHEMA_VERSION:
        errors.append("bundle_schema_invalid")
        bundle_meta = {}
    if bundle_meta:
        if (
            bundle_meta.get("transcript_text_copied") is not False
            or bundle_meta.get("network_used") is not False
            or bundle_meta.get("identity_assessment") != "not_performed"
            or bundle_meta.get("checksum_claim") != CHECKSUM_CLAIM
        ):
            errors.append("privacy_contract_invalid")
        raw_routes = bundle_meta.get("routes_generated")
        if (
            isinstance(raw_routes, list)
            and raw_routes
            and all(isinstance(route, str) and route in ROUTES for route in raw_routes)
            and len(raw_routes) == len(set(raw_routes))
        ):
            routes = raw_routes
        else:
            routes = []
            errors.append("routes_invalid")
        allowed = _allowed_files(routes)
        allowed_directories: set[str] = set()
        for relative in allowed:
            parent = Path(relative).parent
            while parent != Path("."):
                allowed_directories.add(parent.as_posix())
                parent = parent.parent
        if allowed - expected_names:
            errors.append("required_bundle_file_missing")
        if expected_names - allowed:
            errors.append("unexpected_bundle_file")
        if directories != allowed_directories:
            errors.append("unexpected_or_missing_bundle_directory")
        if not errors:
            _validate_schema_and_closure(root, bundle_meta, errors)

    source_results: list[dict[str, Any]] = []
    if check_sources:
        if errors:
            warnings.append("source_check_skipped_due_to_bundle_errors")
        else:
            source_results = _check_sources(root, warnings, errors)
    else:
        warnings.append("source_drift_not_checked")
    return {
        "ok": not errors,
        "errors": sorted(set(errors)),
        "warnings": sorted(set(warnings)),
        "sources": source_results,
    }
