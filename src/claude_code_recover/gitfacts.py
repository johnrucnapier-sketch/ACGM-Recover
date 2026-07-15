"""Read-only Git and worktree observation with hostile-config defenses."""

from __future__ import annotations

import os
import selectors
import stat
import subprocess
import time
from pathlib import Path
from typing import Iterable

from .sanitize import sanitize_remote_url, sanitize_untrusted
from .util import RecoverError, path_is_within, stat_snapshot

MAX_GIT_OUTPUT_BYTES = 32 * 1024 * 1024
MAX_GIT_FILTER_DRIVERS = 512
MAX_GIT_FILTER_DRIVER_BYTES = 128 * 1024
MAX_GIT_OBJECT_DIRECTORIES = 128
MAX_GIT_ALTERNATES_BYTES = 1024 * 1024
MAX_GIT_ALTERNATES_LINES = 1024


def _git_environment() -> dict[str, str]:
    environment = os.environ.copy()
    inherited_git_controls = {
        "GIT_DIR",
        "GIT_WORK_TREE",
        "GIT_COMMON_DIR",
        "GIT_INDEX_FILE",
        "GIT_OBJECT_DIRECTORY",
        "GIT_ALTERNATE_OBJECT_DIRECTORIES",
        "GIT_NAMESPACE",
        "GIT_CEILING_DIRECTORIES",
        "GIT_DISCOVERY_ACROSS_FILESYSTEM",
        "GIT_CONFIG_PARAMETERS",
        "GIT_EXEC_PATH",
        "GIT_SSH",
        "GIT_SSH_COMMAND",
    }
    for key in list(environment):
        if (
            key in inherited_git_controls
            or key == "GIT_CONFIG_COUNT"
            or key.startswith("GIT_CONFIG_KEY_")
            or key.startswith("GIT_CONFIG_VALUE_")
            or key.startswith("GIT_TRACE")
        ):
            environment.pop(key, None)
    environment.update(
        {
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_CONFIG_GLOBAL": os.devnull,
            "GIT_OPTIONAL_LOCKS": "0",
            "GIT_TERMINAL_PROMPT": "0",
            "GIT_PAGER": "cat",
            "PAGER": "cat",
            "LC_ALL": "C",
            "GIT_ATTR_NOSYSTEM": "1",
            "GIT_LFS_SKIP_SMUDGE": "1",
        }
    )
    environment.pop("GIT_EXTERNAL_DIFF", None)
    environment.pop("GIT_ASKPASS", None)
    environment.pop("SSH_ASKPASS", None)
    return environment


def _run_git_bytes(
    project: Path,
    arguments: Iterable[str],
    *,
    timeout: int = 15,
    extra_config: Iterable[tuple[str, str]] = (),
    max_output_bytes: int = MAX_GIT_OUTPUT_BYTES,
    allowed_returncodes: Iterable[int] = (0,),
) -> bytes:
    command = ["git", "--no-pager"]
    command.extend(
        [
        "-C",
        str(project),
        *arguments,
        ]
    )
    process: subprocess.Popen[bytes] | None = None
    selector: selectors.BaseSelector | None = None
    try:
        environment = _git_environment()
        config_pairs = [
            ("core.fsmonitor", "false"),
            ("core.hooksPath", "/dev/null"),
            ("diff.external", ""),
            ("color.ui", "false"),
            *list(extra_config),
        ]
        environment["GIT_CONFIG_COUNT"] = str(len(config_pairs))
        for index, (key, value) in enumerate(config_pairs):
            environment[f"GIT_CONFIG_KEY_{index}"] = key
            environment[f"GIT_CONFIG_VALUE_{index}"] = value
        process = subprocess.Popen(
            command,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            env=environment,
        )
        if process.stdout is None:
            raise RecoverError("git_read_failed")
        selector = selectors.DefaultSelector()
        selector.register(process.stdout, selectors.EVENT_READ)
        chunks: list[bytes] = []
        total = 0
        deadline = time.monotonic() + timeout
        while selector.get_map():
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise RecoverError("git_read_timeout")
            events = selector.select(min(remaining, 0.25))
            for key, _ in events:
                chunk = os.read(key.fd, 64 * 1024)
                if not chunk:
                    selector.unregister(key.fileobj)
                    continue
                total += len(chunk)
                if total > max_output_bytes:
                    raise RecoverError("git_output_budget_exceeded")
                chunks.append(chunk)
        return_code = process.wait(timeout=max(0.1, deadline - time.monotonic()))
        if return_code not in set(allowed_returncodes):
            raise RecoverError("git_read_failed")
        return b"".join(chunks)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise RecoverError("git_read_failed") from exc
    finally:
        if process is not None and process.poll() is None:
            process.kill()
            process.wait()
        if selector is not None:
            selector.close()
        if process is not None and process.stdout is not None:
            process.stdout.close()


