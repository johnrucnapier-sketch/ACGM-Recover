"""Evidence-first project-family analysis and structural session mapping."""

from __future__ import annotations

import hashlib
import json
import os
import re
import stat
import unicodedata
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from .constants import (
    CONFIDENCE_LEVELS,
    DEFAULT_EXCLUDED_DIRS,
    MAX_INVENTORY_FILES,
    SENSITIVE_FILE_NAMES,
)
from .gitfacts import family_roots, find_git_root, inspect_git
from .sanitize import sanitize_path, sanitize_untrusted
from .scan import scan_jsonl_roots, scan_metadata_roots
from .util import RecoverError, path_is_within, stat_snapshot, unique_existing_dirs, utc_now

SAFE_CODE_RE = re.compile(r"^[a-z0-9][a-z0-9_.:-]{0,95}$")
CONTENT_PROJECT_VALUES = {"this-project", "external-project", "mixed", "unknown"}
MAPPING_STATUS_VALUES = {"confirmed", "misopened", "mixed", "candidate", "unresolved"}
DECISION_STATUS_VALUES = {"implemented", "active", "superseded", "proposed", "unverified"}
PUBLIC_EVENT_TYPES = {
    "attachment",
    "assistant",
    "user",
    "last-prompt",
    "custom-title",
    "mode",
    "queue-operation",
    "system",
    "ai-title",
    "frame-link",
}


def load_annotations(path: Path | None) -> dict[str, Any]:
    if path is None:
        return {
            "schema_version": "1.0",
            "transcripts_by_sha256": {},
            "sessions_by_id": {},
            "known_gaps": [],
            "decisions": [],
            "continuation": {},
            "_source_sha256": None,
            "_source_stable": True,
        }
    try:
        source = path.expanduser()
        if source.is_symlink() or source.stat().st_size > 16 * 1024 * 1024:
            raise OSError
        before = stat_snapshot(source)
        payload = source.read_bytes()
        decoded = json.loads(payload.decode("utf-8"))
        after = stat_snapshot(source)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, RecursionError) as exc:
        raise RecoverError("annotations_invalid") from exc
    if before != after:
        raise RecoverError("annotations_changed_during_read")
    if not isinstance(decoded, dict) or decoded.get("schema_version") != "1.0":
        raise RecoverError("annotations_schema_invalid")
    for key in ("transcripts_by_sha256", "sessions_by_id"):
        if not isinstance(decoded.get(key, {}), dict):
            raise RecoverError("annotations_schema_invalid")
    if not isinstance(decoded.get("known_gaps", []), list):
        raise RecoverError("annotations_schema_invalid")
    if not isinstance(decoded.get("decisions", []), list) or not isinstance(decoded.get("continuation", {}), dict):
        raise RecoverError("annotations_schema_invalid")
    return {
        "schema_version": "1.0",
        "transcripts_by_sha256": decoded.get("transcripts_by_sha256", {}),
        "sessions_by_id": decoded.get("sessions_by_id", {}),
        "known_gaps": decoded.get("known_gaps", []),
        "decisions": decoded.get("decisions", []),
        "continuation": decoded.get("continuation", {}),
        "_source_sha256": hashlib.sha256(payload).hexdigest(),
        "_source_stable": True,
    }


def _validated_annotation(value: Any) -> dict[str, Any] | None:
    if not isinstance(value, dict):
        return None
    if value.get("human_reviewed") is not True or value.get("share_approved") is not True:
        return None
    content_project = value.get("content_project", "unknown")
    mapping_status = value.get("mapping_status", "unresolved")
    confidence = value.get("confidence", "unresolved")
    if not isinstance(content_project, str) or content_project not in CONTENT_PROJECT_VALUES:
        return None
    if not isinstance(mapping_status, str) or mapping_status not in MAPPING_STATUS_VALUES:
        return None
    if not isinstance(confidence, str) or confidence not in CONFIDENCE_LEVELS:
        return None
    evidence_codes: list[str] = []
    raw_evidence_codes = value.get("evidence_codes", [])
    if not isinstance(raw_evidence_codes, list):
        return None
    for code in raw_evidence_codes:
        if isinstance(code, str) and SAFE_CODE_RE.fullmatch(code):
            evidence_codes.append(code)
    raw_label = value.get("private_content_label")
    label = sanitize_untrusted(raw_label, limit=160) if isinstance(raw_label, str) else ""
    project_ref = value.get("content_project_ref")
    if project_ref is not None and (not isinstance(project_ref, str) or not SAFE_CODE_RE.fullmatch(project_ref)):
        project_ref = None
    return {
        "content_project": content_project,
        "mapping_status": mapping_status,
        "confidence": confidence,
        "evidence_codes": sorted(set(evidence_codes)),
        "private_content_label": label or None,
        "content_project_ref": project_ref,
        "human_reviewed": bool(value.get("human_reviewed", False)),
    }


def _sanitize_share_text(value: Any, *, limit: int) -> str:
    if not isinstance(value, str):
        return ""
    text = sanitize_untrusted(value, limit=limit)
    text = re.sub(r"(?<![:\w])/(?:[^\s,;]+)", "[LOCAL_PATH_REDACTED]", text)
    text = re.sub(r"(?i)\b[A-Z]:\\[^\r\n,;]+", "[LOCAL_PATH_REDACTED]", text)
    return text.strip()


def _reviewed_decisions(annotations: dict[str, Any]) -> tuple[list[dict[str, Any]], int]:
    rows: list[dict[str, Any]] = []
    rejected = 0
    for index, raw in enumerate(annotations.get("decisions", []), start=1):
        if (
            not isinstance(raw, dict)
            or raw.get("human_reviewed") is not True
            or raw.get("share_approved") is not True
        ):
            rejected += 1
            continue
        summary = _sanitize_share_text(raw.get("summary"), limit=800)
        status = raw.get("status")
        confidence = raw.get("confidence")
        if (
            not summary
            or not isinstance(status, str)
            or status not in DECISION_STATUS_VALUES
            or not isinstance(confidence, str)
            or confidence not in CONFIDENCE_LEVELS
            or not isinstance(raw.get("current_artifact_corroborated", False), bool)
        ):
            rejected += 1
            continue
        raw_id = raw.get("decision_id")
        decision_id = raw_id if isinstance(raw_id, str) and SAFE_CODE_RE.fullmatch(raw_id) else f"D-{index:04d}"
        raw_evidence_hashes = raw.get("evidence_transcript_sha256", [])
        if not isinstance(raw_evidence_hashes, list):
            rejected += 1
            continue
        evidence_hashes = sorted(
            {
                value.lower()
                for value in raw_evidence_hashes
                if isinstance(value, str) and re.fullmatch(r"[0-9a-fA-F]{64}", value)
            }
        )
        rows.append(
            {
                "decision_id": decision_id,
                "summary": summary,
                "status": status,
                "confidence": confidence,
                "evidence_transcript_sha256": evidence_hashes,
                "current_artifact_corroborated": raw.get("current_artifact_corroborated", False),
                "human_reviewed": True,
                "share_approved": True,
                "interpretation": "historical_data_not_current_execution_authority",
            }
        )
    return rows, rejected


