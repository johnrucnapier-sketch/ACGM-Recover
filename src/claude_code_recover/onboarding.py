"""Consent-preserving environment guidance for Claude Code Recover."""

from __future__ import annotations

import platform
import shutil
import sys
from pathlib import Path
from typing import Any

from .constants import (
    ROUTES,
    TOOL_VERSION,
    default_auxiliary_roots,
    default_claude_projects_roots,
    default_metadata_roots,
)


ROUTE_GUIDANCE: dict[str, dict[str, object]] = {
    "claude-compatible-api": {
        "purpose": "Continue in a Claude Code-compatible runtime chosen by the user.",
        "capabilities_to_verify": [
            "cli_and_tool_protocol",
            "hooks_and_plugin_loading",
            "session_storage",
            "context_compaction",
        ],
        "identity_inference_allowed": False,
    },
    "claude-new-account": {
        "purpose": "Continue in Claude Code with a new account through an evidence handoff.",
        "capabilities_to_verify": [
            "claude_cli_available",
            "hooks_and_plugin_loading",
            "fresh_session_access",
        ],
        "account_data_transfer_allowed": False,
    },
    "agent-neutral": {
        "purpose": "Move to Codex, Grok, or another user-selected agent platform.",
        "capabilities_to_verify": [
            "target_agent_available",
            "target_governance_contract",
            "current_repository_access",
        ],
        "automatic_rule_translation_allowed": False,
    },
}


def _presence(paths: list[Path]) -> dict[str, int]:
    """Report only aggregate directory presence; never emit a local path."""

    return {
        "configured_locations": len(paths),
        "visible_directories": sum(path.expanduser().is_dir() and not path.is_symlink() for path in paths),
    }


def recovery_runtime_supported() -> bool:
    """Return whether the RC's secure bundle runtime is implemented here."""

    return sys.platform == "darwin" or sys.platform.startswith("linux")


def environment_guide(
    route: str | None = None,
    *,
    source_roots: tuple[list[Path], list[Path], list[Path]] | None = None,
) -> dict[str, Any]:
    """Return an offline plan without scanning evidence or selecting a route."""

    if route is not None and route not in ROUTES:
        raise ValueError("route_invalid")

    python_supported = sys.version_info >= (3, 10)
    git_visible = shutil.which("git") is not None
    selected = ROUTE_GUIDANCE[route] if route else None
    selection_status = "explicit_cli_argument" if route else "selection_required"
    runtime_supported = recovery_runtime_supported()
    if source_roots is None:
        source_roots = (
            [path for path in default_claude_projects_roots() if path.is_dir() and not path.is_symlink()],
            [path for path in default_metadata_roots() if path.is_dir() and not path.is_symlink()],
            [path for path in default_auxiliary_roots() if path.is_dir() and not path.is_symlink()],
        )
    transcript_roots, metadata_roots, auxiliary_roots = source_roots
    next_commands = [
        ["PYTHON", "-m", "claude_code_recover", "doctor", "--no-default-sources"],
    ]
    future_commands: list[list[str]] = []
    if route and runtime_supported:
        next_commands.append(["PYTHON", "-m", "claude_code_recover", "discover", "SOURCE_OPTIONS"])
        future_commands.extend(
            [
                [
                    "PYTHON", "-m", "claude_code_recover", "inspect", "--project", "PROJECT",
                    "SOURCE_OPTIONS",
                ],
                [
                    "PYTHON", "-m", "claude_code_recover", "build", "--project", "PROJECT",
                    "--output", "NEW_BUNDLE", "--route", route, "SOURCE_OPTIONS",
                ],
            ]
        )
    elif route is None:
        next_commands.append(["PYTHON", "-m", "claude_code_recover", "guide", "--route", "ROUTE"])

    return {
        "ok": python_supported and git_visible and runtime_supported,
        "installation_ready": python_supported,
        "scan_ready": python_supported and git_visible and runtime_supported,
        "build_ready": False,
        "recovery_runtime_supported": runtime_supported,
        "tool_version": TOOL_VERSION,
        "environment": {
            "operating_system": {
                "family": platform.system() or "unknown",
                "release": platform.release() or "unknown",
                "architecture": platform.machine() or "unknown",
            },
            "python": {
                "supported": python_supported,
                "version": ".".join(str(part) for part in sys.version_info[:3]),
            },
            "visible_commands": {
                "git": git_visible,
                "claude": shutil.which("claude") is not None,
                "codex": shutil.which("codex") is not None,
            },
            "default_source_location_presence": {
                "claude_projects": _presence(transcript_roots),
                "session_metadata": _presence(metadata_roots),
                "auxiliary": _presence(auxiliary_roots),
            },
        },
        "route_selection": {
            "status": selection_status,
            "selection_required": route is None,
            "selected_route": route,
            "available_routes": [
                {"route": candidate, **ROUTE_GUIDANCE[candidate]} for candidate in ROUTES
            ],
            "selected_route_guidance": selected,
            "automatic_selection_performed": False,
            "user_confirmation_still_required": True,
        },
        "privacy": {
            "network_used": False,
            "evidence_scan_performed": False,
            "account_inspection_performed": False,
            "credential_inspection_performed": False,
            "model_identity_assessment": "not_performed",
            "local_paths_emitted": False,
        },
        "authorization": {
            "installation_authorizes_evidence_discovery": False,
            "next_scan_requires_explicit_user_action": True,
            "route_requires_explicit_user_confirmation": True,
            "agent_self_confirmation_allowed": False,
            "project_confirmation_required_before_inspect_or_build": True,
            "reuse_reviewed_source_options_for_scan": True,
        },
        "command_template_contract": {
            "commands_are_templates_not_authorized_actions": True,
            "agent_must_resolve_placeholders_after_user_confirmation": True,
            "placeholders": {
                "PYTHON": "Selected Python 3.10+ interpreter.",
                "ROUTE": "One route explicitly confirmed by the user.",
                "SOURCE_OPTIONS": "Reviewed source-root arguments; expand to zero or more argv elements.",
                "PROJECT": "One discovered project root explicitly confirmed by the user.",
                "NEW_BUNDLE": "A new output path explicitly approved by the user.",
            },
        },
        "next_commands_argv": next_commands,
        "future_commands_after_confirmation_argv": future_commands,
    }