def _run_git_text(
    project: Path,
    arguments: Iterable[str],
    *,
    timeout: int = 15,
    allowed_returncodes: Iterable[int] = (0,),
) -> str:
    return _run_git_bytes(
        project,
        arguments,
        timeout=timeout,
        allowed_returncodes=allowed_returncodes,
    ).decode("utf-8", errors="replace").strip()


def _try_git_text(project: Path, arguments: Iterable[str]) -> str | None:
    try:
        return _run_git_text(project, arguments)
    except RecoverError:
        return None


def find_git_root(project: Path) -> Path | None:
    value = _try_git_text(project, ["rev-parse", "--path-format=absolute", "--show-toplevel"])
    if not value:
        return None
    candidate = Path(value).resolve(strict=False)
    project_resolved = project.resolve(strict=False)
    return candidate if candidate.is_dir() and path_is_within(project_resolved, candidate) else None


def _git_path(project: Path, name: str) -> Path | None:
    value = _try_git_text(project, ["rev-parse", "--path-format=absolute", "--git-path", name])
    if not value:
        return None
    return Path(value).resolve(strict=False)


def _optional_stat(path: Path | None) -> dict[str, int] | None:
    if path is None:
        return None
    try:
        return stat_snapshot(path)
    except OSError:
        return None


def _status_summary(project: Path) -> dict[str, object]:
    try:
        filter_overrides = _filter_driver_overrides(project)
        payload = _run_git_bytes(
            project,
            [
                "status",
                "--porcelain=v2",
                "-z",
                "--untracked-files=all",
                "--no-renames",
                "--ignore-submodules=all",
            ],
            extra_config=filter_overrides,
        )
    except RecoverError:
        return {"readable": False, "dirty": None, "entry_count": None}
    records = [record for record in payload.split(b"\0") if record]
    entries = [record for record in records if record[:2] in {b"1 ", b"2 ", b"u ", b"? ", b"! "}]
    return {"readable": True, "dirty": bool(entries), "entry_count": len(entries)}


def _filter_driver_overrides(project: Path) -> list[tuple[str, str]]:
    """Disable every effective repository filter driver before status inspection."""

    payload = _run_git_text(
        project,
        ["config", "--get-regexp", r"^filter\..*\.(clean|process|required)$"],
        allowed_returncodes=(0, 1),
    )
    drivers: set[str] = set()
    total_driver_bytes = 0
    for line in payload.splitlines():
        key = line.split(None, 1)[0]
        if not key.startswith("filter."):
            continue
        body = key[len("filter.") :]
        driver, separator, field = body.rpartition(".")
        if separator and field in {"clean", "process", "required"} and driver:
            if len(driver) > 4096 or any(ord(char) < 32 for char in driver):
                raise RecoverError("git_filter_config_unsafe")
            if driver not in drivers:
                total_driver_bytes += len(driver.encode("utf-8", errors="replace"))
                if len(drivers) >= MAX_GIT_FILTER_DRIVERS or total_driver_bytes > MAX_GIT_FILTER_DRIVER_BYTES:
                    raise RecoverError("git_filter_config_unsafe")
                drivers.add(driver)
    overrides: list[tuple[str, str]] = []
    for driver in sorted(drivers):
        overrides.extend(
            [
                (f"filter.{driver}.clean", ""),
                (f"filter.{driver}.smudge", ""),
                (f"filter.{driver}.process", ""),
                (f"filter.{driver}.required", "false"),
            ]
        )
    return overrides


