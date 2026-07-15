"""Command-line entrypoint for offline project recovery."""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path
from typing import Any

from .analysis import analyze_project, discover_candidates
from .bundle import build_bundle
from .constants import (
    ROUTES,
    TOOL_VERSION,
    default_auxiliary_roots,
    default_claude_projects_roots,
    default_metadata_roots,
)
from .onboarding import environment_guide, recovery_runtime_supported
from .util import RecoverError, pretty_json, unique_existing_dirs
from .verify import verify_bundle


def _path_list(values: list[str] | None, defaults: list[Path], use_defaults: bool) -> list[Path]:
    explicit = [Path(value).expanduser() for value in (values or [])]
    if any(path.is_symlink() or not path.is_dir() for path in explicit):
        raise RecoverError("source_root_invalid")
    paths = explicit
    if use_defaults:
        paths = [*defaults, *paths]
    return unique_existing_dirs(paths)


def _sources(args: argparse.Namespace) -> tuple[list[Path], list[Path], list[Path]]:
    use_defaults = not args.no_default_sources
    transcript_roots = _path_list(
        args.claude_projects_root,
        default_claude_projects_roots(),
        use_defaults,
    )
    metadata_roots = _path_list(args.metadata_root, default_metadata_roots(), use_defaults)
    auxiliary_roots = _path_list(args.auxiliary_root, default_auxiliary_roots(), use_defaults)
    return transcript_roots, metadata_roots, auxiliary_roots


def _add_source_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--claude-projects-root",
        action="append",
        help="Observed Claude projects storage root; may be repeated.",
    )
    parser.add_argument(
        "--metadata-root",
        action="append",
        help="Observed Claude/Claude-3p session metadata root; may be repeated.",
    )
    parser.add_argument(
        "--auxiliary-root",
        action="append",
        help="Optional auxiliary JSONL root (for example homunculus observations).",
    )
    parser.add_argument(
        "--no-default-sources",
        action="store_true",
        help="Use only source roots explicitly supplied on this command.",
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="claude-code-recover",
        description="Offline, evidence-first Claude Code project recovery.",
    )
    parser.add_argument("--version", action="version", version=f"Claude Code Recover {TOOL_VERSION}")
    subparsers = parser.add_subparsers(dest="command", required=True)

    doctor = subparsers.add_parser("doctor", help="Check local prerequisites without changing state.")
    _add_source_arguments(doctor)

    guide = subparsers.add_parser(
        "guide",
        help="Observe local capabilities and require an explicit continuation-route choice.",
    )
    _add_source_arguments(guide)
    guide.add_argument(
        "--route",
        choices=ROUTES,
        help="Record the route explicitly selected by the user; never inferred automatically.",
    )

    discover = subparsers.add_parser("discover", help="Discover surviving project-family candidates.")
    _add_source_arguments(discover)
    discover.add_argument(
        "--registry",
        type=Path,
        default=Path.home() / ".claude.json",
        help="Optional Claude registry file; only the projects keys are read.",
    )

    inspect_parser = subparsers.add_parser("inspect", help="Inspect one project family without writing a bundle.")
    _add_source_arguments(inspect_parser)
    inspect_parser.add_argument("--project", type=Path, required=True)
    inspect_parser.add_argument("--annotations", type=Path)

    build = subparsers.add_parser("build", help="Build a new recovery bundle atomically.")
    _add_source_arguments(build)
    build.add_argument("--project", type=Path, required=True)
    build.add_argument("--output", type=Path, required=True)
    build.add_argument("--annotations", type=Path)
    build.add_argument(
        "--route",
        action="append",
        choices=ROUTES,
        help="Generate a route template; may be repeated. All three are generated when omitted.",
    )

    verify = subparsers.add_parser("verify", help="Verify bundle integrity and optional source drift.")
    verify.add_argument("--bundle", type=Path, required=True)
    verify.add_argument("--check-sources", action="store_true")
    return parser


