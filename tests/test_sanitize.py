from __future__ import annotations

import unittest

from claude_code_recover.sanitize import (
    contains_specific_secret,
    sanitize_remote_url,
    sanitize_untrusted,
)


class SanitizeTests(unittest.TestCase):
    def test_known_secret_patterns_are_removed(self) -> None:
        values = [
            "sk-ant-abcdefghijklmnop",
            "sk-proj-abcdefghijklmnop",
            "ghp_abcdefghijklmnop",
            "Bearer abc.def.ghi",
            "Cookie: session-value",
            "https://user:pass@example.invalid/repo?token=value",
            "-----BEGIN PRIVATE KEY-----",
        ]
        for value in values:
            with self.subTest(value=value):
                self.assertTrue(contains_specific_secret(value))
                self.assertNotIn(value, sanitize_untrusted(value))

    def test_remote_url_strips_credentials_and_query(self) -> None:
        value = sanitize_remote_url("https://user:pass@example.invalid/org/repo.git?token=secret")
        self.assertEqual(value, "https://example.invalid/org/repo.git")

    def test_malformed_remote_port_fails_closed(self) -> None:
        self.assertEqual(
            sanitize_remote_url("https://example.invalid:notaport/repo.git"),
            "[REDACTED_REMOTE]",
        )

    def test_cookie_tail_and_local_remote_are_fully_withheld(self) -> None:
        value = sanitize_untrusted("Cookie: harmless=x; sessionid=CANARYABC123")
        self.assertNotIn("sessionid", value)
        self.assertNotIn("CANARY", value)
        self.assertEqual(sanitize_remote_url("/Users/example/private/repo.git"), "[LOCAL_REMOTE_REDACTED]")
        basic = sanitize_untrusted("Authorization: Basic dXNlcjpwYXNz")
        self.assertNotIn("dXNlcjpwYXNz", basic)

    def test_display_label_is_preserved_only_as_text(self) -> None:
        self.assertEqual(sanitize_untrusted("Claude Opus"), "Claude Opus")

    def test_human_readable_repository_slug_is_not_false_positive(self) -> None:
        value = "Agent-Coding-Governance-Methodology"
        self.assertEqual(sanitize_untrusted(value), value)

    def test_uuid_transcript_path_remains_resolvable(self) -> None:
        value = "/safe/11111111-1111-4111-8111-111111111111.jsonl"
        from claude_code_recover.sanitize import sanitize_path

        self.assertEqual(sanitize_path(value), value)


if __name__ == "__main__":
    unittest.main()