def _reviewed_continuation(annotations: dict[str, Any]) -> tuple[dict[str, Any], bool]:
    raw = annotations.get("continuation", {})
    if (
        not isinstance(raw, dict)
        or raw.get("human_reviewed") is not True
        or raw.get("share_approved") is not True
    ):
        return {}, False
    objective = _sanitize_share_text(raw.get("objective"), limit=800)
    if not objective:
        return {}, False

    def clean_list(name: str) -> list[str]:
        result: list[str] = []
        raw_values = raw.get(name, [])
        if not isinstance(raw_values, list):
            return result
        for value in raw_values:
            if not isinstance(value, str):
                continue
            cleaned = _sanitize_share_text(value, limit=600)
            if cleaned:
                result.append(cleaned)
            if len(result) >= 20:
                break
        return result

    return (
        {
            "objective": objective,
            "next_steps": clean_list("next_steps"),
            "blocked_by": clean_list("blocked_by"),
            "human_reviewed": True,
            "share_approved": True,
            "interpretation": "handoff_data_requires_fresh_runtime_authority",
        },
        True,
    )


def _annotation_for(record: dict[str, Any], annotations: dict[str, Any]) -> dict[str, Any] | None:
    sha = record.get("sha256")
    if isinstance(sha, str):
        found = _validated_annotation(annotations["transcripts_by_sha256"].get(sha))
        if found is not None:
            return found
    if record.get("category") == "main_transcript":
        for session_id in record.get("observed", {}).get("session_ids", []):
            found = _validated_annotation(annotations["sessions_by_id"].get(str(session_id)))
            if found is not None:
                return found
    return None


def _match_cwd(raw: Any, roots: list[Path]) -> str | None:
    if not isinstance(raw, str):
        return None
    if len(raw) > 4096 or "\x00" in raw or any(ord(char) < 32 for char in raw):
        return None
    try:
        candidate = Path(raw).expanduser()
        if not candidate.is_absolute():
            return None
        resolved = candidate.resolve(strict=False)
    except (OSError, ValueError, RuntimeError):
        return None
    matches = [(index, root) for index, root in enumerate(roots, start=1) if path_is_within(resolved, root)]
    if not matches:
        return None
    index, _ = max(matches, key=lambda pair: len(str(pair[1])))
    return f"W-{index:03d}"


def _structural_matches(record: dict[str, Any], roots: list[Path]) -> tuple[list[str], bool]:
    values = list(record.get("observed", {}).get("cwds", [])) + list(
        record.get("observed", {}).get("origin_cwds", [])
    )
    aliases: set[str] = set()
    external = False
    for value in values:
        alias = _match_cwd(value, roots)
        if alias:
            aliases.add(alias)
        elif isinstance(value, str) and Path(value).is_absolute():
            external = True
    return sorted(aliases), external


def _metadata_structural_matches(record: dict[str, Any], roots: list[Path]) -> tuple[list[str], bool]:
    aliases: set[str] = set()
    external = False
    for field in ("cwd", "origin_cwd"):
        value = record.get(field)
        alias = _match_cwd(value, roots)
        if alias:
            aliases.add(alias)
        elif isinstance(value, str) and Path(value).is_absolute():
            external = True
    return sorted(aliases), external


def _source_locator(value: Any) -> str:
    return sanitize_path(value, home=None, limit=600)


def _vendor_data_boundaries(roots: list[Path]) -> list[Path]:
    boundaries: set[Path] = set()
    for root in roots:
        resolved = root.resolve(strict=False)
        parts = resolved.parts
        normalized_parts = [unicodedata.normalize("NFC", part).casefold() for part in parts]
        if ".claude" in normalized_parts:
            index = normalized_parts.index(".claude")
            boundaries.add(Path(*parts[: index + 1]))
        if unicodedata.normalize("NFC", resolved.parent.name).casefold() in {"claude", "claude-3p"}:
            boundaries.add(resolved.parent)
    return sorted((path for path in boundaries if path.is_dir()), key=str)


def _public_event_counts(raw: dict[str, Any]) -> dict[str, int]:
    result = {key: 0 for key in sorted(PUBLIC_EVENT_TYPES)}
    result["other"] = 0
    for key, count in raw.items():
        target = key if key in PUBLIC_EVENT_TYPES else "other"
        if isinstance(count, int) and count >= 0:
            result[target] += count
    return {key: value for key, value in result.items() if value}


def _inventory_family(
    roots: list[Path],
) -> tuple[list[dict[str, Any]], dict[str, Any], bool, dict[str, int]]:
    rows: list[dict[str, Any]] = []
    private_paths: dict[str, Any] = {}
    capped = False
    counter = 0
    stats = {"read_errors": 0, "special_files_ignored": 0, "symlinks_ignored": 0}
    for root_index, root in enumerate(roots, start=1):
        stack: list[tuple[Path, Path]] = [(root, Path("."))]
        while stack and counter < MAX_INVENTORY_FILES:
            current, relative_parent = stack.pop()
            try:
                entries = sorted(os.scandir(current), key=lambda entry: entry.name, reverse=True)
            except OSError:
                stats["read_errors"] += 1
                continue
            for entry in entries:
                if counter >= MAX_INVENTORY_FILES:
                    capped = True
                    break
                relative = relative_parent / entry.name
                try:
                    if entry.is_symlink():
                        kind = "symlink_ignored"
                        stats["symlinks_ignored"] += 1
                        source_stat = entry.stat(follow_symlinks=False)
                    elif entry.is_dir(follow_symlinks=False):
                        if entry.name in DEFAULT_EXCLUDED_DIRS:
                            continue
                        stack.append((Path(entry.path), relative))
                        continue
                    elif entry.is_file(follow_symlinks=False):
                        kind = "regular_file"
                        source_stat = entry.stat(follow_symlinks=False)
                    else:
                        kind = "special_file_ignored"
                        stats["special_files_ignored"] += 1
                        source_stat = entry.stat(follow_symlinks=False)
                except OSError:
                    stats["read_errors"] += 1
                    continue
                counter += 1
                file_id = f"F-{counter:06d}"
                sensitive_name = entry.name.lower() in SENSITIVE_FILE_NAMES or entry.name.lower().startswith(".env.")
                rows.append(
                    {
                        "file_id": file_id,
                        "worktree_id": f"W-{root_index:03d}",
                        "kind": kind,
                        "size": int(source_stat.st_size),
                        "mtime_ns": int(source_stat.st_mtime_ns),
                        "mode": format(stat.S_IMODE(source_stat.st_mode), "04o"),
                        "sensitive_filename": sensitive_name,
                        "content_read": False,
                    }
                )
                private_paths[file_id] = {
                    "relative_path": "[SENSITIVE_FILENAME_WITHHELD]"
                    if sensitive_name
                    else sanitize_path(str(relative), home=None, limit=600)
                }
    if stack:
        capped = True
    return rows, private_paths, capped, stats