def _doctor(args: argparse.Namespace) -> dict[str, Any]:
    transcript_roots, metadata_roots, auxiliary_roots = _sources(args)
    runtime_supported = recovery_runtime_supported()
    warnings = [
        "observed_storage_layout_is_versioned_not_a_permanent_vendor_contract",
        "display_model_labels_are_not_model_identity",
    ]
    if not runtime_supported:
        warnings.append("secure_recovery_runtime_not_implemented_on_this_platform")
    return {
        "ok": sys.version_info >= (3, 10) and shutil.which("git") is not None and runtime_supported,
        "installation_ready": sys.version_info >= (3, 10),
        "recovery_runtime_supported": runtime_supported,
        "tool_version": TOOL_VERSION,
        "python": {
            "supported": sys.version_info >= (3, 10),
            "version": ".".join(str(part) for part in sys.version_info[:3]),
        },
        "git_available": shutil.which("git") is not None,
        "network_required": False,
        "source_mutation_intended": False,
        "default_or_selected_sources": {
            "claude_projects_roots": len(transcript_roots),
            "metadata_roots": len(metadata_roots),
            "auxiliary_roots": len(auxiliary_roots),
        },
        "warnings": warnings,
    }


def _analyze(args: argparse.Namespace) -> dict[str, Any]:
    transcript_roots, metadata_roots, auxiliary_roots = _sources(args)
    return analyze_project(
        args.project,
        claude_projects_roots=transcript_roots,
        metadata_roots=metadata_roots,
        auxiliary_roots=auxiliary_roots,
        annotations_path=args.annotations,
    )


def _safe_inspection(analysis: dict[str, Any]) -> dict[str, Any]:
    return {
        "schema_version": "1.0",
        "observed_at": analysis["observed_at"],
        "project": analysis["project"],
        "git": analysis["git"],
        "worktrees": analysis["worktrees"],
        "summary": analysis["summary"],
        "conflicts": analysis["conflicts"],
        "gaps": analysis["gaps"],
        "privacy": {
            "transcript_text_copied": False,
            "model_identity_assessment": "not_performed",
            "source_paths_emitted": False,
        },
    }


def run(args: argparse.Namespace) -> tuple[dict[str, Any], int]:
    if args.command in {"discover", "inspect", "build", "verify"} and not recovery_runtime_supported():
        raise RecoverError("recovery_runtime_not_supported_on_platform")
    if args.command == "doctor":
        result = _doctor(args)
        return result, 0 if result["ok"] else 1
    if args.command == "guide":
        result = environment_guide(args.route, source_roots=_sources(args))
        return result, 0 if result["ok"] else 1
    if args.command == "discover":
        transcript_roots, metadata_roots, auxiliary_roots = _sources(args)
        result = discover_candidates(
            claude_projects_roots=transcript_roots,
            metadata_roots=metadata_roots,
            auxiliary_roots=auxiliary_roots,
            registry_path=args.registry.expanduser() if args.registry else None,
        )
        return result, 0
    if args.command == "inspect":
        return _safe_inspection(_analyze(args)), 0
    if args.command == "build":
        analysis = _analyze(args)
        output = build_bundle(analysis, args.output, routes=args.route or ROUTES)
        verification = verify_bundle(output)
        result = {
            "ok": verification["ok"],
            "output": str(output),
            "summary": analysis["summary"],
            "verification": verification,
            "transcript_text_copied": False,
            "model_identity_assessment": "not_performed",
        }
        return result, 0 if verification["ok"] else 1
    if args.command == "verify":
        result = verify_bundle(args.bundle, check_sources=args.check_sources)
        return result, 0 if result["ok"] else 1
    raise RecoverError("command_unknown")


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        result, code = run(args)
    except RecoverError as exc:
        result = {"ok": False, "error": str(exc)}
        code = 2
    except OSError:
        result = {"ok": False, "error": "filesystem_error"}
        code = 2
    except KeyboardInterrupt:
        result = {"ok": False, "error": "interrupted"}
        code = 130
    print(pretty_json(result), end="")
    return code


if __name__ == "__main__":
    raise SystemExit(main())
