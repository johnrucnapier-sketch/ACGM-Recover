from __future__ import annotations

import importlib.util
import hashlib
import json
import os
import subprocess
import sys
import unittest
from pathlib import Path
from unittest import mock
import tempfile
import zipfile


ROOT = Path(__file__).resolve().parents[1]
BOOTSTRAP_PATH = ROOT / "scripts" / "bootstrap.py"
SPEC = importlib.util.spec_from_file_location("acgm_recover_bootstrap", BOOTSTRAP_PATH)
if SPEC is None or SPEC.loader is None:
    raise RuntimeError("bootstrap_import_failed")
bootstrap = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(bootstrap)


class BootstrapTests(unittest.TestCase):
    def test_version_policy_is_idempotent_explicit_and_no_downgrade(self) -> None:
        self.assertEqual(
            bootstrap._version_policy("0.1.0rc3", "0.1.0-rc.3", False),
            ("same_version_reinstall", True),
        )
        self.assertEqual(
            bootstrap._version_policy("0.1.0rc1", "0.1.0-rc.3", False),
            ("upgrade_confirmation_required", False),
        )
        self.assertEqual(
            bootstrap._version_policy("0.1.0rc1", "0.1.0-rc.3", True),
            ("explicit_upgrade", True),
        )
        self.assertEqual(
            bootstrap._version_policy("0.2.0", "0.1.0-rc.3", True),
            ("downgrade_refused", False),
        )

    def test_source_manifest_is_verified_before_install(self) -> None:
        ok, error = bootstrap._manifest_check()
        self.assertTrue(ok, error)

    def test_stdlib_wheel_is_complete_and_does_not_need_build_packages(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            wheel = bootstrap._build_offline_wheel(Path(tmp), "0.1.0-rc.3")
            with zipfile.ZipFile(wheel) as archive:
                names = set(archive.namelist())
                metadata = archive.read(
                    "claude_code_recover-0.1.0rc3.dist-info/METADATA"
                ).decode("utf-8")
                entry_points = archive.read(
                    "claude_code_recover-0.1.0rc3.dist-info/entry_points.txt"
                ).decode("utf-8")
        self.assertIn("claude_code_recover/__main__.py", names)
        self.assertIn("acgm_recover/__main__.py", names)
        self.assertIn("claude_code_recover-0.1.0rc3.dist-info/RECORD", names)
        self.assertIn("Name: claude-code-recover", metadata)
        self.assertIn("Version: 0.1.0rc3", metadata)
        self.assertIn("claude-code-recover = claude_code_recover.cli:main", entry_points)
        self.assertIn("acgm-recover = acgm_recover.cli:main", entry_points)
        self.assertNotIn("setuptools", "\n".join(names))

    def test_wheel_uses_manifest_verified_snapshot_not_a_second_source_read(self) -> None:
        verified, error = bootstrap._verified_manifest_payloads()
        self.assertIsNone(error)
        self.assertIsNotNone(verified)
        snapshot = dict(verified or {})
        snapshot["src/claude_code_recover/__init__.py"] = b"SNAPSHOT_SENTINEL = True\n"
        with (
            tempfile.TemporaryDirectory() as tmp,
            mock.patch.object(
                bootstrap,
                "_verified_manifest_payloads",
                return_value=(snapshot, None),
            ),
        ):
            wheel = bootstrap._build_offline_wheel(Path(tmp), "0.1.0-rc.3")
            with zipfile.ZipFile(wheel) as archive:
                installed = archive.read("claude_code_recover/__init__.py")
        self.assertEqual(installed, b"SNAPSHOT_SENTINEL = True\n")

    def test_unlisted_source_file_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            files = {"VERSION": b"0.1.0-rc.3\n", "trusted.py": b"pass\n"}
            for name, payload in files.items():
                (root / name).write_bytes(payload)
            manifest = {
                "package": "claude-code-recover",
                "version": "0.1.0-rc.3",
                "file_count": len(files),
                "files": [
                    {
                        "path": name,
                        "size": len(payload),
                        "sha256": hashlib.sha256(payload).hexdigest(),
                    }
                    for name, payload in sorted(files.items())
                ],
            }
            (root / "PACKAGE_MANIFEST.json").write_text(json.dumps(manifest), encoding="utf-8")
            (root / "unlisted_setup.py").write_text("raise RuntimeError('must not run')\n", encoding="utf-8")
            with mock.patch.object(bootstrap, "ROOT", root):
                ok, error = bootstrap._manifest_check()
        self.assertFalse(ok)
        self.assertEqual(error, "source_manifest_file_set_mismatch")

    def test_dry_run_is_offline_and_does_not_authorize_discovery(self) -> None:
        before = bootstrap._installed_version()
        process = subprocess.run(
            [sys.executable, str(BOOTSTRAP_PATH), "--dry-run", "--upgrade", "--json"],
            cwd=ROOT,
            env={**os.environ, "PYTHONPATH": str(ROOT / "src")},
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=30,
            check=False,
        )
        payload = json.loads(process.stdout)
        self.assertEqual(process.returncode, 0, process.stderr)
        self.assertEqual(payload["status"], "dry_run_complete_no_changes")
        self.assertFalse(payload["network_used"])
        self.assertFalse(payload["evidence_scan_performed"])
        self.assertFalse(payload["installation_authorizes_discovery"])
        self.assertIn("--no-deps", payload["install_command_argv"])
        self.assertIn("--no-build-isolation", payload["install_command_argv"])
        self.assertIn("--no-index", payload["install_command_argv"])
        self.assertIn("VERIFIED_LOCAL_WHEEL", payload["install_command_argv"])
        self.assertEqual(payload["installer_backend"], "stdlib_wheel_plus_pip")
        if payload["install_scope"] == "virtual_environment":
            self.assertNotIn("--user", payload["install_command_argv"])
        else:
            self.assertIn("--user", payload["install_command_argv"])
        environment = payload["installation_environment"]
        if environment["pip_break_system_packages_enabled"]:
            self.assertTrue(environment["externally_managed"])
            self.assertIn("--user", payload["install_command_argv"])
            self.assertIn("--break-system-packages", payload["install_command_argv"])
        else:
            self.assertNotIn("--break-system-packages", payload["install_command_argv"])
        self.assertEqual(bootstrap._installed_version(), before)

    def test_dry_run_keeps_explicit_route_as_argument_only(self) -> None:
        with mock.patch.object(bootstrap, "_installed_distribution_versions", return_value={}):
            result, code = bootstrap.install(
                dry_run=True,
                route="agent-neutral",
                upgrade=False,
            )
        self.assertEqual(code, 0)
        self.assertEqual(result["route_argument"], "agent-neutral")
        self.assertFalse(result["route_selected_automatically"])
        self.assertNotIn("discover", " ".join(result["install_command_argv"]))

    def test_pep668_marker_uses_the_selected_interpreter_stdlib(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            marker = Path(tmp) / "EXTERNALLY-MANAGED"
            marker.write_text("[externally-managed]\n", encoding="utf-8")
            with (
                mock.patch.object(bootstrap.sysconfig, "get_default_scheme", return_value="test"),
                mock.patch.object(bootstrap.sysconfig, "get_path", return_value=tmp) as get_path,
            ):
                self.assertTrue(bootstrap._externally_managed_marker_present())
        get_path.assert_called_once_with("stdlib", "test")

    def test_pep668_marker_permission_error_fails_closed(self) -> None:
        with (
            mock.patch.object(bootstrap.sysconfig, "get_default_scheme", return_value="test"),
            mock.patch.object(bootstrap.sysconfig, "get_path", return_value="/unreadable"),
            mock.patch.object(bootstrap.Path, "stat", side_effect=PermissionError("denied")),
        ):
            environment = bootstrap._installation_environment(False)
        self.assertEqual(environment["status"], "externally_managed_marker_check_failed")
        self.assertIsNone(environment["externally_managed"])
        self.assertFalse(environment["safe_install_plan_available"])

    def test_pep668_non_regular_marker_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "EXTERNALLY-MANAGED").mkdir()
            with (
                mock.patch.object(bootstrap.sysconfig, "get_default_scheme", return_value="test"),
                mock.patch.object(bootstrap.sysconfig, "get_path", return_value=tmp),
            ):
                environment = bootstrap._installation_environment(False)
        self.assertEqual(environment["status"], "externally_managed_marker_check_failed")
        self.assertIsNone(environment["externally_managed"])
        self.assertFalse(environment["safe_install_plan_available"])

    def test_pip_override_capability_probe_is_exact_and_fail_closed(self) -> None:
        cases = (
            (0, "  --break-system-packages  Allow override\n", True, "supported"),
            (0, "This pip has no matching option\n", False, "unsupported"),
            (2, "  --break-system-packages  unavailable\n", None, "capability_check_failed"),
        )
        for returncode, stdout, expected, status in cases:
            with (
                self.subTest(returncode=returncode, stdout=stdout),
                mock.patch.object(
                    bootstrap,
                    "_run",
                    return_value=subprocess.CompletedProcess(
                        args=[], returncode=returncode, stdout=stdout, stderr=""
                    ),
                ) as run,
            ):
                supported, check_status = bootstrap._pip_install_break_system_packages_support()
            self.assertIs(supported, expected)
            self.assertEqual(check_status, status)
            self.assertEqual(
                run.call_args.args[0],
                [sys.executable, "-m", "pip", "install", "--help"],
            )

    def test_installation_environment_matrix(self) -> None:
        marker = mock.Mock(return_value=False)
        capability = mock.Mock(return_value=(True, "supported"))
        with (
            mock.patch.object(bootstrap, "_externally_managed_marker_present", marker),
            mock.patch.object(bootstrap, "_pip_install_break_system_packages_support", capability),
        ):
            ordinary = bootstrap._installation_environment(False)
        self.assertEqual(ordinary["status"], "ordinary_interpreter")
        self.assertTrue(ordinary["user_scope_flag_enabled"])
        self.assertFalse(ordinary["pip_break_system_packages_enabled"])
        capability.assert_not_called()

        marker = mock.Mock(return_value=True)
        capability = mock.Mock(return_value=(True, "supported"))
        with (
            mock.patch.object(bootstrap, "_externally_managed_marker_present", marker),
            mock.patch.object(bootstrap, "_pip_install_break_system_packages_support", capability),
        ):
            supported = bootstrap._installation_environment(False)
        self.assertEqual(supported["status"], "externally_managed_user_override_supported")
        self.assertTrue(supported["pip_break_system_packages_support_checked"])
        self.assertTrue(supported["pip_break_system_packages_enabled"])
        self.assertTrue(supported["safe_install_plan_available"])

        marker = mock.Mock(return_value=True)
        capability = mock.Mock(return_value=(False, "unsupported"))
        with (
            mock.patch.object(bootstrap, "_externally_managed_marker_present", marker),
            mock.patch.object(bootstrap, "_pip_install_break_system_packages_support", capability),
        ):
            unsupported = bootstrap._installation_environment(False)
        self.assertFalse(unsupported["pip_break_system_packages_enabled"])
        self.assertFalse(unsupported["safe_install_plan_available"])

        with mock.patch.object(
            bootstrap,
            "_externally_managed_marker_present",
            side_effect=OSError("unreadable"),
        ):
            unreadable = bootstrap._installation_environment(False)
        self.assertEqual(unreadable["status"], "externally_managed_marker_check_failed")
        self.assertIsNone(unreadable["externally_managed"])
        self.assertFalse(unreadable["safe_install_plan_available"])

        marker = mock.Mock()
        capability = mock.Mock()
        with (
            mock.patch.object(bootstrap, "_externally_managed_marker_present", marker),
            mock.patch.object(bootstrap, "_pip_install_break_system_packages_support", capability),
        ):
            virtual = bootstrap._installation_environment(True)
        self.assertEqual(virtual["status"], "virtual_environment")
        self.assertFalse(virtual["user_scope_flag_enabled"])
        self.assertFalse(virtual["pip_break_system_packages_enabled"])
        marker.assert_not_called()
        capability.assert_not_called()

    def test_install_plan_pairs_pep668_override_with_user_scope_only(self) -> None:
        prerequisites = {"ok": True}
        with (
            mock.patch.object(bootstrap, "_prerequisites", return_value=prerequisites),
            mock.patch.object(bootstrap, "_installed_distribution_versions", return_value={}),
            mock.patch.object(
                bootstrap,
                "_installation_environment",
                return_value={
                    "safe_install_plan_available": True,
                    "user_scope_flag_enabled": True,
                    "pip_break_system_packages_enabled": True,
                    "externally_managed": True,
                },
            ),
        ):
            result, code = bootstrap.install(dry_run=True, route=None, upgrade=False)
        self.assertEqual(code, 0)
        command = result["install_command_argv"]
        self.assertIn("--user", command)
        self.assertIn("--break-system-packages", command)
        self.assertLess(command.index("--user"), command.index("--break-system-packages"))

    def test_ordinary_install_plan_never_uses_pep668_override(self) -> None:
        prerequisites = {"ok": True}
        with (
            mock.patch.object(bootstrap, "_prerequisites", return_value=prerequisites),
            mock.patch.object(bootstrap, "_installed_distribution_versions", return_value={}),
            mock.patch.object(
                bootstrap,
                "_installation_environment",
                return_value={
                    "safe_install_plan_available": True,
                    "user_scope_flag_enabled": True,
                    "pip_break_system_packages_enabled": False,
                    "externally_managed": False,
                },
            ),
        ):
            result, code = bootstrap.install(dry_run=True, route=None, upgrade=False)
        self.assertEqual(code, 0)
        self.assertIn("--user", result["install_command_argv"])
        self.assertNotIn("--break-system-packages", result["install_command_argv"])

    def test_unsupported_pep668_install_fails_closed_before_dry_run_or_install(self) -> None:
        prerequisites = {"ok": True}
        unavailable = {
            "safe_install_plan_available": False,
            "user_scope_flag_enabled": True,
            "pip_break_system_packages_enabled": False,
            "externally_managed": True,
            "pip_break_system_packages_support_checked": True,
            "pip_break_system_packages_supported": False,
        }
        for dry_run in (True, False):
            with (
                self.subTest(dry_run=dry_run),
                mock.patch.object(bootstrap, "_prerequisites", return_value=prerequisites),
                mock.patch.object(bootstrap, "_installed_distribution_versions", return_value={}),
                mock.patch.object(bootstrap, "_installation_environment", return_value=unavailable),
                mock.patch.object(bootstrap, "_build_offline_wheel") as build,
                mock.patch.object(bootstrap, "_run") as run,
            ):
                result, code = bootstrap.install(dry_run=dry_run, route=None, upgrade=False)
            self.assertEqual(code, 2)
            self.assertEqual(result["status"], "externally_managed_user_install_unavailable")
            self.assertFalse(result["install_command_executable"])
            self.assertIsNone(result["install_command_argv"])
            self.assertFalse(result["mutation_performed"])
            build.assert_not_called()
            run.assert_not_called()

    def test_marker_check_failure_has_truthful_top_level_status(self) -> None:
        prerequisites = {"ok": True}
        unavailable = {
            "status": "externally_managed_marker_check_failed",
            "safe_install_plan_available": False,
            "user_scope_flag_enabled": True,
            "pip_break_system_packages_enabled": False,
            "externally_managed": None,
        }
        with (
            mock.patch.object(bootstrap, "_prerequisites", return_value=prerequisites),
            mock.patch.object(bootstrap, "_installed_distribution_versions", return_value={}),
            mock.patch.object(bootstrap, "_installation_environment", return_value=unavailable),
            mock.patch.object(bootstrap, "_build_offline_wheel") as build,
            mock.patch.object(bootstrap, "_run") as run,
        ):
            result, code = bootstrap.install(dry_run=False, route=None, upgrade=False)
        self.assertEqual(code, 2)
        self.assertEqual(result["status"], "externally_managed_marker_check_failed")
        self.assertIn("could not safely determine", result["guidance"])
        self.assertFalse(result["mutation_performed"])
        build.assert_not_called()
        run.assert_not_called()

    def test_virtual_environment_install_plan_omits_user_scope_flag(self) -> None:
        prerequisites = {"ok": True}
        with (
            mock.patch.object(bootstrap, "_prerequisites", return_value=prerequisites),
            mock.patch.object(bootstrap, "_installed_distribution_versions", return_value={}),
            mock.patch.object(bootstrap.sys, "prefix", "/venv"),
            mock.patch.object(bootstrap.sys, "base_prefix", "/base"),
        ):
            result, code = bootstrap.install(dry_run=True, route=None, upgrade=False)
        self.assertEqual(code, 0)
        self.assertEqual(result["install_scope"], "virtual_environment")
        self.assertNotIn("--user", result["install_command_argv"])
        self.assertNotIn("--break-system-packages", result["install_command_argv"])

    def test_same_version_plan_forces_verified_source_reinstall(self) -> None:
        prerequisites = {"ok": True}
        with (
            mock.patch.object(bootstrap, "_prerequisites", return_value=prerequisites),
            mock.patch.object(
                bootstrap,
                "_installed_distribution_versions",
                return_value={"claude-code-recover": "0.1.0rc3"},
            ),
        ):
            result, code = bootstrap.install(dry_run=True, route=None, upgrade=False)
        self.assertEqual(code, 0)
        self.assertEqual(result["version_action"], "same_version_reinstall")
        self.assertIn("--force-reinstall", result["install_command_argv"])

    def test_rc1_distribution_requires_separate_migration_without_mutation(self) -> None:
        prerequisites = {"ok": True}
        for upgrade in (False, True):
            with (
                self.subTest(upgrade=upgrade),
                mock.patch.object(bootstrap, "_prerequisites", return_value=prerequisites),
                mock.patch.object(
                    bootstrap,
                    "_installed_distribution_versions",
                    return_value={"acgm-recover": "0.1.0rc1"},
                ),
                mock.patch.object(bootstrap, "_installation_environment") as install_environment,
                mock.patch.object(bootstrap, "_build_offline_wheel") as build,
                mock.patch.object(bootstrap, "_run") as run,
            ):
                result, code = bootstrap.install(
                    dry_run=False,
                    route=None,
                    upgrade=upgrade,
                )
            self.assertEqual(code, 2)
            self.assertFalse(result["ok"])
            self.assertEqual(result["status"], "MIGRATION_REQUIRED")
            self.assertFalse(result["mutation_performed"])
            plan = result["migration_plan"]
            self.assertFalse(plan["executable"])
            self.assertTrue(plan["requires_separate_user_authorization"])
            self.assertTrue(all(step["authorized"] is False for step in plan["steps"]))
            install_environment.assert_not_called()
            build.assert_not_called()
            run.assert_not_called()

    def test_run_removes_python_path_configuration(self) -> None:
        fake = mock.Mock(returncode=0, stdout="", stderr="")
        with (
            mock.patch.dict(
                os.environ,
                {
                    "PYTHONPATH": "untrusted",
                    "PYTHONHOME": "untrusted",
                    "PIP_BREAK_SYSTEM_PACKAGES": "1",
                    "PIP_USER": "1",
                    "PIP_TARGET": "/untrusted-target",
                    "PIP_PREFIX": "/untrusted-prefix",
                    "PIP_ROOT": "/untrusted-root",
                    "PIP_REQUIRE_VIRTUALENV": "1",
                    "PIP_REQUIREMENT": "/untrusted-requirements.txt",
                    "PIP_FIND_LINKS": "https://untrusted.invalid/packages",
                    "PYTHONUSERBASE": "/untrusted-user-base",
                    "PYTHONNOUSERSITE": "1",
                },
            ),
            mock.patch.object(bootstrap.subprocess, "run", return_value=fake) as run,
        ):
            bootstrap._run([sys.executable, "--version"])
        environment = run.call_args.kwargs["env"]
        self.assertNotIn("PYTHONPATH", environment)
        self.assertNotIn("PYTHONHOME", environment)
        self.assertNotIn("PYTHONUSERBASE", environment)
        self.assertNotIn("PYTHONNOUSERSITE", environment)
        pip_environment = {
            key: value for key, value in environment.items() if key.upper().startswith("PIP_")
        }
        self.assertEqual(
            pip_environment,
            {
                "PIP_DISABLE_PIP_VERSION_CHECK": "1",
                "PIP_NO_INDEX": "1",
                "PIP_CONFIG_FILE": os.devnull,
            },
        )

    def test_pep668_failure_never_runs_automatic_uninstall(self) -> None:
        installation_environment = {
            "user_scope_flag_enabled": True,
            "pip_break_system_packages_enabled": True,
        }
        with (
            mock.patch.object(
                bootstrap,
                "_installed_distribution_versions",
                return_value={"claude-code-recover": "0.1.0rc3"},
            ),
            mock.patch.object(bootstrap, "_run") as run,
        ):
            result = bootstrap._rollback(
                None,
                installation_environment=installation_environment,
            )
        self.assertEqual(result["status"], "externally_managed_no_automatic_cleanup")
        self.assertFalse(result["automatic_cleanup_attempted"])
        self.assertFalse(result["pip_break_system_packages_enabled"])
        self.assertIsNone(result["manual_command_argv"])
        run.assert_not_called()

    def test_installed_version_query_uses_clean_external_process(self) -> None:
        completed = subprocess.CompletedProcess(
            args=[], returncode=0, stdout='{"acgm-recover": "0.1.0rc1"}\n', stderr=""
        )
        with (
            mock.patch.object(bootstrap.tempfile, "TemporaryDirectory") as temporary,
            mock.patch.object(bootstrap, "_run", return_value=completed) as run,
        ):
            temporary.return_value.__enter__.return_value = "/tmp/external-metadata"
            self.assertEqual(bootstrap._installed_version(), "0.1.0rc1")
        self.assertEqual(run.call_args.kwargs["cwd"], Path("/tmp/external-metadata"))

    def test_unreadable_installed_distribution_state_fails_closed_without_mutation(self) -> None:
        prerequisites = {"ok": True}
        for unreadable in (None,):
            with (
                self.subTest(unreadable=unreadable),
                mock.patch.object(bootstrap, "_prerequisites", return_value=prerequisites),
                mock.patch.object(
                    bootstrap,
                    "_installed_distribution_versions",
                    return_value=unreadable,
                ),
                mock.patch.object(bootstrap, "_run") as run,
            ):
                result, code = bootstrap.install(
                    dry_run=False,
                    route=None,
                    upgrade=True,
                )
            self.assertEqual(code, 2)
            self.assertFalse(result["ok"])
            self.assertEqual(result["status"], "installed_distribution_state_unavailable")
            self.assertFalse(result["mutation_performed"])
            run.assert_not_called()

    def test_invalid_distribution_metadata_output_is_unreadable(self) -> None:
        for completed in (
            subprocess.CompletedProcess(args=[], returncode=1, stdout="", stderr="failed"),
            subprocess.CompletedProcess(args=[], returncode=0, stdout="not-json", stderr=""),
            subprocess.CompletedProcess(args=[], returncode=0, stdout="[]", stderr=""),
            subprocess.CompletedProcess(
                args=[], returncode=0, stdout='{"acgm-recover": null}', stderr=""
            ),
            subprocess.CompletedProcess(
                args=[], returncode=0, stdout='{"acgm-recover": ""}', stderr=""
            ),
            subprocess.CompletedProcess(
                args=[], returncode=0, stdout='{"unknown-package": "1.0"}', stderr=""
            ),
        ):
            with (
                self.subTest(completed=completed),
                mock.patch.object(bootstrap, "_run", return_value=completed),
            ):
                self.assertIsNone(bootstrap._installed_distribution_versions())

    def test_canonical_distribution_version_takes_precedence_over_legacy(self) -> None:
        completed = subprocess.CompletedProcess(
            args=[],
            returncode=0,
            stdout=(
                '{"acgm-recover": "0.1.0rc1", '
                '"claude-code-recover": "0.1.0rc3"}\n'
            ),
            stderr="",
        )
        with mock.patch.object(bootstrap, "_run", return_value=completed):
            self.assertEqual(bootstrap._installed_version(), "0.1.0rc3")


if __name__ == "__main__":
    unittest.main()