def _flatten_metadata_files(scanned: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for source in scanned:
        for record in source.get("records", []):
            row = dict(record)
            row.update(
                {
                    "source_path": source["source_path"],
                    "source_sha256": source.get("sha256"),
                    "source_parse_status": source.get("parse_status"),
                    "source_stable_during_read": source.get("source_stable_during_read"),
                    "storage_route_observed": source.get("storage_route_observed"),
                    "sensitive_fields_present": source.get("sensitive_fields_present", False),
                }
            )
            rows.append(row)
    return rows


def _assign_duplicates(records: list[dict[str, Any]]) -> None:
    first_by_hash: dict[str, int] = {}
    for index, record in enumerate(records):
        sha = record.get("sha256")
        if not isinstance(sha, str):
            record["duplicate_index"] = None
        elif sha in first_by_hash:
            record["duplicate_index"] = first_by_hash[sha]
        else:
            first_by_hash[sha] = index
            record["duplicate_index"] = None


def _propagate_lineage_structural(records: list[dict[str, Any]]) -> dict[int, list[int]]:
    owners: dict[tuple[str, str], list[int]] = defaultdict(list)
    for index, record in enumerate(records):
        if record.get("category") not in {"main_transcript", "subagent_transcript"}:
            continue
        if record.get("duplicate_index") is not None:
            continue
        session_ids = {str(value) for value in record.get("observed", {}).get("session_ids", [])}
        for tool_use_id in record.get("observed", {}).get("tool_use_ids", []):
            for session_id in session_ids:
                owners[(session_id, str(tool_use_id))].append(index)
    parents: dict[int, list[int]] = {}
    main_by_session: dict[str, list[int]] = defaultdict(list)
    for index, record in enumerate(records):
        if record.get("category") != "main_transcript" or record.get("duplicate_index") is not None:
            continue
        for session_id in record.get("observed", {}).get("session_ids", []):
            main_by_session[str(session_id)].append(index)
    for index, record in enumerate(records):
        parent_candidates: set[int] = set()
        if record.get("category") == "subagent_transcript":
            session_ids = {str(value) for value in record.get("observed", {}).get("session_ids", [])}
            parent_tool_ids = record.get("observed", {}).get("parent_tool_use_ids", [])
            for tool_use_id in parent_tool_ids:
                for session_id in session_ids:
                    parent_candidates.update(owners.get((session_id, str(tool_use_id)), []))
            if parent_candidates:
                record["lineage_basis"] = "tool_use_id" if len(parent_candidates) == 1 else "ambiguous_tool_use_id"
            elif not parent_tool_ids:
                for session_id in session_ids:
                    parent_candidates.update(main_by_session.get(session_id, []))
                record["lineage_basis"] = (
                    "parent_session_id" if len(parent_candidates) == 1 else "ambiguous_parent_session_id"
                )
            else:
                record["lineage_basis"] = "unmatched_tool_use_id"
        parent_candidates.discard(index)
        parents[index] = sorted(parent_candidates)

    changed = True
    while changed:
        changed = False
        for index, parent_indices in parents.items():
            record = records[index]
            if record.get("category") != "subagent_transcript" or record.get("structural_worktrees"):
                continue
            inherited: set[str] = set()
            if len(parent_indices) == 1:
                inherited.update(records[parent_indices[0]].get("structural_worktrees", []))
            if inherited:
                record["structural_worktrees"] = sorted(inherited)
                record["structural_match_basis"] = (
                    "tool_use_lineage"
                    if record.get("lineage_basis") == "tool_use_id"
                    else "parent_session_lineage"
                )
                changed = True
    return parents


def _gap_rows(
    annotations: dict[str, Any],
    selected: list[dict[str, Any]],
    *,
    source_scan: dict[str, int],
    inventory_capped: bool,
    inventory_stats: dict[str, int],
    recovery_readiness: str,
    rejected_decisions: int,
) -> list[dict[str, Any]]:
    gaps: list[dict[str, Any]] = []
    gap_counter = 0

    def add(code: str, scope: str, evidence: list[str] | None = None) -> None:
        nonlocal gap_counter
        gap_counter += 1
        gaps.append(
            {
                "gap_id": f"G-{gap_counter:04d}",
                "code": code,
                "scope": scope,
                "evidence_ids": evidence or [],
                "status": "open",
            }
        )

    if not any(record.get("category") == "main_transcript" for record in selected):
        add("no_main_transcript_structurally_matched", "project")
    if any(
        record.get("category") == "main_transcript" and record.get("content_project") == "unknown"
        for record in selected
    ):
        add("main_transcript_content_project_unreviewed", "project")
    if any(record.get("parse", {}).get("status") != "ok" for record in selected):
        add("transcript_parse_incomplete", "project")
    if any(not record.get("source_stable_during_read") for record in selected):
        add("source_changed_or_unstable_during_read", "project")
    if any(record.get("lineage_basis") == "unmatched_tool_use_id" for record in selected):
        add("subagent_parent_tool_use_unmatched", "session_lineage")
    if any(
        record.get("sidecar", {}).get("present")
        and (
            record.get("sidecar", {}).get("parse_status") != "ok"
            or record.get("sidecar", {}).get("source_stable_during_read") is not True
        )
        for record in selected
    ):
        add("subagent_sidecar_unusable", "session_lineage")
    if source_scan.get("metadata_parse_failures", 0):
        add("metadata_sources_not_fully_parsed", "source_scan")
    if source_scan.get("metadata_files_skipped_budget", 0):
        add("metadata_global_scan_budget_exhausted", "source_scan")
    if source_scan.get("metadata_records_skipped_budget", 0):
        add("metadata_record_budget_exhausted", "source_scan")
    if source_scan.get("jsonl_parse_failures", 0):
        add("jsonl_sources_not_fully_parsed", "source_scan")
    if source_scan.get("unstable_sources", 0):
        add("source_scan_observed_changes", "source_scan")
    if source_scan.get("quarantined_jsonl", 0):
        add("unclassified_jsonl_quarantined", "source_scan")
    if source_scan.get("jsonl_files_skipped_budget", 0):
        add("jsonl_global_scan_budget_exhausted", "source_scan")
    if source_scan.get("quarantined_worktree_candidates", 0):
        add("worktree_candidates_quarantined", "git_worktrees")
    if inventory_capped:
        add("file_inventory_capped", "project_inventory")
    if inventory_stats.get("read_errors", 0):
        add("file_inventory_read_errors", "project_inventory")
    if rejected_decisions:
        add("decision_annotations_rejected", "human_annotation")
    if recovery_readiness != "HANDOFF_READY":
        add("continuity_handoff_not_ready", "recovery_readiness")
    for raw in annotations.get("known_gaps", []):
        if not isinstance(raw, dict):
            continue
        code = raw.get("code")
        if isinstance(code, str) and SAFE_CODE_RE.fullmatch(code):
            add(code, "human_annotation")
    return gaps


def analyze_project(
    project: Path,
    *,
    claude_projects_roots: list[Path],
    metadata_roots: list[Path],
    auxiliary_roots: list[Path],
    annotations_path: Path | None = None,
) -> dict[str, Any]:
    raw_project = project.expanduser()
    if raw_project.is_symlink() or not raw_project.is_dir():
        raise RecoverError("project_root_invalid")
    project_root = raw_project.resolve(strict=True)
    annotations = load_annotations(annotations_path)
    git = inspect_git(project_root)
    if git["is_git_repository"] and Path(str(git["git_root"])).resolve(strict=False) != project_root:
        raise RecoverError("project_must_be_git_root")
    roots = family_roots(project_root, git)
    selected_worktree_id = next(
        (f"W-{index:03d}" for index, root in enumerate(roots, start=1) if root == project_root),
        None,
    )
    if selected_worktree_id is None:
        raise RecoverError("selected_worktree_not_validated")

    metadata_roots = unique_existing_dirs(metadata_roots)
    transcript_roots = unique_existing_dirs(claude_projects_roots)
    auxiliary_roots = unique_existing_dirs(auxiliary_roots)
    metadata_diagnostics: dict[str, int] = {}
    metadata_files = scan_metadata_roots(metadata_roots, diagnostics=metadata_diagnostics)
    metadata_records = _flatten_metadata_files(metadata_files)
    jsonl_specs = [(root, "claude_projects") for root in transcript_roots]
    jsonl_specs += [(root, "homunculus") for root in auxiliary_roots]
    jsonl_specs += [(root, "local_agent") for root in metadata_roots if "local-agent-mode-sessions" in str(root)]
    jsonl_diagnostics: dict[str, int] = {}
    transcripts = scan_jsonl_roots(jsonl_specs, diagnostics=jsonl_diagnostics)
    _assign_duplicates(transcripts)

    metadata_worktrees_by_session: dict[str, set[str]] = defaultdict(set)
    for record in metadata_records:
        matches, external = _metadata_structural_matches(record, roots)
        record["structural_worktrees"] = matches
        record["has_external_cwd"] = external
        session_id = record.get("session_id")
        if session_id is not None:
            metadata_worktrees_by_session[str(session_id)].update(matches)

    for record in transcripts:
        matches, external = _structural_matches(record, roots)
        basis = "transcript_internal_cwd" if matches else None
        if not matches:
            inherited: set[str] = set()
            for session_id in record.get("observed", {}).get("session_ids", []):
                inherited.update(metadata_worktrees_by_session.get(str(session_id), set()))
            if inherited:
                matches = sorted(inherited)
                basis = "session_metadata_link"
        record["structural_worktrees"] = matches
        record["has_external_cwd"] = external
        record["structural_match_basis"] = basis
        annotation = _annotation_for(record, annotations)
        record["annotation"] = annotation
        record["content_project"] = annotation["content_project"] if annotation else "unknown"
        if annotation:
            record["mapping_status"] = annotation["mapping_status"]
        elif matches and external:
            record["mapping_status"] = "mixed"
        elif matches:
            record["mapping_status"] = "candidate"
        else:
            record["mapping_status"] = "unresolved"

    lineage_parents = _propagate_lineage_structural(transcripts)
    for record in transcripts:
        if record.get("structural_worktrees") and record.get("mapping_status") == "unresolved":
            record["mapping_status"] = "candidate"
    selected_set = {
        index
        for index, record in enumerate(transcripts)
        if record.get("structural_worktrees") or record.get("content_project") == "this-project"
    }
    selected_main_sessions = {
        str(session_id)
        for index in selected_set
        if transcripts[index].get("category") == "main_transcript"
        for session_id in transcripts[index].get("observed", {}).get("session_ids", [])
    }
    for index, record in enumerate(transcripts):
        if index in selected_set or record.get("category") != "subagent_transcript":
            continue
        record_sessions = {str(value) for value in record.get("observed", {}).get("session_ids", [])}
        if record_sessions & selected_main_sessions and record.get("lineage_basis") in {
            "unmatched_tool_use_id",
            "ambiguous_tool_use_id",
            "ambiguous_parent_session_id",
        }:
            record["inclusion_basis"] = "orphan_subagent_same_parent_session"
            selected_set.add(index)
    changed = True
    while changed:
        changed = False
        for index, record in enumerate(transcripts):
            if index in selected_set or record.get("category") != "subagent_transcript":
                continue
            if any(parent in selected_set for parent in lineage_parents.get(index, [])):
                record["inclusion_basis"] = "lineage_to_selected_transcript"
                selected_set.add(index)
                changed = True
    selected_indices = sorted(selected_set)
    selected = [transcripts[index] for index in selected_indices]

    raw_session_ids = sorted(
        {
            str(value)
            for record in selected
            for value in record.get("observed", {}).get("session_ids", [])
            if value is not None
        }
    )
    session_aliases = {value: f"S-{index:04d}" for index, value in enumerate(raw_session_ids, start=1)}
    transcript_aliases = {index: f"T-{position:04d}" for position, index in enumerate(selected_indices, start=1)}

    linked_raw_session_ids = set(raw_session_ids)
    selected_metadata: list[dict[str, Any]] = []
    for record in metadata_records:
        matches = record["structural_worktrees"]
        external = record["has_external_cwd"]
        session_link = str(record.get("session_id")) in linked_raw_session_ids if record.get("session_id") else False
        if not matches and not session_link:
            continue
        copy = dict(record)
        copy["structural_worktrees"] = matches
        copy["has_external_cwd"] = external
        copy["linked_by_session_id"] = session_link
        selected_metadata.append(copy)

    metadata_aliases = {index: f"M-{index + 1:04d}" for index in range(len(selected_metadata))}
    file_inventory, private_file_paths, inventory_capped, inventory_stats = _inventory_family(roots)

    public_transcripts: list[dict[str, Any]] = []
    private_transcripts: dict[str, Any] = {}
    corrections: list[dict[str, Any]] = []
    for source_index in selected_indices:
        record = transcripts[source_index]
        transcript_id = transcript_aliases[source_index]
        parent_ids = [
            transcript_aliases[parent]
            for parent in lineage_parents.get(source_index, [])
            if parent in transcript_aliases
        ]
        duplicate_id = transcript_aliases.get(record.get("duplicate_index"))
        session_ids = [
            session_aliases[str(value)]
            for value in record.get("observed", {}).get("session_ids", [])
            if str(value) in session_aliases
        ]
        annotation = record.get("annotation")
        public_transcripts.append(
            {
                "transcript_id": transcript_id,
                "category": record["category"],
                "source_kind": record["source_kind"],
                "session_ids": session_ids,
                "structural_project": "this-project" if record["structural_worktrees"] else "unresolved",
                "structural_worktrees": record["structural_worktrees"],
                "structural_match_basis": record["structural_match_basis"],
                "has_external_cwd": record["has_external_cwd"],
                "content_project": record["content_project"],
                "content_project_ref": annotation.get("content_project_ref") if annotation else None,
                "mapping_status": record["mapping_status"],
                "content_reviewed": bool(annotation and annotation.get("human_reviewed")),
                "parent_transcripts": parent_ids,
                "lineage_basis": record.get("lineage_basis"),
                "inclusion_basis": record.get("inclusion_basis")
                or ("structural_mapping" if record["structural_worktrees"] else "human_content_correction"),
                "duplicate_of": duplicate_id,
                "sha256": record.get("sha256"),
                "size": record["size"],
                "mtime_ns": record["mtime_ns"],
                "parse": record["parse"],
                "event_type_counts": _public_event_counts(record["observed"]["event_types"]),
                "message_counts": record["observed"]["counts"],
                "display_model_value_observed": bool(record["observed"]["display_models"]),
                "model_identity_assessment": "not_performed",
                "sensitive_fields_present": bool(record["observed"]["sensitive_fields_present"]),
                "source_stable_during_read": record["source_stable_during_read"],
                "sidecar_present": bool(record.get("sidecar", {}).get("present")),
                "sidecar_parse_status": record.get("sidecar", {}).get("parse_status"),
                "sidecar_source_stable": record.get("sidecar", {}).get("source_stable_during_read"),
                "sidecar_sha256": record.get("sidecar", {}).get("sha256"),
                "sidecar_lineage_accepted": bool(record["observed"].get("sidecar_lineage_accepted", False)),
            }
        )
        private_transcripts[transcript_id] = {
            "source_path": _source_locator(record["source_path"]),
            "source_root": _source_locator(record["source_root"]),
            "session_id_hashes": [
                hashlib.sha256(str(value).encode("utf-8", errors="replace")).hexdigest()
                for value in record["observed"]["session_ids"]
            ],
            "cwd_value_count": len(record["observed"]["cwds"]),
            "origin_cwd_value_count": len(record["observed"]["origin_cwds"]),
            "git_branch_value_count": len(record["observed"]["git_branches"]),
            "display_model_label_count": len(record["observed"]["display_models"]),
            "sha256": record.get("sha256"),
            "stat": {"size": record["size"], "mtime_ns": record["mtime_ns"]},
            "private_content_label": annotation.get("private_content_label") if annotation else None,
            "sidecar_source_path": _source_locator(record.get("sidecar", {}).get("source_path"))
            if record.get("sidecar", {}).get("present")
            else None,
            "sidecar_sha256": record.get("sidecar", {}).get("sha256"),
            "sidecar_stat": {
                "size": record.get("sidecar", {}).get("size"),
                "mtime_ns": record.get("sidecar", {}).get("mtime_ns"),
            }
            if record.get("sidecar", {}).get("present")
            else None,
        }
        if annotation:
            corrections.append(
                {
                    "transcript_id": transcript_id,
                    "content_project": annotation["content_project"],
                    "content_project_ref": annotation.get("content_project_ref"),
                    "mapping_status": annotation["mapping_status"],
                    "confidence": annotation["confidence"],
                    "evidence_codes": annotation["evidence_codes"],
                    "human_reviewed": annotation["human_reviewed"],
                }
            )

    public_metadata: list[dict[str, Any]] = []
    private_metadata: dict[str, Any] = {}
    for index, record in enumerate(selected_metadata):
        metadata_id = metadata_aliases[index]
        session_id = record.get("session_id")
        session_alias = session_aliases.get(str(session_id)) if session_id is not None else None
        public_metadata.append(
            {
                "metadata_id": metadata_id,
                "session_id": session_alias,
                "storage_route_observed": record["storage_route_observed"],
                "structural_worktrees": record["structural_worktrees"],
                "has_external_cwd": record["has_external_cwd"],
                "linked_by_session_id": record["linked_by_session_id"],
                "title_present": bool(record.get("title")),
                "display_model_value_observed": bool(record.get("display_model")),
                "model_identity_assessment": "not_performed",
                "transcript_unavailable_flag": record.get("transcript_unavailable")
                if isinstance(record.get("transcript_unavailable"), bool)
                else None,
                "archived_flag": record.get("archived") if isinstance(record.get("archived"), bool) else None,
                "source_parse_status": record["source_parse_status"],
                "source_stable_during_read": record["source_stable_during_read"],
                "sensitive_fields_present": record["sensitive_fields_present"],
            }
        )
        private_metadata[metadata_id] = {
            "source_path": _source_locator(record["source_path"]),
            "source_sha256": record.get("source_sha256"),
            "session_id_hash": hashlib.sha256(str(session_id).encode("utf-8", errors="replace")).hexdigest()
            if session_id is not None
            else None,
            "title_value_copied": False,
            "cwd_value_copied": False,
            "display_model_label_copied": False,
        }

    decisions, rejected_decisions = _reviewed_decisions(annotations)
    transcript_id_by_hash = {
        row["sha256"]: row["transcript_id"] for row in public_transcripts if isinstance(row.get("sha256"), str)
    }
    for decision in decisions:
        decision["evidence_transcript_ids"] = [
            transcript_id_by_hash[value]
            for value in decision.pop("evidence_transcript_sha256")
            if value in transcript_id_by_hash
        ]
    continuation, continuation_reviewed = _reviewed_continuation(annotations)
    has_main = any(row["category"] == "main_transcript" for row in public_transcripts)
    main_content_reviewed = bool(has_main) and all(
        row["content_reviewed"] for row in public_transcripts if row["category"] == "main_transcript"
    )
    relevant_main_reviewed = any(
        row["category"] == "main_transcript"
        and row["content_reviewed"]
        and row["content_project"] in {"this-project", "mixed"}
        for row in public_transcripts
    )
    decisions_evidenced = bool(decisions) and all(
        (decision["evidence_transcript_ids"] or decision["current_artifact_corroborated"])
        and decision["status"] in {"implemented", "active", "superseded"}
        and decision["confidence"] in {"verified", "corroborated", "probable"}
        for decision in decisions
    )
    selected_sources_complete = all(
        row["parse"]["status"] == "ok" and row["source_stable_during_read"] for row in public_transcripts
    )
    metadata_sources_complete = all(
        row.get("parse_status") == "ok" and row.get("source_stable_during_read") is not False
        for row in metadata_files
    )
    current_state_complete = bool(
        git.get("observation_status") in {"complete", "not_repository"}
        and git.get("object_store_observation_status") in {"complete", "not_applicable"}
        and
        git["source_stable_during_read"]
        and git["status"]["readable"]
        and not inventory_capped
        and inventory_stats["read_errors"] == 0
    )
    base_handoff_ready = (
        decisions_evidenced
        and continuation_reviewed
        and main_content_reviewed
        and relevant_main_reviewed
        and selected_sources_complete
        and metadata_sources_complete
        and current_state_complete
    )
    recovery_readiness = "REVIEW_REQUIRED" if has_main else "STRUCTURAL_ONLY"

    evidence_manifest: list[dict[str, Any]] = [
        {
            "evidence_id": "E-0001",
            "source_type": "current_filesystem",
            "scope": "selected_project_family",
            "freshness": "current_observation",
            "content_copied": False,
        },
        {
            "evidence_id": "E-0002",
            "source_type": "git_current_state",
            "scope": "selected_project_family",
            "freshness": "current_observation",
            "content_copied": False,
        },
    ]
    evidence_by_transcript: dict[str, str] = {}
    evidence_counter = 2
    for row in public_transcripts:
        evidence_counter += 1
        evidence_id = f"E-{evidence_counter:04d}"
        evidence_by_transcript[row["transcript_id"]] = evidence_id
        evidence_manifest.append(
            {
                "evidence_id": evidence_id,
                "source_type": row["category"],
                "scope": row["transcript_id"],
                "freshness": "historical",
                "content_copied": False,
                "duplicate_of": row["duplicate_of"],
                "sidecar": {
                    "present": row["sidecar_present"],
                    "parse_status": row["sidecar_parse_status"],
                    "source_stable": row["sidecar_source_stable"],
                    "sha256": row["sidecar_sha256"],
                },
            }
        )
    annotation_evidence_id: str | None = None
    if annotations_path is not None:
        evidence_counter += 1
        annotation_evidence_id = f"E-{evidence_counter:04d}"
        evidence_manifest.append(
            {
                "evidence_id": annotation_evidence_id,
                "source_type": "human_annotations",
                "scope": "reviewed_content_and_continuity",
                "freshness": "current_human_review",
                "content_copied": bool(decisions or continuation),
                "sha256": annotations["_source_sha256"],
            }
        )

    claims: list[dict[str, Any]] = [
        {
            "claim_id": "C-0001",
            "subject_id": "P-0001",
            "claim_type": "current_project_exists",
            "assertion": {"exists": True},
            "evidence_ids": ["E-0001"],
            "confidence": "verified",
            "conflict_status": "none",
            "time_scope": "current",
            "generated_by": "claude-code-recover",
            "review_status": "machine_observed",
        },
        {
            "claim_id": "C-0002",
            "subject_id": "P-0001",
            "claim_type": "git_repository_state",
            "assertion": {
                "is_git_repository": git["is_git_repository"],
                "head": git["head"],
                "branch": git["branch"],
                "dirty": git["status"]["dirty"],
                "worktree_count": len(roots),
                "selected_worktree_id": selected_worktree_id,
            },
            "evidence_ids": ["E-0002"],
            "confidence": "verified"
            if git.get("observation_status") in {"complete", "not_repository"}
            and git["source_stable_during_read"]
            else "unresolved",
            "conflict_status": "none",
            "time_scope": "current",
            "generated_by": "claude-code-recover",
            "review_status": "machine_observed",
        },
    ]
    claim_counter = 2
    for row in public_transcripts:
        claim_counter += 1
        evidence_id = evidence_by_transcript[row["transcript_id"]]
        if not row["source_stable_during_read"] or row["parse"]["status"] != "ok":
            structural_confidence = "unresolved"
        elif row["duplicate_of"]:
            structural_confidence = "candidate"
        elif row["structural_match_basis"] == "transcript_internal_cwd":
            structural_confidence = "verified"
        elif row["structural_match_basis"] == "tool_use_lineage":
            structural_confidence = "corroborated"
        elif row["structural_match_basis"] in {"session_metadata_link", "parent_session_lineage"}:
            structural_confidence = "probable"
        else:
            structural_confidence = "unresolved"
        claims.append(
            {
                "claim_id": f"C-{claim_counter:04d}",
                "subject_id": row["transcript_id"],
                "claim_type": "structural_project_mapping",
                "assertion": {
                    "structural_project": row["structural_project"],
                    "worktrees": row["structural_worktrees"],
                    "basis": row["structural_match_basis"],
                },
                "evidence_ids": [evidence_id],
                "confidence": structural_confidence,
                "conflict_status": "contradictory" if not row["source_stable_during_read"] else "none",
                "time_scope": "historical",
                "generated_by": "claude-code-recover",
                "review_status": "machine_observed",
            }
        )
        if row["content_reviewed"]:
            claim_counter += 1
            correction = next(item for item in corrections if item["transcript_id"] == row["transcript_id"])
            claims.append(
                {
                    "claim_id": f"C-{claim_counter:04d}",
                    "subject_id": row["transcript_id"],
                    "claim_type": "content_project_mapping",
                    "assertion": {
                        "content_project": row["content_project"],
                        "content_project_ref": row["content_project_ref"],
                        "mapping_status": row["mapping_status"],
                    },
                    "evidence_ids": [annotation_evidence_id] if annotation_evidence_id else [],
                    "confidence": correction["confidence"],
                    "conflict_status": "none",
                    "time_scope": "historical",
                    "generated_by": "human_annotation",
                    "review_status": "human_reviewed",
                }
            )
    for decision in decisions:
        claim_counter += 1
        decision_evidence = [
            evidence_by_transcript[transcript_id]
            for transcript_id in decision["evidence_transcript_ids"]
            if transcript_id in evidence_by_transcript
        ]
        if annotation_evidence_id:
            decision_evidence.append(annotation_evidence_id)
        claims.append(
            {
                "claim_id": f"C-{claim_counter:04d}",
                "subject_id": decision["decision_id"],
                "claim_type": "human_reviewed_historical_decision",
                "assertion": {
                    "summary": decision["summary"],
                    "status": decision["status"],
                    "current_artifact_corroborated": decision["current_artifact_corroborated"],
                },
                "evidence_ids": decision_evidence,
                "confidence": decision["confidence"],
                "conflict_status": "none",
                "time_scope": "historical",
                "generated_by": "human_annotation",
                "review_status": "human_reviewed",
            }
        )

    conflicts: list[dict[str, Any]] = []
    conflict_counter = 0
    for row in public_transcripts:
        if not row["source_stable_during_read"]:
            conflict_counter += 1
            conflicts.append(
                {
                    "conflict_id": f"X-{conflict_counter:04d}",
                    "code": "source_unstable_during_scan",
                    "subject_id": row["transcript_id"],
                    "conflict_status": "contradictory",
                    "resolution": "unresolved",
                }
            )
        if row["mapping_status"] == "misopened":
            conflict_counter += 1
            conflicts.append(
                {
                    "conflict_id": f"X-{conflict_counter:04d}",
                    "code": "structural_and_content_project_differ",
                    "subject_id": row["transcript_id"],
                    "conflict_status": "superseded",
                    "resolution": "human_annotation_preserved",
                }
            )

    source_scan = {
        "metadata_files_scanned": len(metadata_files),
        "metadata_parse_failures": sum(1 for row in metadata_files if row.get("parse_status") != "ok"),
        "metadata_files_skipped_budget": metadata_diagnostics.get("source_files_skipped_budget", 0),
        "metadata_records_skipped_budget": metadata_diagnostics.get("records_skipped_budget", 0),
        "jsonl_files_scanned": len(transcripts),
        "jsonl_parse_failures": sum(1 for row in transcripts if row.get("parse", {}).get("status") != "ok"),
        "unstable_sources": sum(1 for row in metadata_files if row.get("source_stable_during_read") is False)
        + sum(1 for row in transcripts if row.get("source_stable_during_read") is False),
        "quarantined_jsonl": sum(1 for row in transcripts if row.get("category") == "unknown_jsonl"),
        "jsonl_files_skipped_budget": jsonl_diagnostics.get("source_files_skipped_budget", 0),
        "quarantined_worktree_candidates": sum(
            1 for row in git.get("worktrees", []) if not row.get("trusted_family_member", False)
        ),
    }
    critical_quality_ok = bool(
        source_scan["metadata_parse_failures"] == 0
        and source_scan["metadata_files_skipped_budget"] == 0
        and source_scan["metadata_records_skipped_budget"] == 0
        and source_scan["jsonl_parse_failures"] == 0
        and source_scan["jsonl_files_skipped_budget"] == 0
        and source_scan["unstable_sources"] == 0
        and source_scan["quarantined_worktree_candidates"] == 0
        and rejected_decisions == 0
        and not any(row.get("category") == "unknown_jsonl" for row in selected)
        and not any(
            row.get("lineage_basis") in {
                "unmatched_tool_use_id",
                "ambiguous_tool_use_id",
                "ambiguous_parent_session_id",
            }
            for row in selected
        )
        and not any(
            row.get("sidecar", {}).get("present")
            and (
                row.get("sidecar", {}).get("parse_status") != "ok"
                or row.get("sidecar", {}).get("source_stable_during_read") is not True
            )
            for row in selected
        )
    )
    if base_handoff_ready and critical_quality_ok:
        recovery_readiness = "HANDOFF_READY"
    gaps = _gap_rows(
        annotations,
        selected,
        source_scan=source_scan,
        inventory_capped=inventory_capped,
        inventory_stats=inventory_stats,
        recovery_readiness=recovery_readiness,
        rejected_decisions=rejected_decisions,
    )
    public_git = {
        "is_git_repository": git["is_git_repository"],
        "observation_status": git.get("observation_status"),
        "head": git["head"],
        "branch": git["branch"],
        "status": git["status"],
        "object_store_observation_status": git.get("object_store_observation_status"),
        "object_store_count": len(git.get("object_directories", [])),
        "remotes": git["remotes"],
        "source_stable_during_read": git["source_stable_during_read"],
    }
    public_worktrees: list[dict[str, Any]] = []
    private_worktrees: dict[str, Any] = {}
    git_worktrees_by_path = {
        str(Path(str(item["path"])).resolve(strict=False)): item for item in git.get("worktrees", []) if item.get("path")
    }
    for index, root in enumerate(roots, start=1):
        alias = f"W-{index:03d}"
        details = git_worktrees_by_path.get(str(root), {})
        public_worktrees.append(
            {
                "worktree_id": alias,
                "branch": details.get("branch") or (git["branch"] if root == project_root else None),
                "head": details.get("head") or (git["head"] if root == project_root else None),
                "status": details.get("status") or (git["status"] if root == project_root else None),
                "detached": bool(details.get("detached", False)),
                "locked": bool(details.get("locked", False)),
                "prunable": bool(details.get("prunable", False)),
            }
        )
        private_worktrees[alias] = {"source_path": _source_locator(root)}

    categories = Counter(row["category"] for row in public_transcripts)
    private_source_diagnostics: list[dict[str, Any]] = []

    def add_private_diagnostic(source_type: str, source_path: Any, reason: str, **facts: Any) -> None:
        private_source_diagnostics.append(
            {
                "diagnostic_id": f"SD-{len(private_source_diagnostics) + 1:05d}",
                "source_type": source_type,
                "source_path": _source_locator(source_path),
                "reason": reason,
                **facts,
            }
        )

    for row in metadata_files:
        if row.get("parse_status") != "ok" or row.get("source_stable_during_read") is False:
            add_private_diagnostic(
                "session_metadata",
                row.get("source_path"),
                str(row.get("parse_status")),
                size=row.get("size"),
                sha256=row.get("sha256"),
                source_stable=row.get("source_stable_during_read"),
            )
    for row in transcripts:
        if (
            row.get("category") == "unknown_jsonl"
            or row.get("parse", {}).get("status") != "ok"
            or row.get("source_stable_during_read") is False
        ):
            add_private_diagnostic(
                "jsonl",
                row.get("source_path"),
                "quarantined" if row.get("category") == "unknown_jsonl" else str(row.get("parse", {}).get("status")),
                category=row.get("category"),
                size=row.get("size"),
                sha256=row.get("sha256"),
                source_stable=row.get("source_stable_during_read"),
            )
    for source_path in metadata_diagnostics.get("skipped_source_paths", []):
        add_private_diagnostic("session_metadata", source_path, "global_budget_skipped")
    for source_path in jsonl_diagnostics.get("skipped_source_paths", []):
        add_private_diagnostic("jsonl", source_path, "global_budget_skipped")

    result = {
        "observed_at": utc_now(),
        "project": {
            "project_id": "P-0001",
            "selected_worktree_id": selected_worktree_id,
            "exists": True,
            "is_git_repository": git["is_git_repository"],
            "worktree_count": len(roots),
            "file_inventory_count": len(file_inventory),
            "file_inventory_capped": inventory_capped,
            "recovery_readiness": recovery_readiness,
        },
        "git": public_git,
        "worktrees": public_worktrees,
        "file_inventory": file_inventory,
        "metadata": public_metadata,
        "transcripts": public_transcripts,
        "corrections": corrections,
        "decisions": decisions,
        "continuation": continuation,
        "recovery_readiness": recovery_readiness,
        "source_scan": source_scan,
        "inventory_stats": inventory_stats,
        "evidence_manifest": evidence_manifest,
        "claims": claims,
        "conflicts": conflicts,
        "gaps": gaps,
        "summary": {
            "metadata_records": len(public_metadata),
            "transcript_records": len(public_transcripts),
            "transcript_categories": dict(sorted(categories.items())),
            "content_reviewed": sum(1 for row in public_transcripts if row["content_reviewed"]),
            "content_unreviewed": sum(1 for row in public_transcripts if row["content_project"] == "unknown"),
            "human_reviewed_decisions": len(decisions),
            "continuation_reviewed": continuation_reviewed,
            "recovery_readiness": recovery_readiness,
            "conflicts": len(conflicts),
            "gaps": len(gaps),
        },
        "private": {
            "project_root": _source_locator(project_root),
            "git_root": _source_locator(git["git_root"]) if git["git_root"] else None,
            "git_common_dir": _source_locator(git["common_dir"]) if git["common_dir"] else None,
            "git_object_directories": [
                _source_locator(path) for path in git.get("object_directories", [])
            ],
            "worktrees": private_worktrees,
            "files": private_file_paths,
            "transcripts": private_transcripts,
            "metadata": private_metadata,
            "source_diagnostics": private_source_diagnostics,
            "annotations_path": _source_locator(annotations_path) if annotations_path else None,
            "annotations_sha256": annotations["_source_sha256"],
        },
        "source_roots": [
            project_root,
            *roots,
            *(
                [Path(str(git["common_dir"])).resolve(strict=False)]
                if git.get("common_dir") and Path(str(git["common_dir"])).is_dir()
                else []
            ),
            *[
                Path(str(path)).resolve(strict=False)
                for path in git.get("object_directories", [])
                if Path(str(path)).is_dir()
            ],
            *transcript_roots,
            *metadata_roots,
            *auxiliary_roots,
            *_vendor_data_boundaries([*transcript_roots, *metadata_roots, *auxiliary_roots]),
        ],
    }
    return result


def discover_candidates(
    *,
    claude_projects_roots: list[Path],
    metadata_roots: list[Path],
    auxiliary_roots: list[Path],
    registry_path: Path | None,
) -> dict[str, Any]:
    metadata_roots = unique_existing_dirs(metadata_roots)
    transcript_roots = unique_existing_dirs(claude_projects_roots)
    auxiliary_roots = unique_existing_dirs(auxiliary_roots)
    metadata_diagnostics: dict[str, int] = {}
    metadata = _flatten_metadata_files(scan_metadata_roots(metadata_roots, diagnostics=metadata_diagnostics))
    jsonl_diagnostics: dict[str, int] = {}
    jsonl = scan_jsonl_roots(
        [(root, "claude_projects") for root in transcript_roots]
        + [(root, "homunculus") for root in auxiliary_roots]
        + [(root, "local_agent") for root in metadata_roots if "local-agent-mode-sessions" in str(root)],
        diagnostics=jsonl_diagnostics,
    )
    candidates: dict[str, dict[str, Any]] = {}

    def add(raw: Any, source: str) -> None:
        if not isinstance(raw, str):
            return
        if len(raw) > 4096 or "\x00" in raw or any(ord(char) < 32 for char in raw):
            return
        try:
            path = Path(raw).expanduser()
            if not path.is_absolute():
                return
            resolved = path.resolve(strict=False)
            evidence_source = source
            if not resolved.is_dir():
                ancestor = resolved.parent
                recovered_root: Path | None = None
                for _ in range(32):
                    if ancestor == ancestor.parent:
                        break
                    if ancestor.is_dir():
                        recovered_root = find_git_root(ancestor)
                        break
                    ancestor = ancestor.parent
                if recovered_root is None:
                    return
                resolved = recovered_root
                evidence_source = source + "_surviving_git_ancestor"
        except (OSError, ValueError, RuntimeError):
            return
        key = str(resolved)
        item = candidates.setdefault(
            key,
            {
                "path": sanitize_path(resolved, home=None, limit=600),
                "exists": True,
                "evidence_counts": Counter(),
            },
        )
        item["evidence_counts"][evidence_source] += 1

    for record in metadata:
        add(record.get("cwd"), "session_metadata")
        add(record.get("origin_cwd"), "session_metadata")
    for record in jsonl:
        for raw in record.get("observed", {}).get("cwds", []):
            add(raw, record["category"])
        for raw in record.get("observed", {}).get("origin_cwds", []):
            add(raw, record["category"])

    if registry_path is not None and registry_path.is_file() and not registry_path.is_symlink():
        try:
            if registry_path.stat().st_size <= 32 * 1024 * 1024:
                registry = json.loads(registry_path.read_text(encoding="utf-8"))
                projects = registry.get("projects") if isinstance(registry, dict) else None
                if isinstance(projects, dict):
                    for raw_path in projects.keys():
                        add(raw_path, "claude_project_registry_key")
        except (OSError, UnicodeDecodeError, json.JSONDecodeError, RecursionError):
            pass

    grouped: dict[str, dict[str, Any]] = {}
    for path_text, item in sorted(candidates.items()):
        path = Path(path_text)
        git_root = find_git_root(path)
        if git_root is not None:
            facts = inspect_git(git_root)
            family_key = facts.get("common_dir") or str(git_root)
            family_type = "git_common_directory"
        else:
            family_key = str(path)
            family_type = "directory"
        family = grouped.setdefault(
            str(family_key),
            {
                "family_id": None,
                "family_type": family_type,
                "paths": [],
                "recommended_project_roots": [],
                "evidence_counts": Counter(),
            },
        )
        family["paths"].append(item["path"])
        family["recommended_project_roots"].append(
            sanitize_path(git_root if git_root is not None else path, home=None, limit=600)
        )
        family["evidence_counts"].update(item["evidence_counts"])

    families: list[dict[str, Any]] = []
    for index, (_, item) in enumerate(sorted(grouped.items()), start=1):
        families.append(
            {
                "family_id": f"P-{index:04d}",
                "family_type": item["family_type"],
                "paths": sorted(set(item["paths"])),
                "recommended_project_roots": sorted(set(item["recommended_project_roots"])),
                "evidence_counts": dict(sorted(item["evidence_counts"].items())),
            }
        )
    return {
        "schema_version": "1.0",
        "observed_at": utc_now(),
        "scope": "surviving_local_evidence_only",
        "families": families,
        "scan_diagnostics": jsonl_diagnostics,
        "metadata_scan_diagnostics": metadata_diagnostics,
        "warnings": [
            "paths_are_candidates_not_content_project_proof",
            "project_bucket_keys_are_not_reversible",
            "display_model_labels_are_not_model_identity",
        ]
        + (["jsonl_global_scan_budget_exhausted"] if jsonl_diagnostics.get("source_files_skipped_budget") else [])
        + (["metadata_global_scan_budget_exhausted"] if metadata_diagnostics.get("source_files_skipped_budget") else []),
    }
