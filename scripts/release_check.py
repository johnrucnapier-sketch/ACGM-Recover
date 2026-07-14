#!/usr/bin/env python3
"""Fail-closed release contract checks for ACGM Recover."""

from __future__ import annotations

import argparse
import json
import os
import re
import stat
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from acgm_recover.constants import ROUTES, SCHEMA_VERSION, TOOL_VERSION  # noqa: E402

REQUIRED = {
    ".gitattributes",
    "AGENTS.md",
    "README.md",
    "README.en.md",
    "SECURITY.md",
    "CHANGELOG.md",
    "CONTRIBUTING.md",
    "RELEASING.md",
    "LICENSE-CODE",
    "LICENSE-DOCS",
    "LICENSING.md",
    "VERSION",
    "SCHEMA_VERSION",
    "PACKAGE_MANIFEST.json",
    "pyproject.toml",
    "bin/acgm-recover",
    "scripts/bootstrap.py",
    "src/acgm_recover/__main__.py",
    "src/acgm_recover/cli.py",
    "src/acgm_recover/onboarding.py",
    "src/acgm_recover/analysis.py",
    "src/acgm_recover/bundle.py",
    "src/acgm_recover/gitfacts.py",
    "src/acgm_recover/sanitize.py",
    "src/acgm_recover/scan.py",
    "src/acgm_recover/util.py",
    "src/acgm_recover/verify.py",
    "docs/RECOVERY_MODEL.md",
    "docs/CLI_REFERENCE.md",
    "docs/INSTALLATION.md",
    ".github/workflows/ci.yml",
}


def check_required(errors: list[str], passed: list[str]) -> None:
    missing = sorted(path for path in REQUIRED if not (ROOT / path).is_file())
    if missing:
        errors.append("required_files_missing")
    else:
        passed.append("required_files")


def check_versions(errors: list[str], passed: list[str]) -> None:
    version = (ROOT / "VERSION").read_text(encoding="utf-8").strip()
    schema = (ROOT / "SCHEMA_VERSION").read_text(encoding="utf-8").strip()
    pyproject_text = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    project_section = pyproject_text.split("[project]", 1)[-1].split("[", 1)[0]
    match = re.search(r'^version\s*=\s*"([^"]+)"\s*$', project_section, re.MULTILINE)
    package_version = match.group(1).replace("rc", "-rc.") if match else ""
    if version != TOOL_VERSION or package_version != version or schema != SCHEMA_VERSION:
        errors.append("version_mismatch")
    else:
        passed.append("version_contract")


def check_executables(errors: list[str], passed: list[str]) -> None:
    paths = [ROOT / "bin/acgm-recover", *sorted((ROOT / "scripts").glob("*.py"))]
    if any(not (path.stat().st_mode & stat.S_IXUSR) for path in paths):
        errors.append("executable_mode_invalid")
    else:
        passed.append("executable_modes")


def check_privacy_contract(errors: list[str], passed: list[str]) -> None:
    scanned_paths = [*sorted((ROOT / "src").rglob("*.py")), ROOT / "scripts/bootstrap.py"]
    source = "\n".join(path.read_text(encoding="utf-8") for path in scanned_paths)
    forbidden = ["requests.", "urllib.request", "http.client", "socket.socket", "include_redacted_transcript"]
    if any(value in source for value in forbidden):
        errors.append("offline_or_text_boundary_invalid")
        return
    if "model_identity_assessment" not in source or "not_performed" not in source:
        errors.append("model_identity_boundary_missing")
        return
    if set(ROUTES) != {"claude-compatible-api", "claude-new-account", "agent-neutral"}:
        errors.append("route_contract_invalid")
        return
    passed.append("privacy_and_route_contract")


def check_no_real_paths(errors: list[str], passed: list[str]) -> None:
    violations: list[str] = []
    for path in [*ROOT.glob("*.md"), *ROOT.glob("*.en.md"), *(ROOT / "docs").glob("*.md")]:
        text = path.read_text(encoding="utf-8")
        if re.search(r"/Users/[^/\s]+/", text):
            violations.append(path.name)
    if violations:
        errors.append("private_absolute_path_in_docs")
    else:
        passed.append("path_hygiene")