def _parse_worktrees(payload: str) -> list[dict[str, object]]:
    worktrees: list[dict[str, object]] = []
    current: dict[str, object] = {}
    for line in payload.splitlines() + [""]:
        if not line:
            if current:
                worktrees.append(current)
                current = {}
            continue
        key, _, value = line.partition(" ")
        if key == "worktree":
            current["path"] = value
        elif key == "HEAD":
            current["head"] = value
        elif key == "branch":
            current["branch"] = value.removeprefix("refs/heads/")
        elif key in {"bare", "detached", "prunable"}:
            current[key] = True
        elif key == "locked":
            current["locked"] = True
            if value:
                current["locked_reason_present"] = True
    return worktrees


def _discover_object_directories(project: Path) -> tuple[list[Path], list[tuple[Path, dict[str, int]]]]:
    primary = _git_path(project, "objects")
    if primary is None:
        raise RecoverError("git_object_store_unresolved")
    queue = [primary]
    directories: list[Path] = []
    markers: list[tuple[Path, dict[str, int]]] = []
    seen_identities: set[tuple[int, int]] = set()
    while queue:
        if len(directories) >= MAX_GIT_OBJECT_DIRECTORIES:
            raise RecoverError("git_object_store_budget_exceeded")
        raw = queue.pop(0)
        try:
            directory = raw.resolve(strict=True)
            observed = directory.stat()
        except OSError as exc:
            raise RecoverError("git_object_store_unresolved") from exc
        if not stat.S_ISDIR(observed.st_mode):
            raise RecoverError("git_object_store_unresolved")
        identity = (int(observed.st_dev), int(observed.st_ino))
        if identity in seen_identities:
            continue
        seen_identities.add(identity)
        directories.append(directory)
        alternates = directory / "info" / "alternates"
        if not os.path.lexists(alternates):
            continue
        try:
            if alternates.is_symlink():
                raise RecoverError("git_alternates_unsafe")
            before = stat_snapshot(alternates)
            if not stat.S_ISREG(alternates.stat(follow_symlinks=False).st_mode):
                raise RecoverError("git_alternates_unsafe")
            if before["size"] > MAX_GIT_ALTERNATES_BYTES:
                raise RecoverError("git_object_store_budget_exceeded")
            payload = alternates.read_bytes()
            after = stat_snapshot(alternates)
        except RecoverError:
            raise
        except OSError as exc:
            raise RecoverError("git_alternates_unsafe") from exc
        if before != after or len(payload) != before["size"]:
            raise RecoverError("git_alternates_changed_during_read")
        lines = payload.splitlines()
        if len(lines) > MAX_GIT_ALTERNATES_LINES or b"\0" in payload:
            raise RecoverError("git_object_store_budget_exceeded")
        markers.append((alternates, before))
        for raw_line in lines:
            if not raw_line:
                continue
            value = os.fsdecode(raw_line)
            if value.startswith('"') or any(ord(char) < 32 for char in value):
                raise RecoverError("git_alternates_unsafe")
            candidate = Path(value)
            if not candidate.is_absolute():
                candidate = directory / candidate
            try:
                candidate = candidate.resolve(strict=True)
            except OSError as exc:
                raise RecoverError("git_alternates_unsafe") from exc
            if not candidate.is_dir():
                raise RecoverError("git_alternates_unsafe")
            queue.append(candidate)
    return directories, markers


