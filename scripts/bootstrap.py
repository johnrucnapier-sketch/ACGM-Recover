#!/usr/bin/env python3
"""Offline, user-scoped installer for an explicitly downloaded Claude Code Recover tree."""

from __future__ import annotations

import argparse
import base64
import csv
import hashlib
import io
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import sysconfig
import tempfile
import zipfile
from pathlib import Path, PurePosixPath
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
PACKAGE = "claude-code-recover"
LEGACY_PACKAGE = "acgm-recover"
MINIMUM_PYTHON = (3, 10)
EXCLUDED_SOURCE_PARTS = {".git", "__pycache__", ".pytest_cache", "build", "dist"}
EXCLUDED_SOURCE_NAMES = {".DS_Store", "PACKAGE_MANIFEST.json"}


def _display_command(arguments: list[str]) -> list[str]:
    return ["PYTHON" if index == 0 else value for index, value in enumerate(arguments)]


def _installed_distribution_versions() -> dict[str, str] | None:
    # Query metadata in a clean child process outside the checkout.  Calling
    # importlib.metadata here would let an inherited PYTHONPATH expose a local
    # *.egg-info directory and misreport the source tree as installed.
    command = [
        sys.executable,
        "-c",
        "import importlib.metadata as m; "
        "import json; "
        "norm=lambda s:str(s or '').lower().replace('_','-').replace('.','-'); "
        "wanted={'claude-code-recover','acgm-recover'}; "
        "found={norm(d.metadata.get('Name','')):d.version for d in m.distributions() "
        "if norm(d.metadata.get('Name','')) in wanted}; "
        "print(json.dumps(found, sort_keys=True))",
    ]
    with tempfile.TemporaryDirectory(prefix="claude-code-recover-metadata-") as temporary:
        process = _run(command, cwd=Path(temporary))
    if process.returncode != 0:
        return None
    try:
        value = json.loads(process.stdout)
    except json.JSONDecodeError:
        return None
    if not isinstance(value, dict):
        return None
    result: dict[str, str] = {}
    for name, version in value.items():
        if (
            name not in {PACKAGE, LEGACY_PACKAGE}
            or not isinstance(version, str)
            or not version.strip()
        ):
            return None
        result[name] = version
    return result


def _installed_version() -> str | None:
    versions = _installed_distribution_versions()
    if versions is None:
        return None
    return versions.get(PACKAGE) or versions.get(LEGACY_PACKAGE)


def _source_version() -> str:
    return (ROOT / "VERSION").read_text(encoding="utf-8").strip()


def _release_key(value: str) -> tuple[int, int, int, int, int] | None:
    """Parse the project's controlled stable/RC version forms without dependencies."""

    match = re.fullmatch(r"(\d+)\.(\d+)\.(\d+)(?:(?:-|\.)?rc(?:\.|-)?(\d+))?", value, re.IGNORECASE)
    if not match:
        return None
    major, minor, patch, rc = match.groups()
    return (int(major), int(minor), int(patch), 0 if rc is not None else 1, int(rc or 0))


def _version_policy(installed: str | None, source: str, upgrade: bool) -> tuple[str, bool]:
    if installed is None:
        return "fresh_install", True
    installed_key = _release_key(installed)
    source_key = _release_key(source)
    if installed_key is None or source_key is None:
        return "version_comparison_unavailable", False
    if installed_key == source_key:
        # A development preview can publish corrected source without changing
        # its version.  Always replace an equal-version installation from the
        # source tree whose manifest was just verified; never execute an
        # unrelated pre-existing package merely because its metadata matches.
        return "same_version_reinstall", True
    if installed_key > source_key:
        return "downgrade_refused", False
    if not upgrade:
        return "upgrade_confirmation_required", False
    return "explicit_upgrade", True


def _safe_text(value: str) -> str:
    home = str(Path.home())
    return value.replace(str(ROOT), "<repository>").replace(home, "~")


