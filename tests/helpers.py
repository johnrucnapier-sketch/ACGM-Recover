from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path


SECRET_SENTINEL = "sk-ant-TESTSECRET1234567890"
MAIN_SESSION = "11111111-1111-4111-8111-111111111111"


def write_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows)
    path.write_text(payload, encoding="utf-8")


def git(path: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(path), *args],
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env={**os.environ, "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": os.devnull},
    )
    return result.stdout.strip()


def create_git_project(root: Path, *, with_worktree: bool = False) -> tuple[Path, Path | None]:
    project = root / "workspace" / "main-repo"
    project.mkdir(parents=True)
    git(project, "init", "--initial-branch=main")
    (project / "README.md").write_text("fixture\n", encoding="utf-8")
    git(project, "add", "README.md")
    git(
        project,
        "-c",
        "user.name=Fixture",
        "-c",
        "user.email=fixture@example.invalid",
        "commit",
        "-m",
        "fixture",
    )
    worktree = None
    if with_worktree:
        worktree = root / "workspace" / "linked-worktree"
        git(project, "worktree", "add", "-b", "feature-fixture", str(worktree), "HEAD")
    return project, worktree


def create_sources(
    root: Path,
    project: Path,
    worktree: Path | None = None,
) -> tuple[Path, Path, Path, Path]:
    claude_root = root / "fixture-home" / ".claude" / "projects"
    bucket = claude_root / "-fixture-project"
    main_path = bucket / f"{MAIN_SESSION}.jsonl"
    second_cwd = str(worktree / "nested") if worktree else "/missing/external/project"
    main_rows = [
        {
            "type": "user",
            "sessionId": MAIN_SESSION,
            "isSidechain": False,
            "cwd": str(project),
            "gitBranch": "main",
            "timestamp": "2026-01-01T00:00:02Z",
            "uuid": "u-root",
            "parentUuid": None,
            "message": {"role": "user", "content": f"untrusted {SECRET_SENTINEL}"},
        },
        {
            "type": "assistant",
            "sessionId": MAIN_SESSION,
            "isSidechain": False,
            "cwd": second_cwd,
            "timestamp": "2026-01-01T00:00:01Z",
            "uuid": "a-tool",
            "parentUuid": "u-root",
            "message": {
                "role": "assistant",
                "model": "Claude Opus (display only)",
                "content": [{"type": "tool_use", "id": "tool-main", "name": "Task", "input": {}}],
            },
        },
        {
            "type": "attachment",
            "sessionId": MAIN_SESSION,
            "isSidechain": False,
            "cwd": str(project),
            "timestamp": "2026-01-01T00:00:03Z",
            "uuid": "attachment-1",
            "parentUuid": "a-tool",
            "response": {"command": f"do not run {SECRET_SENTINEL}", "cookie": "secret"},
        },
    ]
    write_jsonl(main_path, main_rows)

    sub_dir = bucket / MAIN_SESSION / "subagents"
    alpha = sub_dir / "agent-alpha.jsonl"
    write_jsonl(
        alpha,
        [
            {
                "type": "assistant",
                "sessionId": MAIN_SESSION,
                "agentId": "alpha",
                "isSidechain": True,
                "uuid": "alpha-root",
                "parentUuid": None,
                "message": {
                    "role": "assistant",
                    "content": [{"type": "tool_use", "id": "tool-child", "name": "Task", "input": {}}],
                },
            }
        ],
    )
    alpha.with_suffix(".meta.json").write_text(
        json.dumps(
            {
                "agentType": "Explore",
                "description": f"sensitive {SECRET_SENTINEL}",
                "toolUseId": "tool-main",
                "spawnDepth": 1,
            }
        ),
        encoding="utf-8",
    )

    beta = sub_dir / "secondary" / "bucket" / "agent-beta.jsonl"
    write_jsonl(
        beta,
        [
            {
                "type": "assistant",
                "sessionId": MAIN_SESSION,
                "agentId": "beta",
                "isSidechain": True,
                "uuid": "beta-root",
                "parentUuid": None,
                "message": {"role": "assistant", "content": "local detail"},
            }
        ],
    )
    beta.with_suffix(".meta.json").write_text(
        json.dumps({"agentType": "Explore", "toolUseId": "tool-child", "spawnDepth": 2}),
        encoding="utf-8",
    )

    foreign = sub_dir / "secondary" / "bucket" / "foreign-main.jsonl"
    write_jsonl(
        foreign,
        [
            {
                "type": "user",
                "sessionId": "22222222-2222-4222-8222-222222222222",
                "isSidechain": False,
                "cwd": "/another/project",
                "message": {"role": "user", "content": "foreign"},
            }
        ],
    )

    metadata_root = root / "fixture-home" / "Library" / "Application Support" / "Claude-3p" / "claude-code-sessions"
    metadata_root.mkdir(parents=True)
    (metadata_root / "local_fixture.json").write_text(
        json.dumps(
            {
                "sessionId": MAIN_SESSION,
                "title": "Fixture session",
                "cwd": str(project),
                "model": "Claude Opus (display only)",
                "transcriptUnavailable": False,
            }
        ),
        encoding="utf-8",
    )
    auxiliary_root = root / "fixture-home" / ".claude" / "homunculus" / "projects"
    auxiliary_root.mkdir(parents=True)
    return claude_root, metadata_root, auxiliary_root, main_path