def inspect_git(project: Path) -> dict[str, object]:
    """Observe current Git facts without refreshing or locking the index."""

    root = find_git_root(project)
    if root is None:
        marker_present = os.path.lexists(project / ".git")
        return {
            "is_git_repository": None if marker_present else False,
            "observation_status": "read_failed" if marker_present else "not_repository",
            "git_root": None,
            "common_dir": None,
            "object_directories": [],
            "object_store_observation_status": "not_applicable",
            "head": None,
            "branch": None,
            "status": {"readable": False, "dirty": None, "entry_count": None},
            "worktrees": [],
            "remotes": [],
            "source_stable_during_read": not marker_present,
        }

    index_path = _git_path(root, "index")
    index_before = _optional_stat(index_path)
    common_raw = _try_git_text(root, ["rev-parse", "--path-format=absolute", "--git-common-dir"])
    common_dir = Path(common_raw).resolve(strict=False) if common_raw else None
    object_directories, object_store_markers = _discover_object_directories(root)
    head = _try_git_text(root, ["rev-parse", "--verify", "HEAD"])
    branch = _try_git_text(root, ["symbolic-ref", "--quiet", "--short", "HEAD"])
    status = _status_summary(root)
    worktree_payload = _try_git_text(root, ["worktree", "list", "--porcelain"])
    raw_worktrees = _parse_worktrees(worktree_payload or "")

    worktrees: list[dict[str, object]] = []
    for item in raw_worktrees:
        raw_path = item.get("path")
        raw_candidate = Path(str(raw_path)).expanduser() if raw_path else None
        raw_is_symlink = bool(raw_candidate and raw_candidate.is_symlink())
        path = raw_candidate.resolve(strict=False) if raw_candidate else None
        candidate_top_raw = (
            _try_git_text(path, ["rev-parse", "--path-format=absolute", "--show-toplevel"])
            if path is not None and path.is_dir() and not raw_is_symlink
            else None
        )
        candidate_common_raw = (
            _try_git_text(path, ["rev-parse", "--path-format=absolute", "--git-common-dir"])
            if candidate_top_raw
            else None
        )
        candidate_top = Path(candidate_top_raw).resolve(strict=False) if candidate_top_raw else None
        candidate_common = Path(candidate_common_raw).resolve(strict=False) if candidate_common_raw else None
        trusted_family_member = bool(
            path is not None
            and candidate_top == path
            and common_dir is not None
            and candidate_common == common_dir
            and not item.get("bare", False)
            and not raw_is_symlink
        )
        worktree_status = _status_summary(path) if trusted_family_member and path is not None else {
            "readable": False,
            "dirty": None,
            "entry_count": None,
        }
        worktrees.append(
            {
                "path": str(path) if path is not None else None,
                "head": item.get("head"),
                "branch": sanitize_untrusted(item.get("branch"), limit=200),
                "bare": bool(item.get("bare", False)),
                "detached": bool(item.get("detached", False)),
                "locked": bool(item.get("locked", False)),
                "locked_reason_present": bool(item.get("locked_reason_present", False)),
                "prunable": bool(item.get("prunable", False)),
                "trusted_family_member": trusted_family_member,
                "status": worktree_status,
            }
        )

    remote_names_raw = _try_git_text(root, ["remote"]) or ""
    remotes: list[dict[str, object]] = []
    for raw_name in sorted(filter(None, remote_names_raw.splitlines())):
        safe_name = sanitize_untrusted(raw_name, limit=120)
        url = _try_git_text(root, ["remote", "get-url", raw_name])
        remotes.append(
            {
                "name": safe_name,
                "url": sanitize_remote_url(url) if url else None,
            }
        )

    index_after = _optional_stat(index_path)
    object_store_stable = all(_optional_stat(path) == snapshot for path, snapshot in object_store_markers)
    source_stable = index_before == index_after and object_store_stable
    observation_complete = bool(
        common_dir is not None
        and worktree_payload is not None
        and status.get("readable") is True
        and source_stable
    )
    return {
        "is_git_repository": True,
        "observation_status": "complete" if observation_complete else "partial",
        "git_root": str(root),
        "common_dir": str(common_dir) if common_dir is not None else None,
        "object_directories": [str(path) for path in object_directories],
        "object_store_observation_status": "complete",
        "head": head,
        "branch": sanitize_untrusted(branch, limit=200) if branch else None,
        "status": status,
        "worktrees": worktrees,
        "remotes": remotes,
        "source_stable_during_read": source_stable,
    }


def family_roots(project: Path, git_facts: dict[str, object]) -> list[Path]:
    roots: list[Path] = []
    for item in git_facts.get("worktrees", []):
        if (
            not isinstance(item, dict)
            or not item.get("path")
            or item.get("bare")
            or not item.get("trusted_family_member")
        ):
            continue
        candidate = Path(str(item["path"])).resolve(strict=False)
        if candidate.is_dir() and candidate not in roots:
            roots.append(candidate)
    git_root = git_facts.get("git_root")
    fallback = Path(str(git_root)).resolve(strict=False) if git_root else project.resolve(strict=True)
    if not any(path_is_within(project.resolve(strict=True), root) for root in roots) and fallback not in roots:
        roots.append(fallback)
    return roots
