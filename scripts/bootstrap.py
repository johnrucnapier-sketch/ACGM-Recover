#!/usr/bin/env python3
"""Offline, user-scoped installer for an explicitly downloaded ACGM Recover tree."""

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
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path, PurePosixPath
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
PACKAGE = "acgm-recover"
MINIMUM_PYTHON = (3, 10)
EXCLUDED_SOURCE_PARTS = {".git", "__pycache__", ".pytest_cache", "build", "dist"}
EXCLUDED_SOURCE_NAMES = {".DS_Store", "PACKAGE_MANIFEST.json"}


def _display_command(arguments: list[str]) -> list[str]:
    return ["PYTHON" if index == 0 else value for index, value in enumerate(arguments)]


def _installed_version() -> str | None:
    # Query metadata in a clean child process outside the checkout.  Calling
    # importlib.metadata here would let an inherited PYTHONPATH expose a local
    # *.egg-info directory and misreport the source tree as installed.
    command = [
        sys.executable,
        "-c",
        "import importlib.metadata as m; "
        "print(m.version('acgm-recover') if any(d.metadata.get('Name') == 'acgm-recover' "
        "for d in m.distributions()) else '')",
    ]
    with tempfile.TemporaryDirectory(prefix="acgm-recover-metadata-") as temporary:
        process = _run(command, cwd=Path(temporary))
    value = process.stdout.strip()
    return value if process.returncode == 0 and value else None


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
    environment = os.environ.copy()
    environment.update(
        {
            "PIP_DISABLE_PIP_VERSION_CHECK": "1",
            "PIP_NO_INDEX": "1",
            "PIP_CONFIG_FILE": os.devnull,
        }
    )
    environment.pop("PIP_INDEX_URL", None)
    environment.pop("PIP_EXTRA_INDEX_URL", None)
    # Neither installation nor post-install verification may import the
    # checkout through caller-provided Python path configuration.
    environment.pop("PYTHONPATH", None)
    environment.pop("PYTHONHOME", None)
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
    distribution = "acgm_recover"
    dist_info = f"{distribution}-{wheel_version}.dist-info"
    members: dict[str, bytes] = {}
    package_prefix = "src/acgm_recover/"
    for relative, payload in sorted(verified.items()):
        if relative.startswith(package_prefix):
            members[relative.removeprefix("src/")] = payload
    if "acgm_recover/__init__.py" not in members or "acgm_recover/cli.py" not in members:
        raise ValueError("package_source_missing")

    members[f"{dist_info}/METADATA"] = (
        "Metadata-Version: 2.1\n"
        "Name: acgm-recover\n"
        f"Version: {wheel_version}\n"
        "Summary: Offline, evidence-first Claude Code project recovery\n"
        "Requires-Python: >=3.10\n"
        "License: MIT for code; CC-BY-4.0 for documentation. See LICENSING.md.\n"
        "\n"
    ).encode("utf-8")
    members[f"{dist_info}/WHEEL"] = (
        "Wheel-Version: 1.0\n"
        f"Generator: ACGM Recover bootstrap {source_version}\n"
        "Root-Is-Purelib: true\n"
        "Tag: py3-none-any\n"
        "\n"
    ).encode("utf-8")
    members[f"{dist_info}/entry_points.txt"] = (
        "[console_scripts]\n"
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


def _rollback(previous_version: str | None) -> dict[str, Any]:
    current_version = _installed_version()
    uninstall = [sys.executable, "-m", "pip", "uninstall", "-y", PACKAGE]
    if previous_version is None and current_version is not None:
        process = _run(uninstall)
        return {
            "status": "automatic_cleanup_succeeded" if process.returncode == 0 else "automatic_cleanup_failed",
            "previous_version": None,
            "current_version": _installed_version(),
            "manual_command_argv": _display_command(uninstall),
        }
    return {
        "status": "previous_installation_not_removed",
        "previous_version": previous_version,
        "current_version": current_version,
        "guidance": "Re-run bootstrap from the previously trusted source tree, or uninstall explicitly.",
        "manual_command_argv": _display_command(uninstall),
    }


def _verification(route: str | None) -> tuple[dict[str, Any], bool]:
    commands = {
        "version": [sys.executable, "-m", "acgm_recover", "--version"],
        "doctor": [
            sys.executable,
            "-m",
            "acgm_recover",
            "doctor",
            "--no-default-sources",
        ],
        "guide": [sys.executable, "-m", "acgm_recover", "guide", "--no-default-sources"],
    }
    if route:
        commands["guide"].extend(["--route", route])
    results: dict[str, Any] = {}
    version_ok = False
    guide_installation_ready = False
    origin_command = [
        sys.executable,
        "-c",
        "import acgm_recover; from pathlib import Path; "
        "print(Path(acgm_recover.__file__).resolve())",
    ]
    with tempfile.TemporaryDirectory(prefix="acgm-recover-verify-") as temporary:
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
                version_ok = process.returncode == 0 and "ACGM Recover" in value
            else:
                try:
                    payload = json.loads(process.stdout)
                except json.JSONDecodeError:
                    payload = None
                row["result"] = payload
                if name == "guide" and isinstance(payload, dict):
                    guide_installation_ready = payload.get("installation_ready") is True
            results[name] = row
    return results, origin_ok and version_ok and guide_installation_ready


def install(*, dry_run: bool, route: str | None, upgrade: bool) -> tuple[dict[str, Any], int]:
    prerequisites = _prerequisites()
    in_virtual_environment = sys.prefix != getattr(sys, "base_prefix", sys.prefix) or hasattr(sys, "real_prefix")
    command = [
        sys.executable,
        "-m",
        "pip",
        "install",
        "--no-deps",
        "--no-build-isolation",
        "--no-index",
    ]
    if not in_virtual_environment:
        command.append("--user")
    if upgrade:
        command.append("--upgrade")
    previous_version = _installed_version()
    source_version = _source_version()
    version_action, version_allowed = _version_policy(previous_version, source_version, upgrade)
    if version_action == "same_version_reinstall":
        command.append("--force-reinstall")
    command.append("VERIFIED_LOCAL_WHEEL")
    base: dict[str, Any] = {
        "tool": "ACGM Recover bootstrap",
        "dry_run": dry_run,
        "prerequisites": prerequisites,
        "install_command_argv": _display_command(command),
        "install_scope": "virtual_environment" if in_virtual_environment else "current_user",
        "source_version": source_version,
        "installed_version_before": previous_version,
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
                    "official source tree whose PACKAGE_MANIFEST.json matches."
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
    if dry_run:
        base.update(
            {
                "ok": True,
                "status": "dry_run_complete_no_changes",
                "next_action": "Run the same command without --dry-run after reviewing this plan.",
            }
        )
        return base, 0

    try:
        with tempfile.TemporaryDirectory(prefix="acgm-recover-wheel-") as temporary:
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
                "rollback": _rollback(previous_version),
            }
        )
        return base, 3

    verification, verified = _verification(route)
    if not verified:
        base.update(
            {
                "ok": False,
                "status": "post_install_verification_failed",
                "verification": verification,
                "rollback": _rollback(previous_version),
            }
        )
        return base, 4

    guide_result = verification["guide"].get("result") or {}
    base.update(
        {
            "ok": True,
            "status": "installed_and_verified",
            "installed_version": _installed_version(),
            "verification": verification,
            "recovery_runtime_supported": guide_result.get("recovery_runtime_supported", False),
            "route_argument_required": route is None,
            "route_confirmation_still_required": True,
            "next_commands_argv": guide_result.get("next_commands_argv", []),
            "future_commands_after_confirmation_argv": guide_result.get(
                "future_commands_after_confirmation_argv", []
            ),
        }
    )
    return base, 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Install ACGM Recover locally without scanning evidence.")
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