def _run(arguments: list[str], *, cwd: Path = ROOT) -> subprocess.CompletedProcess[str]:
    # No inherited pip setting may change the target, scope, index, isolation,
    # or override behavior represented by the reviewed argv.
    environment = {
        key: value
        for key, value in os.environ.items()
        if not key.upper().startswith("PIP_")
    }
    environment.update(
        {
            "PIP_DISABLE_PIP_VERSION_CHECK": "1",
            "PIP_NO_INDEX": "1",
            "PIP_CONFIG_FILE": os.devnull,
        }
    )
    # Neither installation nor post-install verification may import the
    # checkout through caller-provided Python path configuration.
    environment.pop("PYTHONPATH", None)
    environment.pop("PYTHONHOME", None)
    environment.pop("PYTHONUSERBASE", None)
    environment.pop("PYTHONNOUSERSITE", None)
    return subprocess.run(
        arguments,
        cwd=cwd,
        env=environment,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        timeout=180,
        check=False,
        shell=False,
    )


def _verified_manifest_payloads() -> tuple[dict[str, bytes] | None, str | None]:
    """Return one manifest-verified byte snapshot for downstream use."""

    manifest_path = ROOT / "PACKAGE_MANIFEST.json"
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        expected_version = (ROOT / "VERSION").read_text(encoding="utf-8").strip()
    except (OSError, json.JSONDecodeError):
        return None, "source_manifest_unreadable"
    if (
        manifest.get("package") != PACKAGE
        or manifest.get("version") != expected_version
        or manifest.get("file_count") != len(manifest.get("files", []))
    ):
        return None, "source_manifest_contract_invalid"
    seen: set[str] = set()
    verified: dict[str, bytes] = {}
    for row in manifest.get("files", []):
        if not isinstance(row, dict):
            return None, "source_manifest_contract_invalid"
        relative = row.get("path")
        if not isinstance(relative, str) or "\\" in relative or relative in seen:
            return None, "source_manifest_path_invalid"
        pure = PurePosixPath(relative)
        if pure.is_absolute() or not pure.parts or any(part in {"", ".", ".."} for part in pure.parts):
            return None, "source_manifest_path_invalid"
        seen.add(relative)
        path = ROOT.joinpath(*pure.parts)
        if path.is_symlink() or not path.is_file():
            return None, "source_manifest_file_missing"
        try:
            payload = path.read_bytes()
        except OSError:
            return None, "source_manifest_file_unreadable"
        if row.get("size") != len(payload) or row.get("sha256") != hashlib.sha256(payload).hexdigest():
            return None, "source_manifest_mismatch"
        verified[relative] = payload
    actual: set[str] = set()
    for path in ROOT.rglob("*"):
        relative_path = path.relative_to(ROOT)
        if any(
            part in EXCLUDED_SOURCE_PARTS or part.endswith(".egg-info")
            for part in relative_path.parts
        ):
            continue
        if path.name in EXCLUDED_SOURCE_NAMES or path.suffix in {".pyc", ".pyo"}:
            continue
        if path.is_symlink():
            return None, "source_manifest_symlink_not_allowed"
        if path.is_file():
            actual.add(relative_path.as_posix())
    if actual != seen:
        return None, "source_manifest_file_set_mismatch"
    return verified, None


def _manifest_check() -> tuple[bool, str | None]:
    verified, error = _verified_manifest_payloads()
    return verified is not None, error


def _wheel_version(source_version: str) -> str:
    """Convert the controlled release form to its PEP 440 wheel spelling."""

    match = re.fullmatch(r"(\d+\.\d+\.\d+)(?:-rc\.(\d+))?", source_version)
    if not match:
        raise ValueError("source_version_not_wheel_compatible")
    stable, rc = match.groups()
    return stable if rc is None else f"{stable}rc{rc}"


