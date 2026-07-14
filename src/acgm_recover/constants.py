"""Stable product constants and observed-source defaults."""

from __future__ import annotations

from pathlib import Path

TOOL_VERSION = "0.1.0-rc.1"
SCHEMA_VERSION = "1.0"

ROUTES = (
    "claude-compatible-api",
    "claude-new-account",
    "agent-neutral",
)

CONFIDENCE_LEVELS = (
    "verified",
    "corroborated",
    "probable",
    "candidate",
    "unresolved",
)

CONFLICT_STATES = (
    "none",
    "temporal_difference",
    "superseded",
    "contradictory",
)

TRANSCRIPT_CATEGORIES = (
    "main_transcript",
    "subagent_transcript",
    "local_agent_output",
    "homunculus_observations",
    "tool_result_or_task",
    "metrics_or_cache",
    "unknown_jsonl",
)

MAX_METADATA_BYTES = 32 * 1024 * 1024
MAX_METADATA_SOURCE_FILES = 20_000
MAX_METADATA_TOTAL_BUDGET_BYTES = 1024 * 1024 * 1024
MAX_METADATA_RECORDS_PER_FILE = 10_000
MAX_METADATA_RECORDS_TOTAL = 100_000
MAX_SIDECAR_BYTES = 1024 * 1024
MAX_JSONL_BYTES = 256 * 1024 * 1024
MAX_JSONL_LINES = 250_000
MAX_JSONL_LINE_BYTES = 4 * 1024 * 1024
MAX_JSONL_SOURCE_FILES = 10_000
MAX_JSONL_TOTAL_BUDGET_BYTES = 8 * 1024 * 1024 * 1024
MAX_VALUES_PER_FIELD = 128
MAX_TOOL_IDENTIFIERS = 100_000
MAX_INVENTORY_FILES = 250_000

DEFAULT_EXCLUDED_DIRS = frozenset(
    {
        ".git",
        ".hg",
        ".svn",
        "node_modules",
        "__pycache__",
        ".venv",
        "venv",
        "dist",
        "build",
        ".cache",
    }
)

SENSITIVE_FILE_NAMES = frozenset(
    {
        ".env",
        ".env.local",
        ".env.production",
        ".npmrc",
        ".pypirc",
        "credentials.json",
        "service-account.json",
        "id_rsa",
        "id_ed25519",
    }
)


def default_claude_projects_roots(home: Path | None = None) -> list[Path]:
    base = (home or Path.home()).expanduser()
    return [base / ".claude" / "projects"]


def default_auxiliary_roots(home: Path | None = None) -> list[Path]:
    base = (home or Path.home()).expanduser()
    return [base / ".claude" / "homunculus" / "projects"]


def default_metadata_roots(home: Path | None = None) -> list[Path]:
    base = (home or Path.home()).expanduser()
    app_support = base / "Library" / "Application Support"
    return [
        app_support / "Claude" / "claude-code-sessions",
        app_support / "Claude" / "local-agent-mode-sessions",
        app_support / "Claude-3p" / "claude-code-sessions",
        app_support / "Claude-3p" / "local-agent-mode-sessions",
    ]