def check_documented_contract(errors: list[str], passed: list[str]) -> None:
    readmes = [
        (ROOT / "README.md").read_text(encoding="utf-8"),
        (ROOT / "README.en.md").read_text(encoding="utf-8"),
    ]
    required_terms = (
        "STRUCTURAL_ONLY",
        "REVIEW_REQUIRED",
        "HANDOFF_READY",
        "human_reviewed",
        "share_approved",
        "recommended_project_roots",
    )
    security = (ROOT / "SECURITY.md").read_text(encoding="utf-8")
    if any(any(term not in text for term in required_terms) for text in readmes):
        errors.append("readiness_contract_not_documented")
    elif not all(term in security for term in ("alternate object", "Unicode", "ACL", "no-replace")):
        errors.append("security_contract_not_documented")
    else:
        passed.append("documented_contract")


def check_ci(errors: list[str], passed: list[str]) -> None:
    text = (ROOT / ".github/workflows/ci.yml").read_text(encoding="utf-8")
    if (
        "macos-latest" not in text
        or "ubuntu-latest" not in text
        or "windows-latest" not in text
        or "3.10" not in text
        or "test_onboarding.py" not in text
        or "test_bootstrap.py" not in text
    ):
        errors.append("ci_matrix_incomplete")
    else:
        passed.append("ci_matrix")


def check_onboarding_contract(errors: list[str], passed: list[str]) -> None:
    bootstrap = (ROOT / "scripts/bootstrap.py").read_text(encoding="utf-8")
    onboarding = (ROOT / "src/acgm_recover/onboarding.py").read_text(encoding="utf-8")
    cli = (ROOT / "src/acgm_recover/cli.py").read_text(encoding="utf-8")
    installation = (ROOT / "docs/INSTALLATION.md").read_text(encoding="utf-8")
    security = (ROOT / "SECURITY.md").read_text(encoding="utf-8")
    agents = (ROOT / "AGENTS.md").read_text(encoding="utf-8")
    required_bootstrap = (
        "--no-deps",
        "--no-build-isolation",
        "--no-index",
        "same_version_reinstall",
        "--force-reinstall",
        "VERIFIED_LOCAL_WHEEL",
        "stdlib_wheel_plus_pip",
        "upgrade_confirmation_required",
        "downgrade_refused",
        "shell=False",
    )
    required_onboarding = (
        "explicit_cli_argument",
        "selection_required",
        "recovery_runtime_supported",
        "model_identity_assessment",
        "not_performed",
        "route_requires_explicit_user_confirmation",
        "agent_self_confirmation_allowed",
    )
    if (
        any(term not in bootstrap for term in required_bootstrap)
        or any(term not in onboarding for term in required_onboarding)
        or "recovery_runtime_not_supported_on_platform" not in cli
        or "Windows boundary" not in installation
        or "force-reinstall" not in security
        or "force-reinstalled" not in agents
    ):
        errors.append("onboarding_contract_invalid")
    else:
        passed.append("onboarding_contract")


def check_manifest(errors: list[str], passed: list[str]) -> None:
    from generate_package_manifest import manifest

    try:
        current = json.loads((ROOT / "PACKAGE_MANIFEST.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        current = None
    if current != manifest():
        errors.append("package_manifest_stale")
    else:
        passed.append("package_manifest")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    errors: list[str] = []
    passed: list[str] = []
    check_required(errors, passed)
    if not errors:
        check_versions(errors, passed)
        check_executables(errors, passed)
        check_privacy_contract(errors, passed)
        check_no_real_paths(errors, passed)
        check_documented_contract(errors, passed)
        check_ci(errors, passed)
        check_onboarding_contract(errors, passed)
        check_manifest(errors, passed)
    result = {"ok": not errors, "errors": sorted(errors), "passed": sorted(passed)}
    if args.json:
        print(json.dumps(result, sort_keys=True))
    else:
        print("ok" if result["ok"] else "failed: " + ",".join(result["errors"]))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