def _build_offline_wheel(directory: Path, source_version: str) -> Path:
    """Build this pure-Python package with stdlib only after manifest verification."""

    verified, error = _verified_manifest_payloads()
    if verified is None:
        raise ValueError(error or "source_manifest_verification_failed")
    if verified.get("VERSION", b"").decode("utf-8", "strict").strip() != source_version:
        raise ValueError("source_version_changed_after_plan")
    wheel_version = _wheel_version(source_version)
    distribution = "claude_code_recover"
    dist_info = f"{distribution}-{wheel_version}.dist-info"
    members: dict[str, bytes] = {}
    for relative, payload in sorted(verified.items()):
        if relative.startswith(("src/claude_code_recover/", "src/acgm_recover/")):
            members[relative.removeprefix("src/")] = payload
    module_files = {
        "__init__.py",
        "__main__.py",
        "analysis.py",
        "bundle.py",
        "cli.py",
        "constants.py",
        "gitfacts.py",
        "onboarding.py",
        "sanitize.py",
        "scan.py",
        "util.py",
        "verify.py",
    }
    required_package_members = {
        f"{package}/{module}"
        for package in ("claude_code_recover", "acgm_recover")
        for module in module_files
    }
    if not required_package_members.issubset(members):
        raise ValueError("package_source_missing")

    members[f"{dist_info}/METADATA"] = (
        "Metadata-Version: 2.1\n"
        "Name: claude-code-recover\n"
        f"Version: {wheel_version}\n"
        "Summary: Independent Claude Code recovery tool; not affiliated with or endorsed by Anthropic\n"
        "Requires-Python: >=3.10\n"
        "License: MIT for code; CC-BY-4.0 for documentation. See LICENSING.md.\n"
        "\n"
    ).encode("utf-8")
    members[f"{dist_info}/WHEEL"] = (
        "Wheel-Version: 1.0\n"
        f"Generator: Claude Code Recover bootstrap {source_version}\n"
        "Root-Is-Purelib: true\n"
        "Tag: py3-none-any\n"
        "\n"
    ).encode("utf-8")
    members[f"{dist_info}/entry_points.txt"] = (
        "[console_scripts]\n"
        "claude-code-recover = claude_code_recover.cli:main\n"
        "acgm-recover = acgm_recover.cli:main\n"
    ).encode("utf-8")
    try:
        members[f"{dist_info}/licenses/LICENSE-CODE"] = verified["LICENSE-CODE"]
    except KeyError as error:
        raise ValueError("license_source_missing") from error

    record_path = f"{dist_info}/RECORD"
    record_buffer = io.StringIO(newline="")
    writer = csv.writer(record_buffer, lineterminator="\n")
    for name, payload in sorted(members.items()):
        digest = base64.urlsafe_b64encode(hashlib.sha256(payload).digest()).rstrip(b"=").decode("ascii")
        writer.writerow((name, f"sha256={digest}", str(len(payload))))
    writer.writerow((record_path, "", ""))
    members[record_path] = record_buffer.getvalue().encode("utf-8")

    wheel_path = directory / f"{distribution}-{wheel_version}-py3-none-any.whl"
    with zipfile.ZipFile(wheel_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, payload in sorted(members.items()):
            info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o644 << 16
            archive.writestr(info, payload)
    return wheel_path


def _prerequisites() -> dict[str, Any]:
    python_supported = sys.version_info >= MINIMUM_PYTHON
    git_available = shutil.which("git") is not None
    pip_check = _run([sys.executable, "-m", "pip", "--version"])
    manifest_ok, manifest_error = _manifest_check()
    return {
        "ok": (
            python_supported
            and git_available
            and pip_check.returncode == 0
            and manifest_ok
        ),
        "python_supported": python_supported,
        "python_version": ".".join(str(part) for part in sys.version_info[:3]),
        "pip_available": pip_check.returncode == 0,
        "git_available": git_available,
        "installer_backend": "stdlib_wheel_plus_pip",
        "setuptools_or_wheel_package_required": False,
        "source_manifest_ok": manifest_ok,
        "source_manifest_error": manifest_error,
        "network_used": False,
    }


def _externally_managed_marker_present() -> bool:
    """Apply the PEP 668 marker lookup without reading marker contents."""

    scheme = sysconfig.get_default_scheme()
    standard_library = sysconfig.get_path("stdlib", scheme)
    if not standard_library:
        raise OSError("stdlib_path_unavailable")
    marker = Path(standard_library) / "EXTERNALLY-MANAGED"
    try:
        marker_stat = marker.stat()
    except FileNotFoundError:
        return False
    # Any other OSError propagates to the fail-closed policy.  Treat any
    # unexpected object type as invalid rather than enabling a risky override.
    if not stat.S_ISREG(marker_stat.st_mode):
        raise OSError("externally_managed_marker_not_regular")
    return True


def _pip_install_break_system_packages_support() -> tuple[bool | None, str]:
    """Probe the selected pip's install options without mutating any state."""

    process = _run([sys.executable, "-m", "pip", "install", "--help"])
    if process.returncode != 0:
        return None, "capability_check_failed"
    output = f"{process.stdout}\n{process.stderr}"
    if re.search(r"(?m)^\s*--break-system-packages(?:\s|$)", output):
        return True, "supported"
    return False, "unsupported"


def _installation_environment(in_virtual_environment: bool) -> dict[str, Any]:
    """Build a fail-closed install policy for the selected interpreter."""

    base: dict[str, Any] = {
        "policy": "pep668_user_scope_v1",
        "in_virtual_environment": in_virtual_environment,
        "install_scope": (
            "virtual_environment" if in_virtual_environment else "current_user"
        ),
        "user_scope_flag_enabled": not in_virtual_environment,
        "externally_managed_marker_checked": not in_virtual_environment,
        "externally_managed": False,
        "pip_break_system_packages_check": "not_required",
        "pip_break_system_packages_support_checked": False,
        "pip_break_system_packages_supported": None,
        "pip_break_system_packages_enabled": False,
        "safe_install_plan_available": True,
        "network_used": False,
    }
    if in_virtual_environment:
        base["status"] = "virtual_environment"
        return base

    try:
        externally_managed = _externally_managed_marker_present()
    except (OSError, TypeError, ValueError):
        base.update(
            {
                "status": "externally_managed_marker_check_failed",
                "externally_managed": None,
                "safe_install_plan_available": False,
            }
        )
        return base
    base["externally_managed"] = externally_managed
    if not externally_managed:
        base["status"] = "ordinary_interpreter"
        return base

    supported, check_status = _pip_install_break_system_packages_support()
    base.update(
        {
            "pip_break_system_packages_check": check_status,
            "pip_break_system_packages_support_checked": True,
            "pip_break_system_packages_supported": supported,
            "pip_break_system_packages_enabled": supported is True,
            "safe_install_plan_available": supported is True,
            "status": (
                "externally_managed_user_override_supported"
                if supported is True
                else "externally_managed_user_override_unavailable"
            ),
        }
    )
    return base


def _rollback(
    previous_version: str | None,
    *,
    installation_environment: dict[str, Any],
) -> dict[str, Any]:
    current_distributions = _installed_distribution_versions()
    uninstall = [sys.executable, "-m", "pip", "uninstall", "-y", PACKAGE]
    externally_managed_user_install = bool(
        installation_environment.get("user_scope_flag_enabled") is True
        and installation_environment.get("pip_break_system_packages_enabled") is True
    )
    # pip uninstall does not support --user, so it cannot prove that removal is
    # limited to the user scheme.  Never turn the install override into a
    # broader automatic cleanup command.
    if externally_managed_user_install:
        current_version = None
        if current_distributions is not None:
            current_version = current_distributions.get(PACKAGE) or current_distributions.get(
                LEGACY_PACKAGE
            )
        return {
            "status": "externally_managed_no_automatic_cleanup",
            "previous_version": previous_version,
            "current_version": current_version,
            "installed_state_readable": current_distributions is not None,
            "automatic_cleanup_attempted": False,
            "pip_break_system_packages_enabled": False,
            "manual_command_argv": None,
            "guidance": (
                "The failed install used a PEP 668 override only with --user. pip uninstall "
                "has no equivalent --user scope, so bootstrap did not remove anything "
                "automatically. Inspect the selected interpreter's user installation and "
                "obtain separate authorization before cleanup."
            ),
        }
    if current_distributions is None:
        return {
            "status": "installed_state_unavailable_no_automatic_cleanup",
            "previous_version": previous_version,
            "current_version": None,
            "guidance": (
                "Installed distribution metadata could not be read reliably. "
                "No automatic uninstall was attempted; inspect the interpreter state first."
            ),
            "manual_command_argv": _display_command(uninstall),
            "automatic_cleanup_attempted": False,
            "pip_break_system_packages_enabled": False,
        }
    current_version = current_distributions.get(PACKAGE) or current_distributions.get(
        LEGACY_PACKAGE
    )
    if previous_version is None and current_version is not None:
        process = _run(uninstall)
        after = _installed_distribution_versions()
        return {
            "status": "automatic_cleanup_succeeded" if process.returncode == 0 else "automatic_cleanup_failed",
            "previous_version": None,
            "current_version": (
                after.get(PACKAGE) or after.get(LEGACY_PACKAGE)
                if after is not None
                else None
            ),
            "installed_state_readable": after is not None,
            "manual_command_argv": _display_command(uninstall),
            "automatic_cleanup_attempted": True,
            "pip_break_system_packages_enabled": False,
        }
    return {
        "status": "previous_installation_not_removed",
        "previous_version": previous_version,
        "current_version": current_version,
        "guidance": "Re-run bootstrap from the previously trusted source tree, or uninstall explicitly.",
        "manual_command_argv": _display_command(uninstall),
        "automatic_cleanup_attempted": False,
        "pip_break_system_packages_enabled": False,
    }


def _verification(route: str | None) -> tuple[dict[str, Any], bool]:
    commands = {
        "version": [sys.executable, "-m", "claude_code_recover", "--version"],
        "doctor": [
            sys.executable,
            "-m",
            "claude_code_recover",
            "doctor",
            "--no-default-sources",
        ],
        "guide": [sys.executable, "-m", "claude_code_recover", "guide", "--no-default-sources"],
        "legacy_module_alias": [sys.executable, "-m", "acgm_recover", "--version"],
    }
    if route:
        commands["guide"].extend(["--route", route])
    results: dict[str, Any] = {}
    version_ok = False
    guide_installation_ready = False
    origin_command = [
        sys.executable,
        "-c",
        "import claude_code_recover; from pathlib import Path; "
        "print(Path(claude_code_recover.__file__).resolve())",
    ]
    legacy_alias_ok = False
    with tempfile.TemporaryDirectory(prefix="claude-code-recover-verify-") as temporary:
        verification_cwd = Path(temporary)
        origin_process = _run(origin_command, cwd=verification_cwd)
        origin = Path(origin_process.stdout.strip()) if origin_process.returncode == 0 else None
        try:
            imported_from_checkout = bool(origin and origin.is_relative_to(ROOT.resolve()))
        except (OSError, ValueError):
            imported_from_checkout = False
        origin_ok = origin_process.returncode == 0 and not imported_from_checkout
        results["module_origin"] = {
            "command_argv": _display_command(origin_command),
            "exit_code": origin_process.returncode,
            "outside_source_checkout": origin_ok,
            "local_path_emitted": False,
        }
        for name, command in commands.items():
            process = _run(command, cwd=verification_cwd)
            row: dict[str, Any] = {
                "command_argv": _display_command(command),
                "exit_code": process.returncode,
            }
            if name == "version":
                value = process.stdout.strip()
                row["output"] = value
                version_ok = process.returncode == 0 and "Claude Code Recover" in value
            elif name == "legacy_module_alias":
                value = process.stdout.strip()
                row["output"] = value
                row["legacy_compatibility_alias"] = True
                legacy_alias_ok = process.returncode == 0 and "Claude Code Recover" in value
            else:
                try:
                    payload = json.loads(process.stdout)
                except json.JSONDecodeError:
                    payload = None
                row["result"] = payload
                if name == "guide" and isinstance(payload, dict):
                    guide_installation_ready = payload.get("installation_ready") is True
            results[name] = row
    return results, origin_ok and version_ok and guide_installation_ready and legacy_alias_ok


def install(*, dry_run: bool, route: str | None, upgrade: bool) -> tuple[dict[str, Any], int]:
    prerequisites = _prerequisites()
    in_virtual_environment = sys.prefix != getattr(sys, "base_prefix", sys.prefix) or hasattr(sys, "real_prefix")
    installed_distributions_before = _installed_distribution_versions()
    if installed_distributions_before is None:
        return {
            "tool": "Claude Code Recover bootstrap",
            "ok": False,
            "status": "installed_distribution_state_unavailable",
            "dry_run": dry_run,
            "prerequisites": prerequisites,
            "install_scope": "virtual_environment" if in_virtual_environment else "current_user",
            "install_command_executable": False,
            "source_version": _source_version(),
            "mutation_performed": False,
            "network_used": False,
            "evidence_scan_performed": False,
            "installation_authorizes_discovery": False,
            "guidance": (
                "The current interpreter's installed distribution metadata could not be "
                "read reliably. Resolve that state before installing or migrating."
            ),
        }, 2
    previous_version = installed_distributions_before.get(PACKAGE) or installed_distributions_before.get(
        LEGACY_PACKAGE
    )
    source_version = _source_version()
    version_action, version_allowed = _version_policy(previous_version, source_version, upgrade)
    base: dict[str, Any] = {
        "tool": "Claude Code Recover bootstrap",
        "dry_run": dry_run,
        "prerequisites": prerequisites,
        "install_scope": "virtual_environment" if in_virtual_environment else "current_user",
        "install_command_executable": False,
        "source_version": source_version,
        "installed_version_before": previous_version,
        "installed_distributions_before": installed_distributions_before,
        "legacy_alias_policy": "provided_by_rc3_for_transition_cycle",
        "version_action": version_action,
        "route_argument": route,
        "route_selected_automatically": False,
        "evidence_scan_performed": False,
        "network_used": False,
        "installation_authorizes_discovery": False,
        "installer_backend": "stdlib_wheel_plus_pip",
    }
    if not prerequisites["ok"]:
        base.update(
            {
                "ok": False,
                "status": "prerequisites_failed",
                "guidance": (
                    "Install Python 3.10+, Git, and pip separately; then obtain a clean "
                    "named source tree whose PACKAGE_MANIFEST.json matches."
                ),
            }
        )
        return base, 2
    if LEGACY_PACKAGE in installed_distributions_before:
        base.update(
            {
                "ok": False,
                "status": "MIGRATION_REQUIRED",
                "mutation_performed": False,
                "migration_plan": {
                    "executable": False,
                    "requires_separate_user_authorization": True,
                    "legacy_distribution": LEGACY_PACKAGE,
                    "legacy_version": installed_distributions_before[LEGACY_PACKAGE],
                    "steps": [
                        {
                            "action": "review_legacy_installation",
                            "authorized": False,
                        },
                        {
                            "action": "uninstall_legacy_distribution",
                            "authorized": False,
                            "command_argv_template": [
                                "PYTHON",
                                "-m",
                                "pip",
                                "uninstall",
                                LEGACY_PACKAGE,
                            ],
                        },
                        {
                            "action": "rerun_verified_rc3_bootstrap",
                            "authorized": False,
                        },
                    ],
                },
                "guidance": (
                    "RC3 will not mutate or uninstall the RC1 distribution automatically. "
                    "Obtain separate user authorization for the reviewed migration plan, "
                    "then rerun bootstrap from this verified source tree."
                ),
            }
        )
        return base, 2
    if not version_allowed:
        guidance = {
            "upgrade_confirmation_required": "Re-run with --upgrade after reviewing the newer source tree.",
            "downgrade_refused": "Use a newer trusted source tree; bootstrap does not perform downgrades.",
            "version_comparison_unavailable": "Use a source and installed package with a supported X.Y.Z or X.Y.Z-rc.N version.",
        }[version_action]
        base.update({"ok": False, "status": version_action, "guidance": guidance})
        return base, 2

    installation_environment = _installation_environment(in_virtual_environment)
    base["installation_environment"] = installation_environment
    if not installation_environment["safe_install_plan_available"]:
        marker_check_failed = (
            installation_environment.get("status")
            == "externally_managed_marker_check_failed"
        )
        base.update(
            {
                "ok": False,
                "status": (
                    "externally_managed_marker_check_failed"
                    if marker_check_failed
                    else "externally_managed_user_install_unavailable"
                ),
                "mutation_performed": False,
                "install_command_argv": None,
                "guidance": (
                    (
                        "Bootstrap could not safely determine whether the selected Python "
                        "has a valid EXTERNALLY-MANAGED marker. Inspect that interpreter or "
                        "activate a virtual environment, then rerun bootstrap. No "
                        "installation was attempted."
                    )
                    if marker_check_failed
                    else (
                        "The selected Python is externally managed, but bootstrap could not "
                        "verify a supported pip --break-system-packages install option. "
                        "Use a newer pip for this interpreter or activate a virtual environment, "
                        "then rerun bootstrap. No installation was attempted."
                    )
                ),
            }
        )
        return base, 2

    command = [
        sys.executable,
        "-m",
        "pip",
        "install",
        "--no-deps",
        "--no-build-isolation",
        "--no-index",
    ]
    if installation_environment["user_scope_flag_enabled"]:
        command.append("--user")
    if installation_environment["pip_break_system_packages_enabled"]:
        if "--user" not in command:
            base.update(
                {
                    "ok": False,
                    "status": "installation_environment_policy_invalid",
                    "mutation_performed": False,
                    "install_command_argv": None,
                    "guidance": (
                        "Bootstrap refused an inconsistent PEP 668 plan because the "
                        "override was not paired with current-user scope."
                    ),
                }
            )
            return base, 2
        command.append("--break-system-packages")
    if upgrade:
        command.append("--upgrade")
    if version_action == "same_version_reinstall":
        command.append("--force-reinstall")
    command.append("VERIFIED_LOCAL_WHEEL")
    base["install_command_argv"] = _display_command(command)
    base["install_command_executable"] = True
    if dry_run:
        base.update(
            {
                "ok": True,
                "status": "dry_run_complete_no_changes",
                "mutation_performed": False,
                "next_action": "Run the same command without --dry-run after reviewing this plan.",
            }
        )
        return base, 0

    try:
        with tempfile.TemporaryDirectory(prefix="claude-code-recover-wheel-") as temporary:
            wheel_path = _build_offline_wheel(Path(temporary), source_version)
            actual_command = [
                str(wheel_path) if value == "VERIFIED_LOCAL_WHEEL" else value
                for value in command
            ]
            process = _run(actual_command)
    except (OSError, ValueError, zipfile.BadZipFile) as error:
        base.update(
            {
                "ok": False,
                "status": "offline_wheel_build_failed",
                "error_code": type(error).__name__,
                "mutation_performed": False,
            }
        )
        return base, 3
    if process.returncode != 0:
        base.update(
            {
                "ok": False,
                "status": "pip_install_failed",
                "pip_exit_code": process.returncode,
                "error_tail": _safe_text("\n".join(process.stderr.splitlines()[-12:])),
                "rollback": _rollback(
                    previous_version,
                    installation_environment=installation_environment,
                ),
            }
        )
        return base, 3

    verification, verified = _verification(route)
    installed_distributions_after_result = _installed_distribution_versions()
    installed_distributions_after = installed_distributions_after_result or {}
    distribution_verified = (
        installed_distributions_after_result is not None
        and
        installed_distributions_after.get(PACKAGE) == _wheel_version(source_version)
        and LEGACY_PACKAGE not in installed_distributions_after
    )
    verification["distribution_metadata"] = {
        "canonical_distribution": PACKAGE,
        "canonical_version": installed_distributions_after.get(PACKAGE),
        "metadata_readable": installed_distributions_after_result is not None,
        "legacy_distribution_absent": LEGACY_PACKAGE not in installed_distributions_after,
        "verified": distribution_verified,
    }
    verified = verified and distribution_verified
    if not verified:
        base.update(
            {
                "ok": False,
                "status": "post_install_verification_failed",
                "verification": verification,
                "rollback": _rollback(
                    previous_version,
                    installation_environment=installation_environment,
                ),
            }
        )
        return base, 4

    guide_result = verification["guide"].get("result") or {}
    base.update(
        {
            "ok": True,
            "status": "installed_and_verified",
            "installed_version": installed_distributions_after[PACKAGE],
            "verification": verification,
            "recovery_runtime_supported": guide_result.get("recovery_runtime_supported", False),
            "route_argument_required": route is None,
            "route_confirmation_still_required": True,
            "installed_distributions_after": installed_distributions_after,
            "next_commands_argv": guide_result.get("next_commands_argv", []),
            "future_commands_after_confirmation_argv": guide_result.get(
                "future_commands_after_confirmation_argv", []
            ),
        }
    )
    return base, 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Install Claude Code Recover locally without scanning evidence.")
    parser.add_argument("--dry-run", action="store_true", help="Validate and print the plan without installing.")
    parser.add_argument("--upgrade", action="store_true", help="Request an upgrade from this reviewed source tree.")
    parser.add_argument(
        "--route",
        choices=("claude-compatible-api", "claude-new-account", "agent-neutral"),
        help="Pass through an explicit route argument for post-install guidance only.",
    )
    parser.add_argument("--json", action="store_true", help="Emit machine-readable JSON (the default format).")
    args = parser.parse_args(argv)
    try:
        result, code = install(dry_run=args.dry_run, route=args.route, upgrade=args.upgrade)
    except (OSError, subprocess.SubprocessError):
        result, code = {
            "ok": False,
            "status": "bootstrap_runtime_error",
            "network_used": False,
            "evidence_scan_performed": False,
        }, 5
    print(json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2) + "\n", end="")
    return code


if __name__ == "__main__":
    raise SystemExit(main())
