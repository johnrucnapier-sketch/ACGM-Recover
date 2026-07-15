"""Bounded scanners for observed Claude Code local storage structures."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

from .constants import (
    MAX_JSONL_BYTES,
    MAX_JSONL_LINE_BYTES,
    MAX_JSONL_LINES,
    MAX_JSONL_SOURCE_FILES,
    MAX_JSONL_TOTAL_BUDGET_BYTES,
    MAX_METADATA_BYTES,
    MAX_METADATA_RECORDS_PER_FILE,
    MAX_METADATA_RECORDS_TOTAL,
    MAX_METADATA_SOURCE_FILES,
    MAX_METADATA_TOTAL_BUDGET_BYTES,
    MAX_SIDECAR_BYTES,
    MAX_TOOL_IDENTIFIERS,
    MAX_VALUES_PER_FIELD,
)
from .sanitize import sensitive_keys_present
from .util import is_regular_file, iter_regular_files, stat_snapshot


_MAX_METADATA_SESSION_ID_CHARS = 512
_MAX_METADATA_PATH_CHARS = 4096
_MAX_METADATA_TITLE_CHARS = 512
_MAX_METADATA_MODEL_CHARS = 512
_MAX_METADATA_TIMESTAMP_CHARS = 128
_MAX_METADATA_COMPLETED_TURNS = (1 << 63) - 1


def _route_from_root(root: Path) -> str:
    value = str(root)
    if "Claude-3p" in value:
        return "claude-3p-storage"
    if "Application Support/Claude/" in value:
        return "claude-storage"
    return "user-supplied-storage"


def _append_observed(
    observed: dict[str, Any],
    key: str,
    value: Any,
    *,
    limit: int = MAX_VALUES_PER_FIELD,
    expected_type: type = str,
    max_string_length: int = 4096,
) -> None:
    if value is None or value == "":
        return
    valid = isinstance(value, expected_type)
    if expected_type is int and isinstance(value, bool):
        valid = False
    if not valid:
        observed["invalid_field_types"][key] = observed["invalid_field_types"].get(key, 0) + 1
        return
    if isinstance(value, str) and len(value) > max_string_length:
        observed["field_caps"][key] = True
        return
    seen = observed["_seen_fields"].setdefault(key, set())
    marker = str(value) if not isinstance(value, (bool, int, float)) else (type(value).__name__, value)
    if marker in seen:
        return
    if len(observed[key]) >= limit:
        observed["field_caps"][key] = True
        return
    seen.add(marker)
    observed[key].append(value)


def _walk_dicts(value: Any, *, depth: int = 0, max_depth: int = 16) -> Iterator[dict[str, Any]]:
    if depth > max_depth:
        return
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from _walk_dicts(child, depth=depth + 1, max_depth=max_depth)
    elif isinstance(value, list):
        for child in value:
            yield from _walk_dicts(child, depth=depth + 1, max_depth=max_depth)


def _contains_control_character(value: str) -> bool:
    """Reject C0/C1 controls before a metadata string enters derived output."""

    return any(ord(character) < 32 or 127 <= ord(character) <= 159 for character in value)


def _validated_metadata_field(
    mapping: dict[str, Any],
    names: tuple[str, ...],
    *,
    expected_type: type,
    max_string_length: int | None = None,
    reject_controls: bool = False,
) -> tuple[Any, bool]:
    """Return a bounded allowlisted value and whether every selected value was valid."""

    selected: Any = None
    all_valid = True
    for name in names:
        if name not in mapping:
            continue
        value = mapping[name]
        if value is None or value == "":
            continue
        if expected_type is bool:
            if type(value) is not bool:
                all_valid = False
                continue
        elif expected_type is int:
            if type(value) is not int or not 0 <= value <= _MAX_METADATA_COMPLETED_TURNS:
                all_valid = False
                continue
        elif expected_type is str:
            if type(value) is not str:
                all_valid = False
                continue
            if max_string_length is None or len(value) > max_string_length:
                all_valid = False
                continue
            if reject_controls and _contains_control_character(value):
                all_valid = False
                continue
        else:  # pragma: no cover - all callers use the closed set above
            raise TypeError(f"unsupported metadata field type: {expected_type!r}")
        if selected is None:
            selected = value
    return selected, all_valid


def _metadata_candidate(mapping: dict[str, Any], *, local_named: bool) -> bool:
    has_identity = any(key in mapping for key in ("sessionId", "session_id", "id"))
    has_context = any(
        key in mapping
        for key in (
            "cwd",
            "originCwd",
            "origin_cwd",
            "title",
            "completedTurns",
            "transcriptUnavailable",
        )
    )
    return (has_identity and has_context) or (local_named and has_context)


def scan_metadata_file(path: Path, root: Path) -> dict[str, Any]:
    before = stat_snapshot(path)
    result: dict[str, Any] = {
        "source_path": str(path),
        "source_root": str(root),
        "storage_route_observed": _route_from_root(root),
        "size": before["size"],
        "mtime_ns": before["mtime_ns"],
        "sha256": None,
        "parse_status": "unread",
        "source_stable_during_read": None,
        "records": [],
        "sensitive_fields_present": False,
    }
    if before["size"] > MAX_METADATA_BYTES:
        result["parse_status"] = "size_limit"
        result["source_stable_during_read"] = stat_snapshot(path) == before
        return result
    try:
        payload = path.read_bytes()
    except OSError:
        result["parse_status"] = "read_error"
        return result
    result["sha256"] = hashlib.sha256(payload).hexdigest()
    try:
        decoded = json.loads(payload.decode("utf-8"))
    except (UnicodeDecodeError, ValueError, RecursionError):
        result["parse_status"] = "invalid_json"
        result["source_stable_during_read"] = stat_snapshot(path) == before
        return result
    result["sensitive_fields_present"] = sensitive_keys_present(decoded)
    local_named = path.name.startswith("local_")
    seen: set[str] = set()
    records: list[dict[str, Any]] = []
    record_limit_hit = False
    schema_violations = 0
    candidates_seen = 0
    for candidate in _walk_dicts(decoded):
        if not _metadata_candidate(candidate, local_named=local_named):
            continue
        if candidates_seen >= MAX_METADATA_RECORDS_PER_FILE:
            record_limit_hit = True
            break
        candidates_seen += 1
        session_id, session_id_valid = _validated_metadata_field(
            candidate,
            ("sessionId", "session_id", "id"),
            expected_type=str,
            max_string_length=_MAX_METADATA_SESSION_ID_CHARS,
            reject_controls=True,
        )
        title, title_valid = _validated_metadata_field(
            candidate,
            ("title", "name"),
            expected_type=str,
            max_string_length=_MAX_METADATA_TITLE_CHARS,
            reject_controls=True,
        )
        cwd, cwd_valid = _validated_metadata_field(
            candidate,
            ("cwd", "workingDirectory", "working_directory"),
            expected_type=str,
            max_string_length=_MAX_METADATA_PATH_CHARS,
            reject_controls=True,
        )
        origin_cwd, origin_cwd_valid = _validated_metadata_field(
            candidate,
            ("originCwd", "origin_cwd"),
            expected_type=str,
            max_string_length=_MAX_METADATA_PATH_CHARS,
            reject_controls=True,
        )
        display_model, display_model_valid = _validated_metadata_field(
            candidate,
            ("model", "modelName", "model_name"),
            expected_type=str,
            max_string_length=_MAX_METADATA_MODEL_CHARS,
            reject_controls=True,
        )
        created_at, created_at_valid = _validated_metadata_field(
            candidate,
            ("createdAt", "created_at"),
            expected_type=str,
            max_string_length=_MAX_METADATA_TIMESTAMP_CHARS,
            reject_controls=True,
        )
        last_activity_at, last_activity_at_valid = _validated_metadata_field(
            candidate,
            ("lastActivityAt", "last_activity_at", "updatedAt", "updated_at"),
            expected_type=str,
            max_string_length=_MAX_METADATA_TIMESTAMP_CHARS,
            reject_controls=True,
        )
        completed_turns, completed_turns_valid = _validated_metadata_field(
            candidate,
            ("completedTurns", "completed_turns"),
            expected_type=int,
        )
        archived, archived_valid = _validated_metadata_field(
            candidate,
            ("isArchived", "archived"),
            expected_type=bool,
        )
        transcript_unavailable, transcript_unavailable_valid = _validated_metadata_field(
            candidate,
            ("transcriptUnavailable", "transcript_unavailable"),
            expected_type=bool,
        )
        field_validity = (
            session_id_valid,
            title_valid,
            cwd_valid,
            origin_cwd_valid,
            display_model_valid,
            created_at_valid,
            last_activity_at_valid,
            completed_turns_valid,
            archived_valid,
            transcript_unavailable_valid,
        )
        schema_violations += sum(not valid for valid in field_validity)
        record = {
            "session_id": session_id,
            "title": title,
            "cwd": cwd,
            "origin_cwd": origin_cwd,
            "display_model": display_model,
            "created_at": created_at,
            "last_activity_at": last_activity_at,
            "completed_turns": completed_turns,
            "archived": archived,
            "transcript_unavailable": transcript_unavailable,
        }
        if not any(value is not None for value in record.values()):
            continue
        fingerprint = json.dumps(record, ensure_ascii=False, sort_keys=True)
        if fingerprint in seen:
            continue
        if len(records) >= MAX_METADATA_RECORDS_PER_FILE:
            record_limit_hit = True
            break
        seen.add(fingerprint)
        records.append(record)
    result["records"] = records
    if record_limit_hit:
        result["parse_status"] = "record_limit"
    elif schema_violations and records:
        result["parse_status"] = "structural_partial"
    elif schema_violations:
        result["parse_status"] = "invalid_schema"
    else:
        result["parse_status"] = "ok"
    try:
        result["source_stable_during_read"] = stat_snapshot(path) == before
    except OSError:
        result["source_stable_during_read"] = False
    return result


def scan_metadata_roots(
    roots: list[Path],
    diagnostics: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    scanned: list[dict[str, Any]] = []
    seen: set[Path] = set()
    stats = diagnostics if diagnostics is not None else {}
    stats.update(
        {
            "source_files_seen": 0,
            "source_files_scanned": 0,
            "source_files_skipped_budget": 0,
            "source_bytes_budgeted": 0,
            "skipped_source_paths": [],
            "skipped_paths_capped": 0,
            "records_collected": 0,
            "records_skipped_budget": 0,
        }
    )
    for root in roots:
        for path in iter_regular_files(root, (".json",)):
            resolved = path.resolve(strict=False)
            if resolved in seen or not is_regular_file(path):
                continue
            seen.add(resolved)
            stats["source_files_seen"] += 1
            if stats["records_collected"] >= MAX_METADATA_RECORDS_TOTAL:
                stats["source_files_skipped_budget"] += 1
                stats["records_skipped_budget"] += 1
                if len(stats["skipped_source_paths"]) < 1000:
                    stats["skipped_source_paths"].append(str(path))
                else:
                    stats["skipped_paths_capped"] += 1
                continue
            try:
                planned = min(path.stat(follow_symlinks=False).st_size, MAX_METADATA_BYTES)
            except OSError:
                stats["source_files_skipped_budget"] += 1
                if len(stats["skipped_source_paths"]) < 1000:
                    stats["skipped_source_paths"].append(str(path))
                else:
                    stats["skipped_paths_capped"] += 1
                continue
            if (
                stats["source_files_scanned"] >= MAX_METADATA_SOURCE_FILES
                or stats["source_bytes_budgeted"] + planned > MAX_METADATA_TOTAL_BUDGET_BYTES
            ):
                stats["source_files_skipped_budget"] += 1
                if len(stats["skipped_source_paths"]) < 1000:
                    stats["skipped_source_paths"].append(str(path))
                else:
                    stats["skipped_paths_capped"] += 1
                continue
            stats["source_files_scanned"] += 1
            stats["source_bytes_budgeted"] += planned
            result = scan_metadata_file(path, root)
            records = result.get("records", [])
            remaining = MAX_METADATA_RECORDS_TOTAL - stats["records_collected"]
            if isinstance(records, list) and len(records) > remaining:
                result["records"] = records[:remaining]
                stats["records_skipped_budget"] += len(records) - remaining
                result["parse_status"] = "global_record_limit"
            stats["records_collected"] += len(result.get("records", []))
            scanned.append(result)
    return sorted(scanned, key=lambda item: str(item["source_path"]))


def classify_jsonl(path: Path, root: Path, source_kind: str, observed: dict[str, Any]) -> str:
    relative_parts = tuple(part.lower() for part in path.relative_to(root).parts)
    name = path.name.lower()
    if source_kind == "local_agent":
        return "local_agent_output"
    if source_kind == "homunculus":
        return "homunculus_observations"
    if "subagents" in relative_parts or name.startswith("agent-"):
        sidechain = set(observed.get("sidechain_values", []))
        session_ids = {str(value) for value in observed.get("session_ids", [])}
        agent_ids = {str(value) for value in observed.get("agent_ids", [])}
        try:
            subagents_index = relative_parts.index("subagents")
            parent_session = path.relative_to(root).parts[subagents_index - 1] if subagents_index > 0 else ""
        except (ValueError, IndexError):
            parent_session = ""
        stem_matches_agent = len(agent_ids) == 1 and any(
            path.stem in {value, f"agent-{value}"} for value in agent_ids
        )
        if (
            sidechain == {True}
            and len(session_ids) == 1
            and parent_session in session_ids
            and stem_matches_agent
        ):
            return "subagent_transcript"
        return "unknown_jsonl"
    if any(part in {"tool-results", "tool_results", "tasks"} for part in relative_parts):
        return "tool_result_or_task"
    if any(part in {"metrics", "cache", "caches"} for part in relative_parts):
        return "metrics_or_cache"
    if len(relative_parts) == 2:
        session_ids = {str(value) for value in observed.get("session_ids", [])}
        sidechain = set(observed.get("sidechain_values", []))
        if session_ids == {path.stem} and sidechain == {False} and not observed.get("agent_ids"):
            return "main_transcript"
        return "unknown_jsonl"
    return "unknown_jsonl"


def _count_content_blocks(message: Any, observed: dict[str, Any]) -> None:
    if not isinstance(message, dict):
        return
    content = message.get("content")
    if not isinstance(content, list):
        return
    for block in content:
        if not isinstance(block, dict):
            continue
        block_type = block.get("type")
        if block_type == "tool_use":
            observed["counts"]["tool_use"] += 1
            _append_observed(
                observed,
                "tool_use_ids",
                block.get("id"),
                limit=MAX_TOOL_IDENTIFIERS,
                max_string_length=512,
            )
        elif block_type == "tool_result":
            observed["counts"]["tool_result"] += 1
            _append_observed(
                observed,
                "parent_tool_use_ids",
                block.get("tool_use_id"),
                limit=MAX_TOOL_IDENTIFIERS,
                max_string_length=512,
            )


def _observe_event(obj: Any, observed: dict[str, Any]) -> None:
    if not isinstance(obj, dict):
        return
    event_type = obj.get("type")
    valid_event_type = event_type if isinstance(event_type, str) else None
    if isinstance(event_type, str):
        safe_event_type = event_type if event_type in {
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
            "tool_use",
            "tool_result",
        } else "__other__"
        observed["event_types"][safe_event_type] = observed["event_types"].get(safe_event_type, 0) + 1
        if event_type in {"user", "assistant", "system"}:
            observed["counts"][event_type] += 1
        elif event_type == "tool_use":
            observed["counts"]["tool_use"] += 1
        elif event_type == "tool_result":
            observed["counts"]["tool_result"] += 1
    elif event_type is not None:
        observed["invalid_field_types"]["event_type"] = (
            observed["invalid_field_types"].get("event_type", 0) + 1
        )
    message = obj.get("message")
    if isinstance(message, dict):
        role = message.get("role")
        if isinstance(role, str) and role in {"user", "assistant", "system"} and valid_event_type not in {
            "user",
            "assistant",
            "system",
        }:
            observed["counts"][role] += 1
        elif role is not None and not isinstance(role, str):
            observed["invalid_field_types"]["message_role"] = (
                observed["invalid_field_types"].get("message_role", 0) + 1
            )
        _count_content_blocks(message, observed)
        _append_observed(observed, "display_models", message.get("model"))
    _append_observed(observed, "session_ids", obj.get("sessionId") or obj.get("session_id"))
    _append_observed(observed, "cwds", obj.get("cwd"))
    _append_observed(observed, "origin_cwds", obj.get("originCwd") or obj.get("origin_cwd"))
    _append_observed(observed, "git_branches", obj.get("gitBranch") or obj.get("git_branch"))
    _append_observed(observed, "agent_ids", obj.get("agentId") or obj.get("agent_id"))
    _append_observed(
        observed,
        "tool_use_ids",
        obj.get("toolUseId") if valid_event_type == "tool_use" else None,
        limit=MAX_TOOL_IDENTIFIERS,
        max_string_length=512,
    )
    _append_observed(
        observed,
        "parent_tool_use_ids",
        obj.get("toolUseId") if valid_event_type != "tool_use" else None,
        limit=MAX_TOOL_IDENTIFIERS,
        max_string_length=512,
    )
    _append_observed(observed, "parent_uuids", obj.get("parentUuid") or obj.get("parent_uuid"))
    _append_observed(observed, "sidechain_values", obj.get("isSidechain"), expected_type=bool)
    spawn_depth = obj.get("spawnDepth") if "spawnDepth" in obj else obj.get("spawn_depth")
    _append_observed(observed, "spawn_depths", spawn_depth, expected_type=int)
    _append_observed(observed, "timestamps", obj.get("timestamp") or obj.get("createdAt"))
    _append_observed(observed, "entrypoints", obj.get("entrypoint"))
    if sensitive_keys_present(obj):
        observed["sensitive_fields_present"] = True


def scan_jsonl_file(path: Path, root: Path, source_kind: str) -> dict[str, Any]:
    before = stat_snapshot(path)
    observed: dict[str, Any] = {
        "session_ids": [],
        "cwds": [],
        "origin_cwds": [],
        "git_branches": [],
        "display_models": [],
        "agent_ids": [],
        "tool_use_ids": [],
        "parent_tool_use_ids": [],
        "parent_uuids": [],
        "sidechain_values": [],
        "spawn_depths": [],
        "timestamps": [],
        "entrypoints": [],
        "event_types": {},
        "counts": {"user": 0, "assistant": 0, "system": 0, "tool_use": 0, "tool_result": 0},
        "sensitive_fields_present": False,
        "field_caps": {},
        "invalid_field_types": {},
        "_seen_fields": {},
    }
    digest = hashlib.sha256()
    bytes_seen = 0
    lines_seen = 0
    json_ok = 0
    json_failed = 0
    long_lines = 0
    capped = False
    ends_with_newline = False
    read_error = False
    try:
        with path.open("rb") as handle:
            while lines_seen < MAX_JSONL_LINES and bytes_seen <= MAX_JSONL_BYTES:
                chunk = handle.readline(MAX_JSONL_LINE_BYTES + 1)
                if not chunk:
                    break
                digest.update(chunk)
                bytes_seen += len(chunk)
                lines_seen += 1
                ends_with_newline = chunk.endswith(b"\n")
                if len(chunk) > MAX_JSONL_LINE_BYTES and not ends_with_newline:
                    long_lines += 1
                    while chunk and not chunk.endswith(b"\n"):
                        if bytes_seen > MAX_JSONL_BYTES:
                            capped = True
                            break
                        chunk = handle.readline(MAX_JSONL_LINE_BYTES + 1)
                        digest.update(chunk)
                        bytes_seen += len(chunk)
                    ends_with_newline = chunk.endswith(b"\n")
                    if capped:
                        break
                    continue
                if bytes_seen > MAX_JSONL_BYTES:
                    capped = True
                    break
                try:
                    obj = json.loads(chunk.decode("utf-8"))
                except (UnicodeDecodeError, json.JSONDecodeError, RecursionError):
                    json_failed += 1
                    continue
                json_ok += 1
                _observe_event(obj, observed)
            if lines_seen >= MAX_JSONL_LINES or bytes_seen > MAX_JSONL_BYTES:
                capped = True
    except OSError:
        read_error = True

    try:
        after = stat_snapshot(path)
        stable = before == after
    except OSError:
        stable = False
    full_hash = digest.hexdigest() if not capped and not read_error and bytes_seen == before["size"] else None
    if read_error:
        parse_status = "read_error"
    elif capped:
        parse_status = "bounded_partial"
    elif json_failed or long_lines:
        parse_status = "malformed_partial"
    elif before["size"] > 0 and not ends_with_newline:
        parse_status = "truncated_partial"
    elif before["size"] == 0:
        parse_status = "empty"
    elif observed["field_caps"] or observed["invalid_field_types"]:
        parse_status = "structural_partial"
    else:
        parse_status = "ok"
    result = {
        "source_path": str(path),
        "source_root": str(root),
        "source_kind": source_kind,
        "category": classify_jsonl(path, root, source_kind, observed),
        "size": before["size"],
        "mtime_ns": before["mtime_ns"],
        "sha256": full_hash,
        "parse": {
            "status": parse_status,
            "lines_seen": lines_seen,
            "json_ok": json_ok,
            "json_failed": json_failed,
            "long_lines": long_lines,
            "ends_with_newline": ends_with_newline,
            "appears_complete": bool(
                not read_error and not capped and json_failed == 0 and long_lines == 0 and ends_with_newline
            ),
        },
        "observed": observed,
        "source_stable_during_read": stable,
    }
    if result["category"] == "subagent_transcript":
        result["sidecar"] = scan_subagent_sidecar(path.with_suffix(".meta.json"))
        tool_use_id = result["sidecar"].get("tool_use_id")
        if (
            tool_use_id
            and result["sidecar"].get("parse_status") == "ok"
            and result["sidecar"].get("source_stable_during_read") is True
        ):
            _append_observed(
                result["observed"],
                "parent_tool_use_ids",
                tool_use_id,
                limit=MAX_TOOL_IDENTIFIERS,
                max_string_length=512,
            )
            result["observed"]["sidecar_lineage_accepted"] = True
        else:
            result["observed"]["sidecar_lineage_accepted"] = False
    result["observed"].pop("_seen_fields", None)
    return result


def scan_subagent_sidecar(path: Path) -> dict[str, Any]:
    result: dict[str, Any] = {
        "source_path": str(path),
        "present": False,
        "parse_status": "missing",
        "sha256": None,
        "size": None,
        "mtime_ns": None,
        "source_stable_during_read": None,
        "agent_type_present": False,
        "description_present": False,
        "tool_use_id": None,
        "spawn_depth": None,
        "sensitive_fields_present": False,
    }
    if not is_regular_file(path):
        return result
    result["present"] = True
    before = stat_snapshot(path)
    result["size"] = before["size"]
    result["mtime_ns"] = before["mtime_ns"]
    if before["size"] > MAX_SIDECAR_BYTES:
        result["parse_status"] = "size_limit"
        result["source_stable_during_read"] = stat_snapshot(path) == before
        return result
    try:
        payload = path.read_bytes()
        value = json.loads(payload.decode("utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, RecursionError):
        result["parse_status"] = "invalid_json"
        return result
    if not isinstance(value, dict):
        result["parse_status"] = "invalid_schema"
        return result
    tool_use_raw = value.get("toolUseId")
    tool_use_invalid = tool_use_raw is not None and (
        not isinstance(tool_use_raw, str) or len(tool_use_raw) > 512
    )
    result.update(
        {
            "parse_status": "invalid_schema" if tool_use_invalid else "ok",
            "sha256": hashlib.sha256(payload).hexdigest(),
            "agent_type_present": isinstance(value.get("agentType"), str),
            "description_present": bool(value.get("description")),
            "tool_use_id": value.get("toolUseId")
            if isinstance(value.get("toolUseId"), str) and len(value.get("toolUseId")) <= 512
            else None,
            "spawn_depth": value.get("spawnDepth") if isinstance(value.get("spawnDepth"), int) else None,
            "sensitive_fields_present": sensitive_keys_present(value),
            "tool_use_id_invalid": tool_use_invalid,
        }
    )
    try:
        result["source_stable_during_read"] = stat_snapshot(path) == before
    except OSError:
        result["source_stable_during_read"] = False
    return result


def scan_jsonl_roots(
    root_specs: list[tuple[Path, str]],
    diagnostics: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    scanned: list[dict[str, Any]] = []
    seen: set[Path] = set()
    stats = diagnostics if diagnostics is not None else {}
    stats.update(
        {
            "source_files_seen": 0,
            "source_files_scanned": 0,
            "source_files_skipped_budget": 0,
            "source_bytes_budgeted": 0,
            "skipped_source_paths": [],
            "skipped_paths_capped": 0,
        }
    )
    for root, source_kind in root_specs:
        for path in iter_regular_files(root, (".jsonl",)):
            resolved = path.resolve(strict=False)
            if resolved in seen or not is_regular_file(path):
                continue
            seen.add(resolved)
            stats["source_files_seen"] += 1
            try:
                planned = min(path.stat(follow_symlinks=False).st_size, MAX_JSONL_BYTES)
                sidecar_path = path.with_suffix(".meta.json")
                if is_regular_file(sidecar_path):
                    planned += min(sidecar_path.stat(follow_symlinks=False).st_size, MAX_SIDECAR_BYTES)
            except OSError:
                stats["source_files_skipped_budget"] += 1
                if len(stats["skipped_source_paths"]) < 1000:
                    stats["skipped_source_paths"].append(str(path))
                else:
                    stats["skipped_paths_capped"] += 1
                continue
            if (
                stats["source_files_scanned"] >= MAX_JSONL_SOURCE_FILES
                or stats["source_bytes_budgeted"] + planned > MAX_JSONL_TOTAL_BUDGET_BYTES
            ):
                stats["source_files_skipped_budget"] += 1
                if len(stats["skipped_source_paths"]) < 1000:
                    stats["skipped_source_paths"].append(str(path))
                else:
                    stats["skipped_paths_capped"] += 1
                continue
            stats["source_files_scanned"] += 1
            stats["source_bytes_budgeted"] += planned
            scanned.append(scan_jsonl_file(path, root, source_kind))
    return sorted(scanned, key=lambda item: str(item["source_path"]))
