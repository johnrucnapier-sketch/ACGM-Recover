from __future__ import annotations

import json
import io
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from acgm_recover import cli
from acgm_recover.onboarding import environment_guide


ROOT = Path(__file__).resolve().parents[1]


class OnboardingTests(unittest.TestCase):
    def test_guide_requires_route_and_preserves_privacy_boundary(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "source"
            source.mkdir()
            result = environment_guide(None, source_roots=([source], [], []))
        self.assertEqual(result["route_selection"]["status"], "selection_required")
        self.assertTrue(result["route_selection"]["selection_required"])
        self.assertFalse(result["route_selection"]["automatic_selection_performed"])
        self.assertTrue(result["authorization"]["next_scan_requires_explicit_user_action"])
        self.assertTrue(result["authorization"]["route_requires_explicit_user_confirmation"])
        self.assertFalse(result["authorization"]["agent_self_confirmation_allowed"])
        self.assertFalse(result["authorization"]["installation_authorizes_evidence_discovery"])
        self.assertFalse(result["privacy"]["network_used"])
        self.assertFalse(result["privacy"]["evidence_scan_performed"])
        self.assertEqual(result["privacy"]["model_identity_assessment"], "not_performed")
        self.assertEqual(
            result["environment"]["default_source_location_presence"]["claude_projects"],
            {"configured_locations": 1, "visible_directories": 1},
        )

    def test_explicit_route_is_not_claimed_as_user_identity_confirmation(self) -> None:
        result = environment_guide(
            "agent-neutral",
            source_roots=([], [], []),
        )
        selection = result["route_selection"]
        self.assertEqual(selection["status"], "explicit_cli_argument")
        self.assertEqual(selection["selected_route"], "agent-neutral")
        self.assertTrue(selection["user_confirmation_still_required"])
        self.assertNotIn("user_confirmed", json.dumps(result))
        self.assertFalse(result["build_ready"])
        self.assertTrue(
            result["command_template_contract"]["commands_are_templates_not_authorized_actions"]
        )
        if result["recovery_runtime_supported"]:
            self.assertNotIn('"build"', json.dumps(result["next_commands_argv"]))
            self.assertIn('"build"', json.dumps(result["future_commands_after_confirmation_argv"]))
            self.assertTrue(
                result["authorization"]["project_confirmation_required_before_inspect_or_build"]
            )

    def test_windows_onboarding_does_not_claim_core_runtime_or_emit_build(self) -> None:
        with (
            mock.patch("acgm_recover.onboarding.recovery_runtime_supported", return_value=False),
            mock.patch("acgm_recover.onboarding.platform.system", return_value="Windows"),
        ):
            result = environment_guide(
                "claude-new-account",
                source_roots=([], [], []),
            )
        self.assertTrue(result["installation_ready"])
        self.assertFalse(result["recovery_runtime_supported"])
        self.assertFalse(result["scan_ready"])
        self.assertFalse(result["build_ready"])
        flattened = json.dumps(result["next_commands_argv"])
        self.assertNotIn('"build"', flattened)
        self.assertNotIn('"discover"', flattened)
        self.assertEqual(result["future_commands_after_confirmation_argv"], [])

    def test_module_entrypoint_and_custom_source_options(self) -> None:
        environment = os.environ.copy()
        environment["PYTHONPATH"] = str(ROOT / "src")
        process = subprocess.run(
            [
                sys.executable,
                "-m",
                "acgm_recover",
                "guide",
                "--no-default-sources",
                "--route",
                "claude-compatible-api",
            ],
            cwd=ROOT,
            env=environment,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=20,
            check=False,
        )
        payload = json.loads(process.stdout)
        expected = 0 if payload["recovery_runtime_supported"] else 1
        self.assertEqual(process.returncode, expected, process.stderr)
        self.assertEqual(payload["route_selection"]["selected_route"], "claude-compatible-api")
        self.assertEqual(
            payload["environment"]["default_source_location_presence"]["claude_projects"],
            {"configured_locations": 0, "visible_directories": 0},
        )

    def test_windows_core_commands_fail_before_source_access(self) -> None:
        commands = (
            ["discover", "--no-default-sources"],
            ["inspect", "--project", "unused", "--no-default-sources"],
            ["build", "--project", "unused", "--output", "unused-output"],
            ["verify", "--bundle", "unused"],
        )
        with (
            mock.patch("acgm_recover.cli.recovery_runtime_supported", return_value=False),
            mock.patch("acgm_recover.cli._sources", side_effect=AssertionError("source access attempted")),
            mock.patch("acgm_recover.cli._analyze", side_effect=AssertionError("analysis attempted")),
            mock.patch("acgm_recover.cli.discover_candidates", side_effect=AssertionError("scan attempted")),
            mock.patch("acgm_recover.cli.verify_bundle", side_effect=AssertionError("verify attempted")),
        ):
            for arguments in commands:
                with self.subTest(command=arguments[0]):
                    output = io.StringIO()
                    with mock.patch("sys.stdout", output):
                        code = cli.main(list(arguments))
                    self.assertEqual(code, 2)
                    self.assertEqual(
                        json.loads(output.getvalue())["error"],
                        "recovery_runtime_not_supported_on_platform",
                    )


if __name__ == "__main__":
    unittest.main()
